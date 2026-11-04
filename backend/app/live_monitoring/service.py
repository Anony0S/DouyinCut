from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from typing import Literal

from pydantic import Field

from ..core.config import Settings
from ..core.domain import Model, new_id
from ..core.storage import Store

SEGMENT_SECONDS = 1200
POLL_SECONDS = 20
PRODUCT_CLIP_JOB = "live_product_clips"
ACTIVE_JOB_STATES = ("queued", "running", "pause_requested", "paused", "cancel_requested")


class LiveMonitorRequest(Model):
    url: str = Field(min_length=8, max_length=2000)
    name: str = Field(default="", max_length=200)


class LiveMonitorSettings(Model):
    auto_record: bool | None = None
    notify_live: bool | None = None


class LiveRecordingAction(Model):
    action: Literal["start", "stop"]


class LiveClipOpen(Model):
    reveal: bool = False


class LivePeriodSplit(Model):
    confirmed_missing_segment_ids: list[str] = Field(default_factory=list, max_length=1000)


def normalize_douyin_url(raw: str) -> str:
    match = re.search(r"https?://[^\s，。；;]+", raw.strip())
    value = (match.group(0) if match else raw.strip()).rstrip(".,，。;；)")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "douyin.com" or host.endswith(".douyin.com")
    ):
        raise ValueError("请输入抖音直播间链接或抖音分享短链")
    if host == "www.douyin.com" and parsed.path.startswith(("/user/", "/share/user/")):
        raise ValueError("暂不支持主播主页链接，请使用直播间链接")
    return value


