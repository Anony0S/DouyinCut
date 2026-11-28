"""Platform product existence & stock matching, caching, and controlled lookup."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..core.storage import SessionStopping
from .client import ProductPlatformError
from .daduoduo import search_text
from .service import CONFIG_ID as XINGDOU_CONFIG_ID, ProductPlatformService
from .xingdongli import XingdongliService

logger = logging.getLogger(__name__)

MIN_CONTAINS_MATCH_LEN = 4
DEFAULT_BATCH_INTERVAL_SECONDS = 0.35
OUT_OF_STOCK_TEXTS = {"无库存", "无货", "已售罄", "售罄", "缺货"}
UNKNOWN_STOCK_TEXTS = {"", "-", "--", "—", "暂无", "未知"}


def classify_stock(stock: Any) -> tuple[str, int | float | str | None]:
    if stock is None or isinstance(stock, bool):
        return "stock_unknown", None
    if isinstance(stock, (int, float)):
        if not math.isfinite(stock):
            return "stock_unknown", None
        if stock <= 0:
            return "out_of_stock", 0
        normalized = int(stock) if isinstance(stock, int) or float(stock).is_integer() else stock
        return "available", normalized
    if isinstance(stock, str):
        text = stock.strip()
        if text in UNKNOWN_STOCK_TEXTS:
            return "stock_unknown", None
        if text in OUT_OF_STOCK_TEXTS:
            return "out_of_stock", 0
        compact = text.replace(",", "")
        try:
            numeric = float(compact)
        except ValueError:
            return "available", text
        if not math.isfinite(numeric):
            return "stock_unknown", None
        if numeric <= 0:
            return "out_of_stock", 0
        normalized = int(numeric) if numeric.is_integer() else numeric
        return "available", normalized
    return "stock_unknown", None


def match_platform_product(
    products: list[dict[str, Any]],
    *,
    promotion_id: str = "",
    product_name: str = "",
    allow_short_query_contains: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    target_id = str(promotion_id or "").strip()
    if target_id:
        for item in products:
            if not isinstance(item, dict):
                continue
            item_ids = {
                str(item.get("platform_product_id") or "").strip(),
                str(item.get("id") or "").strip(),
            }
            if target_id in item_ids:
                return item, "id"

    query = search_text(product_name) if product_name else ""
    if not query:
        return None, None

    normalized_items = [
        (search_text(str(item.get("title") or "")), item)
        for item in products
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    for title, item in normalized_items:
        if title == query:
            return item, "exact_title"

    min_len = 1 if allow_short_query_contains else MIN_CONTAINS_MATCH_LEN
    for title, item in normalized_items:
        if len(query) >= min_len and query in title:
            return item, "contains_title"
    for title, item in normalized_items:
        if len(title) >= MIN_CONTAINS_MATCH_LEN and title in query:
            return item, "contains_title"
    return None, None


def build_availability_record(
    platform: str,
    products: list[dict[str, Any]],
    *,
    promotion_id: str = "",
    product_name: str = "",
    checked_at: float | None = None,
    allow_short_query_contains: bool = False,
) -> dict[str, Any]:
    checked = time.time() if checked_at is None else checked_at
    matched, match_kind = match_platform_product(
        products,
        promotion_id=promotion_id,
        product_name=product_name,
        allow_short_query_contains=allow_short_query_contains,
    )
    if matched is None:
        return {
            "platform": platform,
            "state": "not_found",
            "match_kind": None,
            "stock": None,
            "matched_title": None,
            "platform_product_id": None,
            "checked_at": checked,
            "error": "",
        }
    state, stock = classify_stock(matched.get("stock"))
    return {
        "platform": platform,
        "state": state,
        "match_kind": match_kind,
        "stock": stock,
        "matched_title": str(matched.get("title") or "").strip() or None,
        "platform_product_id": str(matched.get("platform_product_id") or matched.get("id") or "").strip() or None,
        "checked_at": checked,
        "error": "",
    }


def _unchecked_record(platform: str, state: str = "unchecked") -> dict[str, Any]:
    return {
        "platform": platform,
        "state": state,
        "match_kind": None,
        "stock": None,
        "matched_title": None,
        "platform_product_id": None,
        "checked_at": None,
        "error": "",
    }


def current_xingdou_scope(conn) -> str:
    row = conn.execute(
        "SELECT body FROM objects WHERE kind='config' AND id=? ORDER BY revision DESC LIMIT 1",
        (XINGDOU_CONFIG_ID,),
    ).fetchone()
    if row is None:
        return ""
    try:
        config = json.loads(row["body"])
    except (ValueError, TypeError):
        return ""
    if not isinstance(config, dict) or not config.get("logged_in"):
        return ""
    return str(config.get("main_ip_id") or "").strip()


def resolve_session_period_availability(conn, monitor_id: str, periods: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """Attach purely local platform availability snapshots to session periods (never performs network I/O)."""
    binding_row = conn.execute(
        "SELECT binding_id, json_extract(body,'$.platform') AS platform, json_extract(body,'$.ip_id') AS ip_id "
        "FROM live_room_platforms WHERE monitor_id=?",
        (monitor_id,),
    ).fetchone()
    if binding_row is None:
        return {period["id"]: None for period in periods}

    platform = binding_row["platform"]
    if platform == "daduoduo":
        return _resolve_daduoduo(conn, binding_row["binding_id"], periods)
    if platform in ("xingdou", "xingdongli"):
        scope_key = str(binding_row["ip_id"] or "").strip() if platform == "xingdongli" else current_xingdou_scope(conn)
        return _resolve_cached(conn, monitor_id, platform, scope_key, periods)
    return {period["id"]: None for period in periods}


def _resolve_daduoduo(conn, binding_id: str, periods: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    saved = conn.execute(
        "SELECT products, synced_at FROM daduoduo_catalogs WHERE binding_id=?",
        (binding_id,),
    ).fetchone()
    if saved is None:
        return {
            period["id"]: (_unchecked_record("daduoduo", "not_synced") if period["product_name"].strip() else None)
            for period in periods
        }
    try:
        products = json.loads(saved["products"])
    except (ValueError, TypeError):
        products = []
    synced_at = float(saved["synced_at"])
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    result: dict[str, dict[str, Any] | None] = {}
    for period in periods:
        name = period["product_name"].strip()
        if not name:
            result[period["id"]] = None
            continue
        key = (str(period["promotion_id"]).strip(), search_text(name))
        if key not in cache:
            cache[key] = build_availability_record(
                "daduoduo",
                products,
                promotion_id=period["promotion_id"],
                product_name=name,
                checked_at=synced_at,
                allow_short_query_contains=True,
            )
        result[period["id"]] = dict(cache[key])
    return result


def _decode_stock(value: str | None) -> int | float | str | None:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _resolve_cached(
    conn,
    monitor_id: str,
    platform: str,
    scope_key: str,
    periods: list[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    keys = {search_text(period["product_name"]) for period in periods if period["product_name"].strip()}
    cached: dict[str, dict[str, Any]] = {}
    if scope_key and keys:
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT query_key, state, match_kind, stock, matched_title, platform_product_id, error, checked_at "
            f"FROM live_platform_product_cache "
            f"WHERE monitor_id=? AND platform=? AND scope_key=? AND query_key IN ({placeholders})",
            (monitor_id, platform, scope_key, *sorted(keys)),
        ).fetchall()
        for row in rows:
            cached[row["query_key"]] = {
                "platform": platform,
                "state": row["state"],
                "match_kind": row["match_kind"],
                "stock": _decode_stock(row["stock"]),
                "matched_title": row["matched_title"],
                "platform_product_id": row["platform_product_id"],
                "checked_at": float(row["checked_at"]),
                "error": row["error"] or "",
            }
    result: dict[str, dict[str, Any] | None] = {}
    for period in periods:
        name = period["product_name"].strip()
        if not name:
            result[period["id"]] = None
            continue
        key = search_text(name)
        result[period["id"]] = dict(cached[key]) if key in cached else _unchecked_record(platform, "unchecked")
    return result


def save_availability_cache(
    store,
    monitor_id: str,
    platform: str,
    scope_key: str,
    product_name: str,
    record: dict[str, Any],
) -> None:
    query_key = search_text(product_name)
    if not query_key or not scope_key:
        return
    encoded_stock = None if record.get("stock") is None else json.dumps(record["stock"], ensure_ascii=False)
    with store.connection() as conn:
        if not conn.execute("SELECT 1 FROM live_monitors WHERE id=?", (monitor_id,)).fetchone():
            return
        conn.execute(
            "INSERT INTO live_platform_product_cache "
            "(monitor_id, platform, scope_key, query_key, state, match_kind, stock, matched_title, platform_product_id, error, checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(monitor_id, platform, scope_key, query_key) DO UPDATE SET "
            "state=excluded.state, match_kind=excluded.match_kind, stock=excluded.stock, "
            "matched_title=excluded.matched_title, platform_product_id=excluded.platform_product_id, "
            "error=excluded.error, checked_at=excluded.checked_at",
            (
                monitor_id,
                platform,
                scope_key,
                query_key,
                record["state"],
                record.get("match_kind"),
                encoded_stock,
                record.get("matched_title"),
                record.get("platform_product_id"),
                str(record.get("error") or "")[:300],
                float(record.get("checked_at") or time.time()),
            ),
        )


def _room_online_binding(conn, monitor_id: str) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT json_extract(body,'$.platform') AS platform, json_extract(body,'$.ip_id') AS ip_id "
        "FROM live_room_platforms WHERE monitor_id=?",
        (monitor_id,),
    ).fetchone()
    if row is None:
        return None
    platform = row["platform"]
    if platform == "xingdongli":
        return platform, str(row["ip_id"] or "").strip()
    if platform == "xingdou":
        return platform, current_xingdou_scope(conn)
    return None


def check_online_product(
    store,
    product_platform: ProductPlatformService,
    xingdongli: XingdongliService,
    monitor_id: str,
    platform: str,
    scope_key: str,
    *,
    promotion_id: str = "",
    product_name: str,
) -> dict[str, Any]:
    keyword = product_name.strip()
    if platform == "xingdou":
        products, _, actual_scope = product_platform.search_items(keyword)
        scope_key = actual_scope or scope_key
    elif platform == "xingdongli":
        result = xingdongli.search(scope_key, keyword, 0)
        products = result["products"]
    else:
        raise ProductPlatformError("该平台不支持在线商品库存检测", status=409)
    record = build_availability_record(
        platform,
        products,
        promotion_id=promotion_id,
        product_name=keyword,
    )
    save_availability_cache(store, monitor_id, platform, scope_key, keyword, record)
    return record


class SessionProductAvailabilityChecker:
    """Manual per-session batch stock/existence checker for online platforms (Xingdou / Xingdongli)."""

    def __init__(
        self,
        store,
        product_platform: ProductPlatformService,
        xingdongli: XingdongliService,
        *,
        request_interval: float = DEFAULT_BATCH_INTERVAL_SECONDS,
    ):
        self.store = store
        self.product_platform = product_platform
        self.xingdongli = xingdongli
        self.request_interval = max(0.0, request_interval)
        self.lock = threading.Lock()
        self.progress: dict[tuple[str, str], dict[str, Any]] = {}
        self.stopping = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="product-availability")

    def close(self) -> None:
        self.stopping.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _validate_session_and_binding(self, monitor_id: str, session_id: str) -> tuple[str, str, list[tuple[str, str]]]:
        with self.store.connection() as conn:
            session = conn.execute(
                "SELECT id FROM live_sessions WHERE id=? AND monitor_id=?",
                (session_id, monitor_id),
            ).fetchone()
            if session is None:
                raise KeyError(session_id)
            row = conn.execute(
                "SELECT json_extract(body,'$.platform') AS platform, json_extract(body,'$.ip_id') AS ip_id "
                "FROM live_room_platforms WHERE monitor_id=?",
                (monitor_id,),
            ).fetchone()
            if row is None:
                raise ProductPlatformError("请先为直播间绑定商品平台", code="ROOM_PLATFORM_REQUIRED", status=409)
            platform = row["platform"]
            if platform == "daduoduo":
                raise ProductPlatformError("达多多请点击「更新商品」同步本地商品目录", status=409)
            if platform not in ("xingdou", "xingdongli"):
                raise ProductPlatformError("当前绑定平台不支持库存检测", status=409)
            scope_key = str(row["ip_id"] or "").strip() if platform == "xingdongli" else current_xingdou_scope(conn)
            periods = conn.execute(
                "SELECT promotion_id, product_name FROM live_product_periods "
                "WHERE session_id=? ORDER BY (ended IS NULL) DESC, started DESC, id DESC",
                (session_id,),
            ).fetchall()
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for period in periods:
            name = str(period["product_name"] or "").strip()
            if not name:
                continue
            key = search_text(name)
            if not key or key in seen:
                continue
            seen.add(key)
            items.append((str(period["promotion_id"] or "").strip(), name))
        return platform, scope_key, items

    def status(self, monitor_id: str, session_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM live_sessions WHERE id=? AND monitor_id=?",
                (session_id, monitor_id),
            ).fetchone():
                raise KeyError(session_id)
        with self.lock:
            state = dict(self.progress.get((monitor_id, session_id), {"state": "idle"}))
        return {
            "state": "idle",
            "checked": 0,
            "total": 0,
            "error": "",
            "error_code": "",
            **state,
        }

    def refresh_session(self, monitor_id: str, session_id: str) -> dict[str, Any]:
        platform, scope_key, items = self._validate_session_and_binding(monitor_id, session_id)
        if not items:
            return {"state": "idle", "checked": 0, "total": 0, "error": "", "error_code": ""}
        # Fail fast for obvious local auth/scope problems before queuing a background batch.
        if platform == "xingdongli":
            self.xingdongli.scope(scope_key)
        else:
            status = self.product_platform.status()
            if not status["logged_in"]:
                raise ProductPlatformError("星斗商品平台未登录", code="PRODUCT_PLATFORM_AUTH_REQUIRED", status=401)
            scope_key = str(status.get("main_ip_id") or "").strip()
            if not scope_key:
                raise ProductPlatformError("当前账号没有关联的商品查询范围，请先在星斗平台确认合作权限", status=409)
        key = (monitor_id, session_id)
        with self.lock:
            if self.stopping.is_set() or self.store.is_shutting_down():
                raise SessionStopping("应用正在退出")
            if self.progress.get(key, {}).get("state") not in ("queued", "running"):
                self.progress[key] = {
                    "state": "queued",
                    "checked": 0,
                    "total": len(items),
                    "error": "",
                    "error_code": "",
                }
                self.executor.submit(self._run_batch, monitor_id, session_id, platform, scope_key, items)
        return self.status(monitor_id, session_id)

    def record_search_result(
        self,
        monitor_id: str,
        platform: str,
        scope_key: str,
        keyword: str,
        products: list[dict[str, Any]],
        *,
        promotion_id: str = "",
    ) -> None:
        if platform not in ("xingdou", "xingdongli") or not keyword.strip() or not scope_key:
            return
        record = build_availability_record(
            platform,
            products,
            promotion_id=promotion_id,
            product_name=keyword.strip(),
        )
        save_availability_cache(self.store, monitor_id, platform, scope_key, keyword.strip(), record)

    def _binding_still_matches(self, monitor_id: str, platform: str, scope_key: str) -> bool:
        if self.stopping.is_set():
            return False
        with self.store.connection() as conn:
            current = _room_online_binding(conn, monitor_id)
        if current is None:
            return False
        return current == (platform, scope_key)

    def _run_batch(
        self,
        monitor_id: str,
        session_id: str,
        platform: str,
        scope_key: str,
        items: list[tuple[str, str]],
    ) -> None:
        key = (monitor_id, session_id)
        checked = 0
        total = len(items)
        with self.lock:
            self.progress[key] = {"state": "running", "checked": 0, "total": total, "error": "", "error_code": ""}
        try:
            for index, (promotion_id, product_name) in enumerate(items):
                if index > 0 and self.request_interval > 0:
                    if self.stopping.wait(self.request_interval):
                        raise ProductPlatformError("应用已停止库存检测")
                if not self._binding_still_matches(monitor_id, platform, scope_key):
                    raise ProductPlatformError("直播间平台绑定或账号范围已变化")
                try:
                    check_online_product(
                        self.store,
                        self.product_platform,
                        self.xingdongli,
                        monitor_id,
                        platform,
                        scope_key,
                        promotion_id=promotion_id,
                        product_name=product_name,
                    )
                except ProductPlatformError as exc:
                    if exc.status in (401, 403, 409, 429) or exc.code in (
                        "PRODUCT_PLATFORM_AUTH_REQUIRED",
                        "XINGDONGLI_AUTH_REQUIRED",
                    ):
                        raise
                    save_availability_cache(
                        self.store,
                        monitor_id,
                        platform,
                        scope_key,
                        product_name,
                        {
                            "platform": platform,
                            "state": "error",
                            "match_kind": None,
                            "stock": None,
                            "matched_title": None,
                            "platform_product_id": None,
                            "checked_at": time.time(),
                            "error": str(exc),
                        },
                    )
                checked += 1
                with self.lock:
                    self.progress[key] = {
                        "state": "running",
                        "checked": checked,
                        "total": total,
                        "error": "",
                        "error_code": "",
                    }
            with self.lock:
                self.progress.pop(key, None)
        except ProductPlatformError as exc:
            with self.lock:
                self.progress[key] = {
                    "state": "failed",
                    "checked": checked,
                    "total": total,
                    "error": str(exc),
                    "error_code": str(exc.code or ""),
                }
        except Exception as exc:
            logger.warning("直播场次商品库存检测异常 (%s)", type(exc).__name__)
            with self.lock:
                self.progress[key] = {
                    "state": "failed",
                    "checked": checked,
                    "total": total,
                    "error": "商品库存检测失败，请重试",
                    "error_code": "",
                }


class LiveProductAvailabilityWatcher:
    """Non-blocking single-product lookup triggered only when a recording receives a new lecture product."""

    def __init__(self, store, product_platform: ProductPlatformService | None = None,
                 xingdongli: XingdongliService | None = None):
        self.store = store
        self.product_platform = product_platform or ProductPlatformService(store)
        self.xingdongli = xingdongli or XingdongliService(store)
        self.lock = threading.Lock()
        self.in_flight: set[tuple[str, str]] = set()
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-platform-stock")

    def schedule(self, monitor_id: str, session_id: str, promotion_id: str, product_name: str) -> bool:
        name = str(product_name or "").strip()
        query_key = search_text(name) if name else ""
        if not query_key:
            return False
        dedupe_key = (monitor_id, query_key)
        with self.lock:
            if self.closed or dedupe_key in self.in_flight:
                return False
            self.in_flight.add(dedupe_key)
        try:
            self.executor.submit(self._run, monitor_id, session_id, str(promotion_id or "").strip(), name, dedupe_key)
            return True
        except RuntimeError:
            with self.lock:
                self.in_flight.discard(dedupe_key)
            return False

    def _run(self, monitor_id: str, session_id: str, promotion_id: str, product_name: str,
             dedupe_key: tuple[str, str]) -> None:
        try:
            with self.store.connection() as conn:
                active = conn.execute(
                    "SELECT 1 FROM live_sessions WHERE id=? AND monitor_id=? AND status='recording'",
                    (session_id, monitor_id),
                ).fetchone()
                if active is None:
                    return
                binding = _room_online_binding(conn, monitor_id)
            if binding is None:
                return
            platform, scope_key = binding
            if not scope_key:
                return
            if platform == "xingdongli":
                if not self.xingdongli.status()["logged_in"]:
                    return
            elif platform == "xingdou":
                if not self.product_platform.status()["logged_in"]:
                    return
            try:
                check_online_product(
                    self.store,
                    self.product_platform,
                    self.xingdongli,
                    monitor_id,
                    platform,
                    scope_key,
                    promotion_id=promotion_id,
                    product_name=product_name,
                )
            except ProductPlatformError as exc:
                logger.debug("录制中自动查询商品库存失败 (%s): %s", platform, exc)
                save_availability_cache(
                    self.store,
                    monitor_id,
                    platform,
                    scope_key,
                    product_name,
                    {
                        "platform": platform,
                        "state": "error",
                        "match_kind": None,
                        "stock": None,
                        "matched_title": None,
                        "platform_product_id": None,
                        "checked_at": time.time(),
                        "error": str(exc),
                    },
                )
        except Exception as exc:
            logger.debug("录制中自动查询商品库存异常 (%s)", type(exc).__name__)
        finally:
            with self.lock:
                self.in_flight.discard(dedupe_key)

    def close(self) -> None:
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
