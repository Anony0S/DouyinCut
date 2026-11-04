from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import new_id


class SessionStopping(RuntimeError):
    """Raised when a new operation arrives after desktop shutdown begins."""


class Store:
    """Short, per-operation SQLite transactions; no network work inside them."""

    _SHUTDOWN_KEY = "desktop-session:shutting-down"

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    body TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(kind, id, revision)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, owner TEXT, lease REAL,
                    error TEXT, result TEXT, created REAL NOT NULL, updated REAL NOT NULL, error_detail TEXT
                );
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cloud_operations (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL, config_fingerprint TEXT NOT NULL, cache_key TEXT, status TEXT NOT NULL,
                    reason TEXT, started REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    job_id TEXT NOT NULL, attempt INTEGER NOT NULL, owner TEXT NOT NULL,
                    started REAL NOT NULL, ended REAL, state TEXT, error TEXT,
                    error_detail TEXT, PRIMARY KEY(job_id, attempt)
                );
                CREATE INDEX IF NOT EXISTS jobs_asset ON jobs(kind,json_extract(payload,'$.asset_id'),created);
                CREATE INDEX IF NOT EXISTS jobs_plan ON jobs(json_extract(payload,'$.plan_id'),updated);
                CREATE INDEX IF NOT EXISTS jobs_updated ON jobs(updated);
                CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC,id DESC);
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def put(self, kind: str, body: dict, revision: int = 1):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO objects VALUES (?, ?, ?, ?, ?)",
                (kind, body["id"], revision, json.dumps(body, ensure_ascii=False), time.time()),
            )

    def get(self, kind: str, object_id: str, revision: int | None = None):
        with self.connection() as conn:
            if revision is None:
                row = conn.execute(
                    "SELECT body FROM objects WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 1",
                    (kind, object_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT body FROM objects WHERE kind=? AND id=? AND revision=?",
                    (kind, object_id, revision),
                ).fetchone()
        if row is None:
            raise KeyError(object_id)
        return json.loads(row["body"])

    def list(self, kind: str, limit: int = 100, offset: int = 0):
        with self.connection() as conn:
            rows = conn.execute("""
                SELECT o.body FROM objects o
                JOIN (SELECT id, MAX(revision) rev FROM objects WHERE kind=? GROUP BY id) latest
                ON o.id=latest.id AND o.revision=latest.rev
                WHERE o.kind=? ORDER BY o.created DESC LIMIT ? OFFSET ?
            """, (kind, kind, limit, offset)).fetchall()
        return [json.loads(row["body"]) for row in rows]

    def next_revision(self, kind: str, object_id: str, body: dict, expected_revision: int | None = None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT MAX(revision) rev FROM objects WHERE kind=? AND id=?", (kind, object_id)
            ).fetchone()
            if row["rev"] is None:
                raise KeyError(object_id)
            if expected_revision is not None and row["rev"] != expected_revision:
                raise ValueError("记录已更新，请刷新后重试")
            revision = row["rev"] + 1
            if kind == "plan":
                body = {**body, "revision": revision}
            conn.execute("INSERT INTO objects VALUES (?, ?, ?, ?, ?)",
                         (kind, object_id, revision, json.dumps(body, ensure_ascii=False), time.time()))
        return body

    def cached(self, key: str):
        with self.connection() as conn:
            row = conn.execute("SELECT body FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row["body"]) if row else None

    def cache(self, key: str, body):
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)",
                         (key, json.dumps(body, ensure_ascii=False)))

    def begin_cloud_operation(self, job_id: str, stage: str, config: dict, cache_key: str | None = None):
        now = time.time()
        operation_id = new_id("cloud")
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM runtime_state WHERE key=?", (self._SHUTDOWN_KEY,)).fetchone():
                raise SessionStopping("桌面会话正在退出，不再提交云端请求")
            job = conn.execute("SELECT attempts,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["state"] != "running":
                raise RuntimeError("分析任务已停止，拒绝提交云端请求")
            conn.execute("INSERT INTO cloud_operations VALUES(?,?,?,?,?,?,'active',NULL,?,?)",
                         (operation_id, job_id, stage, job["attempts"], fingerprint, cache_key, now, now))
            if cache_key:
                conn.execute("INSERT OR REPLACE INTO cache VALUES(?,?)",
                             (cache_key, json.dumps({"status": "submitting", "exit_operation_id": operation_id}))
            )
        return operation_id

    def complete_cloud_operation(self, operation_id: str, cache_key: str | None = None, cache_body=None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT op.status FROM cloud_operations op JOIN jobs ON jobs.id=op.job_id
                WHERE op.id=? AND jobs.attempts=op.attempt""", (operation_id,)).fetchone()
            # A reply for an attempt that has since been reclaimed must not replace the newer execution's state.
            if row is None or row["status"] != "active":
                return False
            if cache_key and cache_body is not None:
                encoded = json.dumps(cache_body, ensure_ascii=False)
                conn.execute("UPDATE cache SET body=? WHERE json_extract(body,'$.exit_operation_id')=?",
                             (encoded, operation_id))
                conn.execute("INSERT OR REPLACE INTO cache VALUES(?,?)",
                             (cache_key, encoded))
            conn.execute("UPDATE cloud_operations SET status='succeeded',updated=? WHERE id=?",
                         (time.time(), operation_id))
        return True

    def fail_cloud_operation(self, operation_id: str):
        with self.connection() as conn:
            conn.execute("UPDATE cloud_operations SET status='failed',updated=? WHERE id=? AND status='active'",
                         (time.time(), operation_id))

    def interruptible_operation(self, operation_id: str):
        with self.connection() as conn:
            row = conn.execute("SELECT status FROM cloud_operations WHERE id=?", (operation_id,)).fetchone()
        return bool(row and row["status"] == "exit_interrupted")

    def recover_exit_interrupted_operations(self):
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,job_id,cache_key FROM cloud_operations "
                                "WHERE status='exit_interrupted'").fetchall()
            job_ids = {row["job_id"] for row in rows}
            for row in rows:
                conn.execute("DELETE FROM cache WHERE json_extract(body,'$.exit_operation_id')=?",
                             (row["id"],))
                conn.execute("UPDATE cloud_operations SET status='consumed',updated=? WHERE id=?",
                             (now, row["id"]))
            for job_id in job_ids:
                job = conn.execute("SELECT state,attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
                if job is None or job["state"] in {"succeeded", "cancelled"}:
                    continue
                conn.execute("UPDATE attempts SET ended=?,state='interrupted',error='显式退出中断云端请求' "
                             "WHERE job_id=? AND attempt=? AND ended IS NULL", (now, job_id, job["attempts"]))
                conn.execute("UPDATE jobs SET state='queued',owner=NULL,lease=NULL,error=NULL,error_detail=NULL,updated=? "
                             "WHERE id=?", (now, job_id))
        return len(job_ids)

    def commit_result(self, cache_key: str, result: dict, objects: list[tuple[str, dict]], followup=None,
                      job_id: str | None = None, owner: str | None = None,
                      replace_transcripts_for: str | None = None):
        """Publish metadata and idempotency cache in the same transaction."""
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if job_id is not None:
                row = conn.execute("SELECT owner,lease,state FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None or row["owner"] != owner or row["lease"] < now or row["state"] != "running":
                    raise RuntimeError("任务租约已失效，拒绝发布结果")
            if conn.execute("SELECT 1 FROM cache WHERE key=?", (cache_key,)).fetchone():
                return
            if replace_transcripts_for is not None:
                conn.execute("DELETE FROM objects WHERE kind='transcript' AND "
                             "json_extract(body, '$.segment.source_asset_id')=?", (replace_transcripts_for,))
            for kind, body in objects:
                conn.execute("INSERT INTO objects VALUES(?,?,?,?,?)",
                             (kind, body["id"], body.get("revision", 1), json.dumps(body, ensure_ascii=False), now))
            conn.execute("INSERT INTO cache VALUES(?,?)", (cache_key, json.dumps(result, ensure_ascii=False)))
            if followup:
                kind, payload = followup
                stopping = conn.execute("SELECT 1 FROM runtime_state WHERE key=?",
                                        (self._SHUTDOWN_KEY,)).fetchone()
                state = "paused" if stopping else "queued"
                conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,?,?,?)",
                             (new_id("job"), kind, json.dumps(payload), state, now, now))

    def enqueue(self, kind: str, payload: dict):
        job_id, now = new_id("job"), time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM runtime_state WHERE key=?", (self._SHUTDOWN_KEY,)).fetchone():
                raise SessionStopping("桌面会话正在退出，不再接收新任务")
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                         (job_id, kind, json.dumps(payload, ensure_ascii=False), now, now))
        return self.job(job_id)

    @staticmethod
    def decode_job(row):
        result = dict(row)
        result["has_error_detail"] = bool(result.pop("error_detail", None))
        result["payload"] = json.loads(result["payload"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        result.pop("owner", None)
        result.pop("lease", None)
        return result

    def job(self, job_id: str):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self.decode_job(row)

    def jobs(self, limit=100, offset=0, before_created=None, before_id=None):
        where, params = "", []
        if before_created is not None:
            where = "WHERE (created,id)<(?,?)"
            params = [before_created, before_id]
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM jobs {where} ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                                (*params, limit, offset)).fetchall()
        return [self.decode_job(row) for row in rows]

    def job_error_detail(self, job_id: str):
        with self.connection() as conn:
            row = conn.execute("SELECT error,error_detail FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        detail = json.loads(row["error_detail"]) if row["error_detail"] else {
            "reason": "该次失败没有保存云端响应；更新后发生的新错误会保留详情",
            "response_body": None,
        }
        return {"summary": row["error"], **detail}

    def claim(self, owner: str, lease_seconds: int = 60):
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM runtime_state WHERE key=?", (self._SHUTDOWN_KEY,)).fetchone():
                return None
            # Recovery never resubmits an uncertain cloud operation: the handler uses stage cache.
            conn.execute("""UPDATE attempts SET ended=?,state='interrupted'
                WHERE ended IS NULL AND job_id IN
                (SELECT id FROM jobs WHERE state IN ('running','pause_requested','cancel_requested') AND lease<?)""", (now, now))
            conn.execute("UPDATE jobs SET state='queued',owner=NULL,lease=NULL WHERE state='running' AND lease<?", (now,))
            conn.execute("UPDATE jobs SET state='paused',owner=NULL,lease=NULL WHERE state='pause_requested' AND lease<?", (now,))
            conn.execute("UPDATE jobs SET state='cancelled',owner=NULL,lease=NULL WHERE state='cancel_requested' AND lease<?", (now,))
            row = conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute("UPDATE jobs SET state='running',owner=?,lease=?,attempts=attempts+1,error=NULL,error_detail=NULL,updated=? WHERE id=?",
                         (owner, now + lease_seconds, now, row["id"]))
            conn.execute("INSERT INTO attempts(job_id,attempt,owner,started) VALUES(?,?,?,?)",
                         (row["id"], row["attempts"] + 1, owner, now))
        return self.job(row["id"])

    def heartbeat(self, job_id: str, owner: str):
        with self.connection() as conn:
            cursor = conn.execute("UPDATE jobs SET lease=?,updated=? WHERE id=? AND owner=? AND state IN ('running','pause_requested','cancel_requested')",
                                  (time.time() + 60, time.time(), job_id, owner))
            return cursor.rowcount == 1

    def progress(self, job_id: str, owner: str, value: float):
        with self.connection() as conn:
            conn.execute("UPDATE jobs SET progress=?,updated=? WHERE id=? AND owner=?",
                         (min(1, max(0, value)), time.time(), job_id, owner))

    def finish(self, job_id: str, owner: str, state: str, result=None, error=None, error_detail=None):
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state, attempts FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
            if row is None:
                raise RuntimeError("任务租约已失效")
            if state == "succeeded" and row["state"] == "cancel_requested":
                state = "cancelled"
            encoded_detail = json.dumps(error_detail, ensure_ascii=False) if error_detail is not None else None
            conn.execute("UPDATE jobs SET state=?,result=?,error=?,error_detail=?,owner=NULL,lease=NULL,updated=?,progress=CASE WHEN ?='succeeded' THEN 1 ELSE progress END WHERE id=?",
                         (state, json.dumps(result, ensure_ascii=False) if result is not None else None, error,
                          encoded_detail, now, state, job_id))
            conn.execute("UPDATE attempts SET ended=?,state=?,error=?,error_detail=? WHERE job_id=? AND attempt=?",
                         (now, state, error, encoded_detail, job_id, row["attempts"]))

    def pause_for_shutdown(self):
        """Persist queued work as paused and ask current owners to stop at a checkpoint."""
        now = time.time()
        local_kinds = ("import", "preview", "export", "jianying", "live_product_clips")
        kinds = ",".join("?" for _ in local_kinds)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO runtime_state(key,value) VALUES(?, 'stopping')",
                         (self._SHUTDOWN_KEY,))
            conn.execute("UPDATE cloud_operations SET status='exit_interrupted',reason='explicit_exit',updated=? "
                         "WHERE status='active'", (now,))
            conn.execute(f"UPDATE jobs SET state='paused',owner=NULL,lease=NULL,updated=? "
                         f"WHERE state='queued' AND kind IN ({kinds})", (now, *local_kinds))
            conn.execute(f"UPDATE jobs SET state='pause_requested',updated=? "
                         f"WHERE state='running' AND kind IN ({kinds}, 'analyze')", (now, *local_kinds))

    def start_session(self):
        self.recover_exit_interrupted_operations()
        with self.connection() as conn:
            conn.execute("""UPDATE jobs SET state='queued',owner=NULL,lease=NULL,updated=?
                WHERE kind='analyze' AND state IN ('queued','paused','pause_requested','running')
                AND EXISTS (SELECT 1 FROM cloud_operations op WHERE op.job_id=jobs.id
                    AND op.attempt=jobs.attempts AND op.status='succeeded')""", (time.time(),))
            conn.execute("DELETE FROM runtime_state WHERE key=?", (self._SHUTDOWN_KEY,))

    def is_shutting_down(self):
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM runtime_state WHERE key=?", (self._SHUTDOWN_KEY,)).fetchone() is not None

    def control(self, job_id: str, action: str, *, retry_config: dict | None = None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if action in {"resume", "retry", "acknowledge_asr_uncertain"} and conn.execute(
                    "SELECT 1 FROM runtime_state WHERE key=?", (self._SHUTDOWN_KEY,)).fetchone():
                raise SessionStopping("桌面会话正在退出，不再接受新任务")
            row = conn.execute("SELECT state,kind,payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            states = {
                "pause": {"queued": "paused", "running": "pause_requested"},
                "resume": {"paused": "queued"},
                "cancel": {"queued": "cancelled", "paused": "cancelled", "running": "cancel_requested", "pause_requested": "cancel_requested"},
                "retry": {"failed": "queued"},
                "acknowledge_asr_uncertain": {"failed": "queued"},
            }
            target = states.get(action, {}).get(row["state"])
            if target is None:
                raise ValueError("当前任务状态不支持此操作")
            if action in {"retry", "acknowledge_asr_uncertain"} and row["kind"] == "analyze" and retry_config is not None:
                payload = json.loads(row["payload"])
                active = conn.execute("SELECT 1 FROM jobs WHERE id<>? AND kind='analyze' "
                                      "AND json_extract(payload,'$.asset_id')=? "
                                      "AND state IN ('queued','running','pause_requested','paused','cancel_requested')",
                                      (job_id, payload["asset_id"])).fetchone()
                if active:
                    raise ValueError("该素材已有活动分析任务，请先完成或取消后再重试")
                previous = payload["config"]
                selection_changed = any(previous[field] != retry_config[field]
                                        for field in ("base_url", "asr_base_url", "asr_model", "planner_model"))
                if selection_changed:
                    if conn.execute("SELECT 1 FROM cache WHERE key=?", (f"job-result:{job_id}",)).fetchone():
                        raise ValueError("该任务已发布粗剪结果，修改模型后请从素材页创建新的分析")
                    # Paid ASR responses and uncertain-request markers remain keyed by endpoint/model.
                    conn.execute("DELETE FROM cache WHERE key IN (?,?)",
                                 (f"roughcut-timestamps-v1:{job_id}", f"roughcut-transcript:{job_id}"))
                if action == "acknowledge_asr_uncertain":
                    # A timeout / disconnect leaves a submitting marker because the provider
                    # may have accepted the paid request. Only an explicit user action after
                    # checking the provider record may release markers for this asset/model.
                    asset = conn.execute("""SELECT body FROM objects
                        WHERE kind='asset' AND id=? ORDER BY revision DESC LIMIT 1""",
                        (payload["asset_id"],)).fetchone()
                    if asset is None:
                        raise KeyError(payload["asset_id"])
                    asset_body = json.loads(asset["body"])
                    asr_url = f"{previous['asr_base_url']}/api/v1/services/aigc/multimodal-generation/generation"
                    prefix = f"qwen-audio-v1:{asset_body['sha256']}:{hashlib.sha256(asr_url.encode()).hexdigest()}:{previous['asr_model']}:"
                    conn.execute("DELETE FROM cache WHERE key LIKE ? AND json_extract(body, '$.status')='submitting'",
                                 (prefix + "%",))
                payload["config"] = retry_config
                conn.execute("UPDATE jobs SET payload=? WHERE id=?",
                             (json.dumps(payload, ensure_ascii=False), job_id))
            if action in {"retry", "acknowledge_asr_uncertain"}:
                conn.execute("UPDATE jobs SET error=NULL,error_detail=NULL WHERE id=?", (job_id,))
            conn.execute("UPDATE jobs SET state=?,updated=? WHERE id=?", (target, time.time(), job_id))
        return self.job(job_id)