class LiveStore:
    def __init__(self, store):
        self.store = store
        with store.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS live_monitors (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, name TEXT NOT NULL,
                    live_state TEXT NOT NULL DEFAULT 'unknown',
                    last_checked REAL, error TEXT, retry_blocked INTEGER NOT NULL DEFAULT 0,
                    current_session_id TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL
                );
                -- record_requested: manual start for the current broadcast;
                -- record_held: user stopped recording, suppress until the room goes offline.
                CREATE TABLE IF NOT EXISTS live_monitor_controls (
                    monitor_id TEXT PRIMARY KEY, auto_record INTEGER NOT NULL DEFAULT 1,
                    notify_live INTEGER NOT NULL DEFAULT 1, record_requested INTEGER NOT NULL DEFAULT 0,
                    record_held INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
                    FOREIGN KEY(monitor_id) REFERENCES live_monitors(id)
                );
                CREATE TABLE IF NOT EXISTS live_sessions (
                    id TEXT PRIMARY KEY, monitor_id TEXT NOT NULL, status TEXT NOT NULL,
                    started REAL NOT NULL, ended REAL, ffmpeg_pid INTEGER,
                    end_time_source TEXT CHECK(end_time_source IN ('observed', 'recovery_check')),
                    FOREIGN KEY(monitor_id) REFERENCES live_monitors(id)
                );
                CREATE TABLE IF NOT EXISTS live_segments (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                    segment_index INTEGER NOT NULL, status TEXT NOT NULL,
                    started REAL NOT NULL, ended REAL, size_bytes INTEGER NOT NULL DEFAULT 0,
                    duration_us INTEGER, asset_id TEXT, import_job_id TEXT,
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_product_events (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
                    promotion_id TEXT NOT NULL, product_name TEXT NOT NULL DEFAULT '',
                    explain_type INTEGER, event_time REAL NOT NULL, received_at REAL NOT NULL,
                    source TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_product_periods (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, promotion_id TEXT NOT NULL,
                    product_name TEXT NOT NULL DEFAULT '', started REAL NOT NULL, ended REAL,
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_segment_product_clips (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL, period_id TEXT NOT NULL,
                    promotion_id TEXT NOT NULL, product_name TEXT NOT NULL DEFAULT '',
                    start REAL NOT NULL, end REAL NOT NULL, path TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0,
                    duration_us INTEGER, asset_id TEXT, import_job_id TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(segment_id, period_id),
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id),
                    FOREIGN KEY(period_id) REFERENCES live_product_periods(id)
                );
                CREATE TABLE IF NOT EXISTS live_monitor_liveness (
                    monitor_id TEXT PRIMARY KEY, is_live INTEGER, checked REAL NOT NULL,
                    FOREIGN KEY(monitor_id) REFERENCES live_monitors(id)
                );
                CREATE TABLE IF NOT EXISTS live_session_current_product (
                    session_id TEXT PRIMARY KEY, promotion_id TEXT NOT NULL,
                    product_name TEXT NOT NULL DEFAULT '', event_time REAL NOT NULL,
                    received_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_session_products (
                    session_id TEXT NOT NULL, promotion_id TEXT NOT NULL,
                    product_name TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(session_id,promotion_id),
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_session_product_catalog_state (
                    session_id TEXT PRIMARY KEY, error_detail TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_product_listener_state (
                    session_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES live_sessions(id)
                );
                CREATE INDEX IF NOT EXISTS live_sessions_monitor ON live_sessions(monitor_id,started DESC);
                CREATE INDEX IF NOT EXISTS live_segments_session ON live_segments(session_id,segment_index);
                CREATE INDEX IF NOT EXISTS live_product_events_session ON live_product_events(session_id,event_time);
            """)

    def create_monitor(self, url: str, name: str):
        url = normalize_douyin_url(url)
        now, monitor_id = time.time(), new_id("live")
        with self.store.connection() as conn:
            duplicate = conn.execute("SELECT id FROM live_monitors WHERE url=?", (url,)).fetchone()
            if duplicate:
                raise ValueError("该直播链接已经在监控中")
            conn.execute("INSERT INTO live_monitors(id,url,name,created,updated) VALUES(?,?,?,?,?)",
                         (monitor_id, url, name.strip(), now, now))
            conn.execute("INSERT INTO live_monitor_controls(monitor_id,updated) VALUES(?,?)", (monitor_id, now))
        return self.monitor(monitor_id)

    def set_auto_name(self, monitor_id: str, name: str):
        name = name.strip()[:200]
        if not name:
            return
        with self.store.connection() as conn:
            conn.execute("UPDATE live_monitors SET name=?,updated=? WHERE id=? AND name=''",
                         (name, time.time(), monitor_id))

    _MONITOR_COLUMNS = (
        "m.id,m.url,m.name,m.live_state,m.last_checked,m.error,m.retry_blocked,m.current_session_id,"
        "m.created,m.updated,COALESCE(c.auto_record,1) AS auto_record,COALESCE(c.notify_live,1) AS notify_live,"
        "COALESCE(c.record_requested,0) AS record_requested,COALESCE(c.record_held,0) AS record_held,"
        "COALESCE(c.updated,0) AS updated_controls,l.is_live,l.checked AS live_checked"
    )
    _MONITOR_JOINS = ("FROM live_monitors m LEFT JOIN live_monitor_controls c ON c.monitor_id=m.id "
                      "LEFT JOIN live_monitor_liveness l ON l.monitor_id=m.id")

    @staticmethod
    def _decode_monitor(row) -> dict:
        result = dict(row)
        for field in ("auto_record", "notify_live", "record_requested", "record_held", "retry_blocked"):
            result[field] = bool(result[field])
        result["is_live"] = None if result["is_live"] is None else bool(result["is_live"])
        return result

    def monitor(self, monitor_id: str):
        with self.store.connection() as conn:
            row = conn.execute(f"SELECT {self._MONITOR_COLUMNS} {self._MONITOR_JOINS} WHERE m.id=?",
                               (monitor_id,)).fetchone()
        if row is None:
            raise KeyError(monitor_id)
        return self._decode_monitor(row)

    def all_monitors(self):
        with self.store.connection() as conn:
            return [self._decode_monitor(row) for row in conn.execute(
                f"SELECT {self._MONITOR_COLUMNS} {self._MONITOR_JOINS} ORDER BY m.created").fetchall()]

    def list_monitors(self, *, window_start: float | None = None, window_end: float | None = None,
                      include_end_recording: bool = False):
        if window_end is None:
            include_end_recording = True
        window_end = time.time() if window_end is None else window_end
        window_start = window_end - 86400 if window_start is None else window_start
        if window_start >= window_end:
            raise ValueError("录制时间窗口的开始必须早于结束")
        with self.store.connection() as conn:
            monitors = [self._decode_monitor(row) for row in conn.execute(
                f"SELECT {self._MONITOR_COLUMNS} {self._MONITOR_JOINS} "
                "ORDER BY m.updated DESC,m.created DESC").fetchall()]
            for monitor in monitors:
                sessions = conn.execute(
                    "SELECT * FROM live_sessions WHERE monitor_id=? AND "
                    "((started<? AND ended>?) OR (status='recording' AND "
                    "(started<? OR (? AND started=?)))) "
                    "ORDER BY started DESC,id",
                    (monitor["id"], window_end, window_start, window_end,
                     include_end_recording, window_end)).fetchall()
                monitor["sessions"] = []
                for session in sessions:
                    monitor["sessions"].append(self._session_detail(conn, session))
        return monitors

    def recording_session(self, monitor_id: str, session_id: str):
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM live_sessions WHERE monitor_id=? AND id=?",
                               (monitor_id, session_id)).fetchone()
            return self._session_detail(conn, row) if row is not None else None

    @staticmethod
    def _session_detail(conn, session):
        item = dict(session)
        clip_jobs = [dict(job) for job in conn.execute(
            "SELECT id,state,error,payload FROM jobs "
            "WHERE kind=? AND json_extract(payload,'$.session_id')=? ORDER BY created DESC",
            (PRODUCT_CLIP_JOB, session["id"]),
        ).fetchall()]
        locked_segments = set()
        period_jobs = {}
        for job in clip_jobs:
            payload = json.loads(job.pop("payload"))
            period_jobs.setdefault(payload["period_id"], job)
            if job["state"] in ACTIVE_JOB_STATES:
                locked_segments.update(window["segment_id"] for window in payload["windows"])
        all_segments = [dict(row) for row in conn.execute(
            "SELECT * FROM live_segments WHERE session_id=? ORDER BY segment_index", (session["id"],))]
        item["segments"] = [{**segment, "splitting": segment["id"] in locked_segments}
                            for segment in reversed(all_segments) if segment["status"] != "deleted"]
        item["product_events"] = [dict(event) for event in conn.execute(
            "SELECT id,promotion_id,product_name,explain_type,event_time,received_at,source "
            "FROM live_product_events WHERE session_id=? ORDER BY event_time,event_key",
            (session["id"],)).fetchall()]
        item["product_clips"] = [dict(clip) for clip in conn.execute(
            "SELECT * FROM live_segment_product_clips WHERE session_id=? "
            "ORDER BY segment_index DESC,start DESC",
            (session["id"],)).fetchall()]
        item["product_periods"] = LiveStore._period_details(
            conn, session, all_segments, item["product_clips"], period_jobs)
        current_product = conn.execute(
            "SELECT promotion_id,product_name,event_time,received_at "
            "FROM live_session_current_product WHERE session_id=?",
            (session["id"],),
        ).fetchone()
        item["current_product"] = dict(current_product) if current_product else None
        listener = conn.execute("SELECT status,detail,updated FROM live_product_listener_state WHERE session_id=?",
                                (session["id"],)).fetchone()
        item["product_listener"] = dict(listener) if listener else None
        catalog_state = conn.execute(
            "SELECT error_detail,updated FROM live_session_product_catalog_state WHERE session_id=?",
            (session["id"],),
        ).fetchone()
        item["product_catalog_error"] = (
            {"detail": json.loads(catalog_state["error_detail"]),
             "updated": catalog_state["updated"]}
            if catalog_state else None
        )
        return item

    def record_product_event(self, session_id: str, *, event_key: str, promotion_id: str,
                             product_name: str = "", explain_type: int | None = None,
                             event_time: float | None = None, received_at: float | None = None,
                             source: str = "websocket"):
        event_key, promotion_id = event_key.strip(), str(promotion_id).strip()
        if not event_key or not promotion_id:
            raise ValueError("商品变更事件缺少事件标识或商品 ID")
        event_key = f"{session_id}:{event_key}"
        received_at = received_at or time.time()
        event_time = event_time or received_at
        product_name = product_name.strip()[:500]
        with self.store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO live_product_events "
                         "(id,session_id,event_key,promotion_id,product_name,explain_type,event_time,received_at,source) "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         (new_id("product_event"), session_id, event_key, promotion_id, product_name,
                          explain_type, event_time, received_at, source))
            row = conn.execute("SELECT id FROM live_product_events WHERE event_key=?", (event_key,)).fetchone()
        return row["id"]

    def update_product_name(self, session_id: str, promotion_id: str, product_name: str):
        product_name = product_name.strip()[:500]
        if not product_name:
            return
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE live_product_events SET product_name=? WHERE session_id=? "
                "AND promotion_id=? AND product_name=''",
                (product_name, session_id, str(promotion_id)),
            )
            conn.execute(
                "UPDATE live_session_current_product SET product_name=? "
                "WHERE session_id=? AND promotion_id=? AND product_name=''",
                (product_name, session_id, str(promotion_id)),
            )
            conn.execute(
                "UPDATE live_product_periods SET product_name=? "
                "WHERE session_id=? AND promotion_id=? AND product_name=''",
                (product_name, session_id, str(promotion_id)),
            )

    def product_name(self, session_id: str, promotion_id: str) -> str:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT product_name FROM live_session_products WHERE session_id=? AND promotion_id=?",
                (session_id, str(promotion_id)),
            ).fetchone()
        return row["product_name"] if row else ""

    def replace_session_products(self, session_id: str, products: dict[str, str]):
        rows = [
            (session_id, str(promotion_id).strip(), str(name).strip()[:500])
            for promotion_id, name in products.items()
            if str(promotion_id).strip() and str(name).strip()
        ]
        now = time.time()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM live_session_products WHERE session_id=?", (session_id,))
            conn.executemany(
                "INSERT INTO live_session_products(session_id,promotion_id,product_name,updated_at) "
                "VALUES(?,?,?,?)",
                [(sid, promotion_id, name, now) for sid, promotion_id, name in rows],
            )
        return len(rows)

    def set_product_catalog_error(self, session_id: str, detail: dict | None):
        with self.store.connection() as conn:
            if detail is None:
                conn.execute("DELETE FROM live_session_product_catalog_state WHERE session_id=?", (session_id,))
                return
            conn.execute(
                "INSERT INTO live_session_product_catalog_state(session_id,error_detail,updated) "
                "VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "error_detail=excluded.error_detail,updated=excluded.updated",
                (session_id, json.dumps(detail, ensure_ascii=False)[:10000], time.time()),
            )

    def set_current_product(self, session_id: str, *, promotion_id: str,
                            event_time: float, received_at: float, product_name: str = ""):
        promotion_id = str(promotion_id).strip()
        if not promotion_id:
            raise ValueError("当前商品缺少商品 ID")
        product_name = product_name.strip()[:500]
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT promotion_id,received_at FROM live_session_current_product WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if current and received_at < current["received_at"]:
                return
            changed = current is None or current["promotion_id"] != promotion_id
            if changed:
                session = conn.execute("SELECT started FROM live_sessions WHERE id=?", (session_id,)).fetchone()
                if session is None:
                    raise KeyError(session_id)
                start = float(session["started"]) if current is None else received_at
                if current:
                    conn.execute(
                        "UPDATE live_product_periods SET ended=? WHERE session_id=? AND ended IS NULL",
                        (received_at, session_id),
                    )
                conn.execute(
                    "INSERT INTO live_product_periods(id,session_id,promotion_id,product_name,started) "
                    "VALUES(?,?,?,?,?)",
                    (new_id("product_period"), session_id, promotion_id, product_name, start),
                )
            elif product_name:
                conn.execute(
                    "UPDATE live_product_periods SET product_name=? WHERE session_id=? AND ended IS NULL "
                    "AND promotion_id=? AND product_name=''",
                    (product_name, session_id, promotion_id),
                )
            conn.execute(
                "INSERT INTO live_session_current_product "
                "(session_id,promotion_id,product_name,event_time,received_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "promotion_id=excluded.promotion_id,product_name=excluded.product_name, "
                "event_time=excluded.event_time,received_at=excluded.received_at "
                "WHERE excluded.received_at>=live_session_current_product.received_at",
                (session_id, promotion_id, product_name, event_time, received_at),
            )

    def set_product_listener_state(self, session_id: str, status: str, detail: str = ""):
        with self.store.connection() as conn:
            conn.execute("INSERT INTO live_product_listener_state(session_id,status,detail,updated) VALUES(?,?,?,?) "
                         "ON CONFLICT(session_id) DO UPDATE SET status=excluded.status,detail=excluded.detail,updated=excluded.updated",
                         (session_id, status, detail[:1000], time.time()))

    def set_monitor_state(self, monitor_id: str, state: str, error: str | None = None,
                          retry_blocked: bool | None = None):
        now = time.time()
        with self.store.connection() as conn:
            if retry_blocked is None:
                conn.execute("UPDATE live_monitors SET live_state=?,last_checked=?,error=?,updated=? WHERE id=?",
                             (state, now, error[:1000] if error else None, now, monitor_id))
            else:
                conn.execute("UPDATE live_monitors SET live_state=?,last_checked=?,error=?,retry_blocked=?,updated=? WHERE id=?",
                             (state, now, error[:1000] if error else None, int(retry_blocked), now, monitor_id))

    def _update_controls(self, conn, monitor_id: str, **fields):
        if conn.execute("SELECT 1 FROM live_monitors WHERE id=?", (monitor_id,)).fetchone() is None:
            raise KeyError(monitor_id)
        now = time.time()
        conn.execute("INSERT OR IGNORE INTO live_monitor_controls(monitor_id,updated) VALUES(?,?)", (monitor_id, now))
        assignments = ",".join(f"{name}=?" for name in fields)
        conn.execute(f"UPDATE live_monitor_controls SET {assignments},updated=? WHERE monitor_id=?",
                     (*(int(value) for value in fields.values()), now, monitor_id))

    def update_settings(self, monitor_id: str, settings: LiveMonitorSettings):
        fields = {name: value for name, value in settings.model_dump().items() if value is not None}
        if fields:
            with self.store.connection() as conn:
                self._update_controls(conn, monitor_id, **fields)
        return self.monitor(monitor_id)

    def request_recording(self, monitor_id: str, action: str):
        """Record a start / stop request; the monitor process applies it within about a second."""
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if action == "start":
                live = conn.execute("SELECT is_live FROM live_monitor_liveness WHERE monitor_id=?",
                                    (monitor_id,)).fetchone()
                if not live or not live["is_live"]:
                    raise ValueError("直播间当前未开播，开播后才能开始录制")
                self._update_controls(conn, monitor_id, record_requested=True, record_held=False)
                # An explicit start also lifts the crash guard for this broadcast.
                conn.execute("UPDATE live_monitors SET retry_blocked=0,error=NULL WHERE id=?", (monitor_id,))
            else:
                self._update_controls(conn, monitor_id, record_requested=False, record_held=True)
        return self.monitor(monitor_id)

    def clear_broadcast_requests(self, monitor_id: str):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_monitor_controls SET record_requested=0,record_held=0 WHERE monitor_id=?",
                         (monitor_id,))

    def set_liveness(self, monitor_id: str, is_live: bool | None):
        with self.store.connection() as conn:
            conn.execute("INSERT INTO live_monitor_liveness(monitor_id,is_live,checked) VALUES(?,?,?) "
                         "ON CONFLICT(monitor_id) DO UPDATE SET is_live=excluded.is_live,checked=excluded.checked",
                         (monitor_id, None if is_live is None else int(is_live), time.time()))

    def delete_monitor(self, monitor_id: str):
        with self.store.connection() as conn:
            row = conn.execute("SELECT id FROM live_monitors WHERE id=?", (monitor_id,)).fetchone()
            if row is None:
                raise KeyError(monitor_id)
            active = conn.execute("SELECT 1 FROM live_sessions WHERE monitor_id=? AND status='recording' LIMIT 1",
                                  (monitor_id,)).fetchone()
            if active:
                raise ValueError("该直播间正在录制，请先停止录制并等待文件封存")
            conn.execute("DELETE FROM live_segments WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_segment_product_clips WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_product_periods WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_product_events WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_session_current_product WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_session_products WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_session_product_catalog_state WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_product_listener_state WHERE session_id IN (SELECT id FROM live_sessions WHERE monitor_id=?)",
                         (monitor_id,))
            conn.execute("DELETE FROM live_sessions WHERE monitor_id=?", (monitor_id,))
            conn.execute("DELETE FROM live_monitor_liveness WHERE monitor_id=?", (monitor_id,))
            conn.execute("DELETE FROM live_monitor_controls WHERE monitor_id=?", (monitor_id,))
            conn.execute("DELETE FROM live_monitors WHERE id=?", (monitor_id,))
        return {"deleted_monitor_id": monitor_id, "recordings_retained": True}

    def start_session(self, monitor_id: str, directory: Path):
        now, session_id = time.time(), new_id("session")
        directory.mkdir(parents=True, exist_ok=True)
        with self.store.connection() as conn:
            current = conn.execute("SELECT current_session_id FROM live_monitors WHERE id=?",
                                   (monitor_id,)).fetchone()
            if current is None:
                raise KeyError(monitor_id)
            if current[0]:
                active = conn.execute("SELECT id FROM live_sessions WHERE id=? AND status='recording'",
                                      (current[0],)).fetchone()
                if active:
                    return current[0]
            conn.execute("INSERT INTO live_sessions(id,monitor_id,status,started) VALUES(?,?,'recording',?)",
                         (session_id, monitor_id, now))
            conn.execute("UPDATE live_monitors SET current_session_id=?,live_state='recording',updated=? WHERE id=?",
                         (session_id, now, monitor_id))
        return session_id

    def set_session_pid(self, session_id: str, pid: int):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_sessions SET ffmpeg_pid=? WHERE id=?", (pid, session_id))

    def upsert_segment(self, session_id: str, path: Path, index: int, status: str,
                       size_bytes: int, duration_us: int | None = None):
        path_text = str(path.resolve())
        now = time.time()
        with self.store.connection() as conn:
            row = conn.execute("SELECT id,status FROM live_segments WHERE path=?", (path_text,)).fetchone()
            if row:
                if row["status"] == "deleted":
                    return row["id"]
                final_status = row["status"] if row["status"] in {"importing", "imported"} else status
                conn.execute("UPDATE live_segments SET status=?,size_bytes=?,duration_us=COALESCE(?,duration_us),"
                             "ended=CASE WHEN ?='recording' THEN NULL ELSE COALESCE(ended,?) END WHERE id=?",
                             (final_status, size_bytes, duration_us, final_status, now, row["id"]))
                return row["id"]
            segment_id = new_id("segment")
            conn.execute("INSERT INTO live_segments(id,session_id,path,segment_index,status,started,ended,size_bytes,duration_us) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (segment_id, session_id, path_text, index, status,
                         now, now if status != "recording" else None, size_bytes, duration_us))
            return segment_id

    def segment_status(self, path: Path):
        with self.store.connection() as conn:
            row = conn.execute("SELECT status,duration_us FROM live_segments WHERE path=?",
                               (str(path.resolve()),)).fetchone()
        return dict(row) if row else None

    def stop_session(self, monitor_id: str, session_id: str, status: str = "stopped"):
        now = time.time()
        with self.store.connection() as conn:
            conn.execute("UPDATE live_sessions SET status=?,ended=?,end_time_source='observed' "
                         "WHERE id=? AND status='recording'",
                         (status, now, session_id))
            conn.execute("UPDATE live_monitors SET current_session_id=NULL,live_state=?,updated=? WHERE id=?",
                         ("offline" if status == "stopped" else "error", now, monitor_id))
            conn.execute("UPDATE live_product_periods SET ended=MAX(?,started) "
                         "WHERE session_id=? AND ended IS NULL",
                         (now, session_id))

    @staticmethod
    def _period_details(conn, session, segments, clips, period_jobs):
        """Use one media timeline for both the lecture list and its split requests.

        Deleted segments retain their duration, so later offsets never close the gap.
        A missing/unknown duration is an error, never a guessed 20-minute boundary.
        """
        timeline, offset_us, mapping_error = [], 0, ""
        unmapped_start = None
        now = time.time()
        for expected_index, segment in enumerate(segments, 1):
            if segment["segment_index"] != expected_index:
                mapping_error = "录制分段时间范围不完整，无法准确定位商品讲解"
                unmapped_start = float(session["started"]) + offset_us / 1_000_000
                break
            start = float(session["started"]) + offset_us / 1_000_000
            length = segment["duration_us"]
            if length is None:
                if segment["status"] != "recording":
                    mapping_error = "录制分段时长未知，无法准确定位商品讲解"
                    unmapped_start = start
                    break
                end = max(start, now)
            else:
                end = start + length / 1_000_000
                offset_us += length
            state = ("deleted" if segment["status"] == "deleted" else
                     "missing" if not Path(segment["path"]).is_file() else
                     "recording" if segment["status"] == "recording" else
                     "ready" if segment["status"] in {"completed", "importing", "imported"} else "failed")
            timeline.append((segment, start, end, state))
        by_period_segment = {(clip["period_id"], clip["segment_id"]): clip for clip in clips}
        periods = []
        for row in conn.execute("SELECT * FROM live_product_periods WHERE session_id=? ORDER BY started DESC,id DESC",
                                (session["id"],)):
            period = dict(row)
            end = float(period["ended"] if period["ended"] is not None else now)
            sources = []
            for segment, segment_start, segment_end, state in timeline:
                start, stop = max(float(period["started"]), segment_start), min(end, segment_end)
                if stop <= start:
                    continue
                clip = by_period_segment.get((period["id"], segment["id"]))
                sources.append({"segment_id": segment["id"], "segment_index": segment["segment_index"],
                                "state": state, "start": start, "end": stop,
                                "clip_start": start - segment_start, "clip_end": stop - segment_start,
                                "clip_status": clip["status"] if clip else None,
                                "clip_id": clip["id"] if clip else None})
            period.update(sources=sources, mapping_error=mapping_error if unmapped_start is not None and end > unmapped_start else "",
                          clip_job=period_jobs.get(period["id"]))
            periods.append(period)
        return periods

    def period_split_plan(self, period_id: str):
        with self.store.connection() as conn:
            return self._period_split_plan(conn, period_id)

    def _period_split_plan(self, conn, period_id):
        row = conn.execute("SELECT session_id FROM live_product_periods WHERE id=?", (period_id,)).fetchone()
        if row is None:
            raise KeyError(period_id)
        session = conn.execute("SELECT * FROM live_sessions WHERE id=?", (row["session_id"],)).fetchone()
        detail = self._session_detail(conn, session)
        return next(period for period in detail["product_periods"] if period["id"] == period_id)

    def request_period_product_clips(self, period_id: str, confirmed_missing_segment_ids: list[str]):
        """Freeze only this lecture's sealed intersections, after checking missing-source consent."""
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            period = self._period_split_plan(conn, period_id)
            active = period["clip_job"]
            if active and active["state"] in ACTIVE_JOB_STATES:
                return self.store.job(active["id"])
            if period["mapping_error"]:
                raise ValueError(period["mapping_error"])
            sources = period["sources"]
            missing = [source for source in sources if source["state"] in {"deleted", "missing", "failed"}]
            available = [source for source in sources if source["state"] == "ready"
                         and source["end"] - source["start"] >= 1]
            pending = any(source["state"] == "recording" for source in sources)
            if not available and missing and not pending:
                raise ValueError("没有对应的可用录制分段，无法切分商品（源分段已删除、文件不存在或文件异常）")
            if missing and {source["segment_id"] for source in missing} != set(confirmed_missing_segment_ids):
                return {"state": "confirmation_required", "missing_segments": missing,
                        "available_count": len(available)}
            windows = [source for source in available
                       if source["clip_status"] not in {"completed", "importing", "imported"}]
            if not windows:
                return {"state": "noop", "message": "已封存部分均已生成，其他部分等待封存后可补切" if pending and available
                        else "等待对应录制分段封存后再切分" if pending
                        else "该次讲解的可用片段均已生成，无需重复切分" if available
                        else "没有至少 1 秒的可切分讲解区间"}
            target_segments = {source["segment_id"] for source in windows}
            placeholders = ",".join("?" * len(ACTIVE_JOB_STATES))
            for row in conn.execute(f"SELECT payload FROM jobs WHERE kind=? AND state IN ({placeholders})",
                                    (PRODUCT_CLIP_JOB, *ACTIVE_JOB_STATES)):
                if any(window["segment_id"] in target_segments for window in json.loads(row["payload"])["windows"]):
                    raise ValueError("对应分段正在切分其他讲解，请等待该任务完成后重试")
            job_id, now = new_id("job"), time.time()
            payload = {"period_id": period_id, "session_id": period["session_id"], "windows": windows}
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                         (job_id, PRODUCT_CLIP_JOB, json.dumps(payload, ensure_ascii=False), now, now))
        return self.store.job(job_id)

    def begin_segment_product_clip(self, segment: dict, window: dict, path: Path):
        """Return the clip id to (re)generate, or None when a usable clip already exists."""
        path_text = str(path.resolve())
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id,status FROM live_segment_product_clips WHERE segment_id=? AND period_id=?",
                               (segment["id"], window["id"])).fetchone()
            if row:
                if row["status"] in {"completed", "importing", "imported"}:
                    return None
                conn.execute("UPDATE live_segment_product_clips SET status='processing',error='',product_name=?,"
                             "start=?,end=?,path=? WHERE id=?",
                             (window["product_name"], window["abs_start"], window["abs_end"], path_text, row["id"]))
                return row["id"]
            clip_id = new_id("product_clip")
            conn.execute(
                "INSERT INTO live_segment_product_clips(id,session_id,segment_id,segment_index,period_id,promotion_id,"
                "product_name,start,end,path,status) VALUES(?,?,?,?,?,?,?,?,?,?,'processing')",
                (clip_id, segment["session_id"], segment["id"], segment["segment_index"], window["id"],
                 window["promotion_id"], window["product_name"], window["abs_start"], window["abs_end"], path_text),
            )
            return clip_id

    def finish_segment_product_clip(self, clip_id: str, path: Path, duration_us: int):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segment_product_clips SET status='completed',size_bytes=?,duration_us=?,error='' "
                         "WHERE id=?", (path.stat().st_size, duration_us, clip_id))

    def fail_segment_product_clip(self, clip_id: str, error: str):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segment_product_clips SET status='failed',error=? "
                         "WHERE id=? AND status='processing'", (error[:1000], clip_id))

    def import_product_clip(self, clip_id: str):
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM live_segment_product_clips WHERE id=?", (clip_id,)).fetchone()
            if row is None:
                raise KeyError(clip_id)
            clip = dict(row)
            if clip["status"] == "imported":
                return {"asset_id": clip["asset_id"], "state": "imported"}
            if clip["status"] not in {"completed", "importing"}:
                raise ValueError("只有已生成的商品视频片段可以导入")
            if not Path(clip["path"]).is_file():
                raise ValueError("商品视频片段文件已不存在")
            if clip["status"] == "importing":
                return self.store.job(clip["import_job_id"])
            job_id, now = new_id("job"), time.time()
            payload = {"path": clip["path"], "display_name": Path(clip["path"]).name,
                       "live_product_clip_id": clip_id, "product_name": clip["product_name"]}
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                         (job_id, "import", json.dumps(payload, ensure_ascii=False), now, now))
            conn.execute("UPDATE live_segment_product_clips SET status='importing',import_job_id=? WHERE id=?",
                         (job_id, clip_id))
        return self.store.job(job_id)

    def finish_product_clip_import(self, clip_id: str, asset_id: str):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segment_product_clips SET status='imported',asset_id=? WHERE id=?",
                         (asset_id, clip_id))

    def retry_product_clip_import(self, clip_id: str):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segment_product_clips SET status='completed',import_job_id=NULL "
                         "WHERE id=? AND status='importing'", (clip_id,))

    def import_segment(self, segment_id: str):
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM live_segments WHERE id=?", (segment_id,)).fetchone()
            if row is None:
                raise KeyError(segment_id)
            segment = dict(row)
            if segment["status"] == "imported":
                return {"asset_id": segment["asset_id"], "state": "imported"}
            if segment["status"] not in {"completed", "importing"}:
                raise ValueError("只有已封存的分段可以进入剪辑")
            if not Path(segment["path"]).is_file():
                raise ValueError("录制分段文件已不存在")
            if segment["status"] == "importing":
                job_id = segment["import_job_id"]
                return self.store.job(job_id)
            job_id, now = new_id("job"), time.time()
            payload = {"path": segment["path"], "display_name": Path(segment["path"]).name,
                       "live_segment_id": segment_id}
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                         (job_id, "import", json.dumps(payload, ensure_ascii=False), now, now))
            conn.execute("UPDATE live_segments SET status='importing',import_job_id=? WHERE id=?",
                         (job_id, segment_id))
        return self.store.job(job_id)

    def open_product_clip(self, clip_id: str, recordings_root: Path, reveal: bool = False):
        """Open a generated clip in the system player, or select it in Explorer."""
        with self.store.connection() as conn:
            row = conn.execute("SELECT path FROM live_segment_product_clips WHERE id=?", (clip_id,)).fetchone()
        if row is None:
            raise KeyError(clip_id)
        path = Path(row["path"]).resolve()
        if not path.is_relative_to(recordings_root.resolve()):
            raise ValueError("商品片段不在直播录制目录中，拒绝打开")
        if not path.is_file():
            raise ValueError("商品片段文件已不存在")
        try:
            if os.name == "nt":
                if reveal:
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])
        except OSError as exc:
            raise ValueError("没有可打开该文件的程序，请改用“所在文件夹”") from exc
        return {"opened": True, "revealed": reveal, "path": str(path)}

    def open_segment(self, segment_id: str, recordings_root: Path, reveal: bool = False):
        """Open a recording segment in the system player, or select it in Explorer."""
        with self.store.connection() as conn:
            row = conn.execute("SELECT path FROM live_segments WHERE id=? AND status!='deleted'", (segment_id,)).fetchone()
        if row is None:
            raise KeyError(segment_id)
        path = Path(row["path"]).resolve()
        if not path.is_relative_to(recordings_root.resolve()):
            raise ValueError("分段文件不在直播录制目录中，拒绝打开")
        if not path.is_file():
            raise ValueError("录制分段文件已不存在")
        try:
            if os.name == "nt":
                if reveal:
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])
        except OSError as exc:
            raise ValueError("没有可打开该文件的程序，请改用“所在文件夹”") from exc
        return {"opened": True, "revealed": reveal, "path": str(path)}

    def delete_product_clip(self, clip_id: str, recordings_root: Path):
        recordings_root = recordings_root.resolve()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM live_segment_product_clips WHERE id=?", (clip_id,)).fetchone()
            if row is None:
                raise KeyError(clip_id)
            clip = dict(row)
            if clip["status"] in {"processing", "importing"}:
                raise ValueError("生成中或导入中的商品片段不能删除")
            path = Path(clip["path"]).resolve()
            if not path.is_relative_to(recordings_root):
                raise ValueError("商品片段不在直播录制目录中，拒绝删除")
            file_deleted = False
            if clip["status"] != "imported" and path.exists():
                if not path.is_file():
                    raise ValueError("商品片段路径不是普通文件，拒绝删除")
                path.unlink()
                file_deleted = True
            conn.execute("DELETE FROM live_segment_product_clips WHERE id=?", (clip_id,))
        return {"deleted_clip_id": clip_id, "file_deleted": file_deleted,
                "project_file_retained": clip["status"] == "imported"}

    def delete_segment(self, segment_id: str, recordings_root: Path):
        recordings_root = recordings_root.resolve()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM live_segments WHERE id=?", (segment_id,)).fetchone()
            if row is None:
                raise KeyError(segment_id)
            segment = dict(row)
            if segment["status"] == "deleted":
                return {"deleted_segment_id": segment_id, "file_deleted": False}
            if segment["status"] in {"recording", "importing"}:
                raise ValueError("录制中或导入中的分段不能删除")
            placeholders = ",".join("?" * len(ACTIVE_JOB_STATES))
            if conn.execute(
                f"SELECT 1 FROM jobs,json_each(jobs.payload,'$.windows') AS window WHERE kind=? "
                f"AND json_extract(window.value,'$.segment_id')=? "
                f"AND state IN ({placeholders}) LIMIT 1",
                (PRODUCT_CLIP_JOB, segment_id, *ACTIVE_JOB_STATES),
            ).fetchone():
                raise ValueError("该分段正在切分商品片段，请等待任务完成或取消后再删除")
            path = Path(segment["path"]).resolve()
            if not path.is_relative_to(recordings_root):
                raise ValueError("分段文件不在直播录制目录中，拒绝删除")
            referenced = conn.execute(
                "SELECT 1 FROM objects WHERE kind='asset' AND json_extract(body,'$.path')=? LIMIT 1",
                (str(path),),
            ).fetchone()
            if referenced:
                raise ValueError("该分段已被视频项目引用，请先删除关联视频项目")
            file_deleted = False
            if path.exists():
                if not path.is_file():
                    raise ValueError("分段路径不是普通文件，拒绝删除")
                path.unlink()
                file_deleted = True
            converted = path.with_name(f"{path.stem}_剪辑源.mp4")
            if converted.is_file() and converted.parent == path.parent:
                converted.unlink()
            conn.execute("UPDATE live_segments SET status='deleted',size_bytes=0 WHERE id=?", (segment_id,))
        return {"deleted_segment_id": segment_id, "file_deleted": file_deleted}

    def finish_import(self, segment_id: str, asset_id: str):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segments SET status='imported',asset_id=? WHERE id=?",
                         (asset_id, segment_id))

    def retry_import(self, segment_id: str):
        with self.store.connection() as conn:
            conn.execute("UPDATE live_segments SET status='completed',import_job_id=NULL WHERE id=? AND status='importing'",
                         (segment_id,))


def generate_period_product_clips(store, settings: Settings, payload: dict,
                                 checkpoint=None, progress=None) -> dict:
    """Generate the frozen sealed pieces of exactly one lecture, one clip per source."""
    from ..media.processing import convert_to_mp4, probe, run_process

    live = LiveStore(store)
    with store.connection() as conn:
        row = conn.execute("SELECT * FROM live_product_periods WHERE id=? AND session_id=?",
                           (payload["period_id"], payload["session_id"])).fetchone()
        if row is None:
            raise ValueError("商品讲解记录已不存在")
        period = dict(row)
    windows = payload["windows"]
    created = skipped = failed = 0
    for index, piece in enumerate(windows):
        if checkpoint:
            checkpoint()
        with store.connection() as conn:
            row = conn.execute("SELECT * FROM live_segments WHERE id=? AND session_id=?",
                               (piece["segment_id"], payload["session_id"])).fetchone()
        if row is None or row["status"] not in {"completed", "importing", "imported"}:
            raise ValueError("对应录制分段已不可用，无法切分商品")
        segment = dict(row)
        original = Path(segment["path"]).resolve()
        if not original.is_relative_to((settings.data_dir / "recordings" / "live").resolve()):
            raise ValueError("录制分段不在直播录制目录中，拒绝切分")
        if not original.is_file():
            raise ValueError("对应录制分段文件已不存在")
        window = {**period, **piece, "abs_start": piece["start"], "abs_end": piece["end"]}
        source = original.with_name(f"{original.stem}_剪辑源.mp4") if original.suffix.lower() == ".ts" else original
        safe_product = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", window["product_name"]).strip(" ._")[:48]
        label = safe_product or window["promotion_id"]
        target = source.parent / f"商品片段_{segment['segment_index']:02d}_{label}_{window['id'][-8:]}.mp4"
        clip_id = live.begin_segment_product_clip(segment, window, target)
        if clip_id is None:
            skipped += 1
            continue
        length = window["clip_end"] - window["clip_start"]
        try:
            if original.suffix.lower() == ".ts":
                convert_to_mp4(original, source, settings, checkpoint)
            run_process([
                settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-ss", f"{window['clip_start']:.3f}", "-t", f"{length:.3f}",
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-f", "mp4", str(target),
            ], checkpoint=checkpoint, timeout=max(300, int(length) + 120))
            live.finish_segment_product_clip(clip_id, target, probe(target, settings)["duration_us"])
            created += 1
        except Exception as exc:
            live.fail_segment_product_clip(clip_id, f"{type(exc).__name__}: {str(exc)[:800]}")
            if type(exc).__name__ in {"Paused", "Cancelled"}:
                raise
            failed += 1
            logging.getLogger(__name__).exception(
                "直播商品片段生成失败 (segment=%s, period=%s)", segment["id"], window["id"]
            )
        if progress:
            progress((index + 1) / len(windows))
    if failed and not created and not skipped:
        raise RuntimeError(f"{failed} 个商品片段全部生成失败，详见片段错误信息")
    return {"period_id": period["id"], "created": created, "skipped": skipped, "failed": failed}


class DouyinResolver:
    async def resolve(self, url: str):
        try:
            import streamget
        except ImportError as exc:
            raise RuntimeError("缺少抖音直播解析依赖，请运行 uv sync") from exc
        live = streamget.DouyinLiveStream()
        if "v.douyin.com" in url:
            raw = await live.fetch_app_stream_data(url=url)
        else:
            raw = await live.fetch_web_stream_data(url=url)
        data = await live.fetch_stream_url(raw, None)
        room_id = str(raw.get("id_str") or raw.get("id") or raw.get("room_id") or "")
        if not room_id:
            owner = raw.get("owner") or {}
            room_id = str(owner.get("web_rid") or owner.get("webRid") or "")
        if not room_id:
            live_url = str(raw.get("live_url") or getattr(data, "live_url", "") or "")
            room_id = urlparse(live_url).path.rstrip("/").rsplit("/", 1)[-1]
        return {"is_live": bool(data.is_live), "stream_url": data.record_url,
                "anchor_name": getattr(data, "anchor_name", "") or "", "room_id": room_id}


def _safe_listener_exception(exc: Exception) -> str:
    if hasattr(exc, "stage"):
        return json.dumps({
            "type": type(exc).__name__,
            "stage": getattr(exc, "stage", ""),
            "http_status": getattr(exc, "status_code", None),
            "reason": str(exc)[:300],
        }, ensure_ascii=False)
    if isinstance(exc, ValueError) and str(exc):
        return f"商品事件会话准备失败：{str(exc)[:300]}"
    return f"商品事件会话准备失败：{type(exc).__name__}"


class LiveMonitorWorker:
    def __init__(self, settings: Settings, resolver=None, poll_seconds: int = POLL_SECONDS):
        self.settings = settings
        self.store = Store(settings.database)
        self.live = LiveStore(self.store)
        self.resolver = resolver or DouyinResolver()
        self.poll_seconds = poll_seconds
        self.processes: dict[str, tuple[subprocess.Popen, str]] = {}
        self.product_threads: dict[str, tuple[threading.Thread, threading.Event, str]] = {}
        self.product_clients: dict[str, object] = {}
        self.last_check: dict[str, float] = {}
        self.seen_controls: dict[str, float] = {}
        # Rooms already announced for the current broadcast; cleared when a room is seen offline.
        self.notified_live: set[str] = set()
        self._recover_interrupted_sessions()

    def _recover_interrupted_sessions(self):
        with self.store.connection() as conn:
            rows = conn.execute("SELECT s.id,s.monitor_id,s.ffmpeg_pid FROM live_sessions s WHERE s.status='recording'").fetchall()
            for row in rows:
                pid = row["ffmpeg_pid"]
                if pid:
                    try:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                           capture_output=True, timeout=10)
                        else:
                            os.kill(pid, 15)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                conn.execute("UPDATE live_sessions SET status='failed',ended=?,end_time_source='recovery_check' WHERE id=?",
                             (time.time(), row["id"]))
                conn.execute("UPDATE live_segments SET status='failed',ended=? WHERE session_id=? AND status='recording'",
                             (time.time(), row["id"]))
                conn.execute("UPDATE live_monitors SET current_session_id=NULL,live_state='unknown',updated=? WHERE id=?",
                             (time.time(), row["monitor_id"]))
                conn.execute("UPDATE live_monitors SET retry_blocked=1 WHERE id=?", (row["monitor_id"],))

    async def _resolve(self, url: str):
        return await self.resolver.resolve(url)

    def _start_recorder(self, monitor: dict, stream_url: str):
        session_id = new_id("session")
        directory = self.settings.data_dir / "recordings" / "live" / monitor["id"] / session_id
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", monitor["name"]).strip(" ._")[:60] or "douyin"
        prefix = f"{safe_name}_{datetime.now():%Y%m%d-%H%M%S}"
        pattern = directory / f"{prefix}-%06d.ts"
        args = [self.settings.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
                "-i", stream_url, "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-reset_timestamps", "1",
                "-segment_format", "mpegts", str(pattern)]
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            # Persist before exposing the child process to the loop.
            with self.store.connection() as conn:
                now = time.time()
                conn.execute("INSERT INTO live_sessions(id,monitor_id,status,started,ffmpeg_pid) VALUES(?,?,'recording',?,?)",
                             (session_id, monitor["id"], now, process.pid))
                conn.execute("UPDATE live_monitors SET current_session_id=?,live_state='recording',last_checked=?,error=NULL,updated=? WHERE id=?",
                             (session_id, now, now, monitor["id"]))
                # A manual start covers this broadcast only; auto recording keeps its own flag.
                conn.execute("UPDATE live_monitor_controls SET record_requested=0,record_held=0 WHERE monitor_id=?",
                             (monitor["id"],))
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            raise
        self.processes[monitor["id"]] = (process, session_id)
        self._start_python_product_listener(monitor, session_id)

    def _stop_product_listener(self, monitor_id: str):
        managed = self.product_threads.pop(monitor_id, None)
        if managed is None:
            return
        thread, stop_event, session_id = managed
        stop_event.set()
        client = self.product_clients.get(monitor_id)
        if client is not None:
            client.stop()
        if thread.is_alive():
            thread.join(timeout=10)
        self.live.set_product_listener_state(session_id, "stopped")

    def _start_python_product_listener(self, monitor: dict, session_id: str):
        self._stop_product_listener(monitor["id"])
        stop_event = threading.Event()
        self.live.set_product_listener_state(session_id, "preparing", "正在准备 Python 商品消息连接")
        thread = threading.Thread(
            target=self._run_python_product_listener,
            args=(monitor["id"], session_id, monitor["url"], stop_event),
            name=f"douyin-products-{monitor['id']}",
            daemon=True,
        )
        self.product_threads[monitor["id"]] = (thread, stop_event, session_id)
        thread.start()

    def _run_python_product_listener(self, monitor_id: str, session_id: str,
                                     room_url: str, stop_event: threading.Event):
        from ..douyin_product_events import DouyinProductEventClient, DouyinWebcastSessionPreparer
        from ..douyin_product_events.promotions import DouyinPromotionNameResolver

        client = None
        preparer = None
        failed = False
        try:
            try:
                cookie = self.store.get("config", "douyin_cookie")["value"]
            except KeyError:
                cookie = ""
            preparer = DouyinWebcastSessionPreparer(cookie=cookie)
            session = preparer.prepare(room_url)
        except Exception as exc:
            failed = True
            detail = _safe_listener_exception(exc)
            self.live.set_product_listener_state(session_id, "error", detail)
            return
        finally:
            if preparer is not None:
                preparer.close()

        if stop_event.is_set():
            self.live.set_product_listener_state(session_id, "stopped")
            return

        self.live.set_product_listener_state(session_id, "connecting", "正在连接 Python 商品消息流")
        name_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-product-name")
        resolver = DouyinPromotionNameResolver(
            session.room, session.user_agent, session.cookie, timeout=8
        )
        catalog_lock = threading.Lock()
        pending_product_ids: set[str] = set()
        checked_product_ids: set[str] = set()
        refresh_running = True

        def refresh_catalog(include_current: bool = False):
            requested_at = time.time()
            names, current = resolver.lookup_catalog()
            self.live.replace_session_products(session_id, names)
            self.live.set_product_catalog_error(session_id, None)
            if include_current and current is not None:
                promotion_id, title = current
                self.live.set_current_product(
                    session_id, promotion_id=promotion_id,
                    event_time=requested_at, received_at=requested_at,
                    product_name=title or names.get(promotion_id, ""),
                )
            return names

        def run_catalog_refresh(include_current: bool = False):
            nonlocal refresh_running
            names = {}
            try:
                names = refresh_catalog(include_current)
            except Exception as exc:
                detail = getattr(exc, "detail", None) or {
                    "summary": "抖音商品列表获取失败",
                    "stage": "catalog_refresh",
                    "reason": f"{type(exc).__name__}: {str(exc)[:1000]}",
                }
                self.live.set_product_catalog_error(session_id, detail)
                logging.getLogger(__name__).debug(
                    "直播间商品列表刷新失败 (%s)", type(exc).__name__
                )
            while True:
                with catalog_lock:
                    pending = set(pending_product_ids)
                    pending_product_ids.clear()
                    if not pending:
                        refresh_running = False
                        return
                unresolved = pending.difference(names)
                for promotion_id in pending.difference(unresolved):
                    self.live.update_product_name(
                        session_id, promotion_id, names[promotion_id]
                    )
                if unresolved:
                    try:
                        names = refresh_catalog()
                    except Exception as exc:
                        detail = getattr(exc, "detail", None) or {
                            "summary": "抖音商品列表获取失败",
                            "stage": "catalog_refresh",
                            "reason": f"{type(exc).__name__}: {str(exc)[:1000]}",
                        }
                        self.live.set_product_catalog_error(session_id, detail)
                        logging.getLogger(__name__).debug(
                            "直播间商品列表刷新失败 (%s)", type(exc).__name__
                        )
                    for promotion_id in unresolved:
                        product_name = names.get(promotion_id, "")
                        if product_name:
                            self.live.update_product_name(
                                session_id, promotion_id, product_name
                            )

        def schedule_catalog_refresh(promotion_id: str):
            nonlocal refresh_running
            with catalog_lock:
                if promotion_id in checked_product_ids:
                    return
                checked_product_ids.add(promotion_id)
                pending_product_ids.add(promotion_id)
                if refresh_running:
                    return
                refresh_running = True
            name_executor.submit(run_catalog_refresh)

        def on_state(state: str, detail: str):
            if state == "listening":
                self.live.set_product_listener_state(session_id, "listening", "Python 商品消息连接已建立")
            elif state in {"error", "disconnected"}:
                safe_detail = detail if state == "disconnected" else "WebSocket 客户端连接异常"
                message = f"Python 商品 WebSocket 连接异常：{safe_detail[:200]}"
                self.live.set_product_listener_state(session_id, "error", message)

        def on_current_product(event):
            product_name = self.live.product_name(session_id, event.promotion_id)
            self.live.set_current_product(
                session_id, promotion_id=event.promotion_id,
                event_time=event.event_time, received_at=event.received_at,
                product_name=product_name,
            )
            if not product_name:
                schedule_catalog_refresh(event.promotion_id)
            return product_name

        def on_event(event):
            if event.source != "live_shopping":
                return
            product_name = on_current_product(event)
            key = f"live-shopping:{event.message_id}:{event.promotion_id}"
            self.live.record_product_event(
                session_id, event_key=key, promotion_id=event.promotion_id,
                product_name=product_name,
                explain_type=event.explain_type, event_time=event.event_time,
                received_at=event.received_at, source="python_live_shopping",
            )

        try:
            # Load the complete room catalog and active product before relying on a change event.
            name_executor.submit(run_catalog_refresh, True)
            client = DouyinProductEventClient(
                on_event, on_current_product=on_current_product,
                on_state=on_state, include_samples=False,
            )
            self.product_clients[monitor_id] = client
            client.start_prepared(session)
            while not stop_event.wait(0.5):
                websocket_thread = getattr(client, "_thread", None)
                if websocket_thread is not None and not websocket_thread.is_alive():
                    failed = True
                    detail = "Python 商品 WebSocket 监听已退出"
                    self.live.set_product_listener_state(session_id, "error", detail)
                    return
        except Exception as exc:
            failed = True
            detail = f"Python 商品监听启动失败：{type(exc).__name__}"
            self.live.set_product_listener_state(session_id, "error", detail)
        finally:
            name_executor.shutdown(wait=True, cancel_futures=True)
            resolver.close()
            self.product_clients.pop(monitor_id, None)
            if client is not None:
                client.stop()
            if stop_event.is_set() and not failed:
                self.live.set_product_listener_state(session_id, "stopped")

    def _scan_segments(self, monitor_id: str, session_id: str, final: bool = False):
        directory = self.settings.data_dir / "recordings" / "live" / monitor_id / session_id
        files = sorted(path for path in directory.glob("*.ts")
                       if not path.name.startswith("商品片段_"))
        for file_index, path in enumerate(files):
            active = not final and file_index == len(files) - 1
            index = int(path.stem.rsplit("-", 1)[-1])
            size = path.stat().st_size
            known = self.live.segment_status(path)
            if known and known["status"] == "deleted":
                continue
            if not active and known and known["status"] in {"completed", "importing", "imported", "failed"}:
                self.live.upsert_segment(session_id, path, index + 1, known["status"], size,
                                         known["duration_us"])
                continue
            status = "recording" if active else "completed"
            duration_us = None
            if not active:
                try:
                    from ..media.processing import probe
                    duration_us = probe(path, self.settings)["duration_us"]
                except Exception:
                    status = "failed"
            self.live.upsert_segment(session_id, path, index + 1, status, size, duration_us)

    @staticmethod
    def _stop_process(process: subprocess.Popen):
        if process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.write(b"q\n")
                process.stdin.flush()
            process.wait(timeout=20)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _end_recorder(self, monitor: dict, status: str):
        managed = self.processes.pop(monitor["id"], None)
        if not managed:
            return
        process, session_id = managed
        self._stop_product_listener(monitor["id"])
        self._stop_process(process)
        self._scan_segments(monitor["id"], session_id, final=True)
        self.live.stop_session(monitor["id"], session_id, "stopped" if status == "offline" else "failed")
        if status == "error":
            self.live.set_monitor_state(monitor["id"], "error",
                                        "录制进程已退出；直播间离线后恢复自动录制，关闭自动录制后可手动开始",
                                        retry_blocked=True)

    def _notify_live(self, monitor: dict):
        from .notifications import show_toast
        show_toast(f"{monitor['name'] or '抖音直播间'} 开播了",
                   "正在自动录制" if monitor["auto_record"] and not monitor["retry_blocked"]
                   else "打开 DouyinCut 直播监控可开始录制")

    def run_once(self, now: float | None = None, stop: threading.Event | None = None):
        now = now or time.time()
        for monitor in self.live.all_monitors():
            if stop and stop.is_set():
                break
            monitor_id = monitor["id"]
            active = self.processes.get(monitor_id)
            if active:
                process, session_id = active
                self._scan_segments(monitor_id, session_id)
                if process.poll() is not None:
                    self._end_recorder(monitor, "error")
                    monitor = self.live.monitor(monitor_id)
                elif monitor["record_held"]:
                    # A manual stop takes effect on the next loop instead of waiting for the poll.
                    self._end_recorder(monitor, "offline")
                    self.live.set_monitor_state(monitor_id, "live")
                    continue
            # A settings change or start / stop request is checked right away instead of waiting for the poll.
            requested = monitor["updated_controls"] != self.seen_controls.get(monitor_id)
            if not requested and now - self.last_check.get(monitor_id, 0) < self.poll_seconds:
                continue
            self.seen_controls[monitor_id] = monitor["updated_controls"]
            self.last_check[monitor_id] = now
            try:
                # Network calls can block for 30+ seconds; use a shorter timeout so exit is responsive.
                async def resolve_with_timeout():
                    return await asyncio.wait_for(self._resolve(monitor["url"]), timeout=8)
                resolved = asyncio.run(resolve_with_timeout())
                is_live = bool(resolved.get("is_live"))
                self.live.set_liveness(monitor_id, is_live)
                if not monitor["name"] and resolved.get("anchor_name"):
                    self.live.set_auto_name(monitor_id, resolved["anchor_name"])
                    monitor = self.live.monitor(monitor_id)
                if not is_live:
                    self.notified_live.discard(monitor_id)
                    if monitor_id in self.processes:
                        self._end_recorder(monitor, "offline")
                    self.live.clear_broadcast_requests(monitor_id)
                    self.live.set_monitor_state(monitor_id, "offline", retry_blocked=False)
                    continue
                if monitor_id not in self.notified_live:
                    self.notified_live.add(monitor_id)
                    if monitor["notify_live"]:
                        self._notify_live(monitor)
                wants_recording = (monitor["auto_record"] or monitor["record_requested"]) and not monitor["record_held"]
                if monitor_id in self.processes:
                    self.live.set_monitor_state(monitor_id, "recording")
                elif not wants_recording:
                    self.live.set_monitor_state(monitor_id, "live")
                elif not resolved.get("stream_url"):
                    self.live.set_monitor_state(monitor_id, "error", "直播间已开播，但暂时无法解析媒体流地址")
                elif monitor["retry_blocked"]:
                    self.live.set_monitor_state(monitor_id, "error",
                                                "录制曾中断；直播间离线后恢复自动录制，关闭自动录制后可手动开始")
                else:
                    self._start_recorder(monitor, resolved["stream_url"])
            except Exception as exc:
                self.live.set_liveness(monitor_id, None)
                self.live.set_monitor_state(monitor_id, "error", f"{type(exc).__name__}: {exc}")

    def run(self, stop=None):
        stop = stop if stop is not None else threading.Event()
        try:
            while not stop.is_set():
                try:
                    self.run_once(stop=stop)
                except Exception:
                    logging.getLogger("douyincut.live-monitor").exception("Live monitor cycle failed")
                stop.wait(1)
        except KeyboardInterrupt:
            pass
        for monitor_id in list(self.processes):
            monitor = self.live.monitor(monitor_id)
            self._end_recorder(monitor, "offline")
            self.live.set_monitor_state(monitor_id, "unknown")
        for monitor_id in list(self.product_threads):
            self._stop_product_listener(monitor_id)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.prepare()
    worker = LiveMonitorWorker(settings)
    worker.run_once() if args.once else worker.run()


if __name__ == "__main__":
    main()
