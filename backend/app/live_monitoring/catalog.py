"""Fetch room catalogs only for missing data; never poll the remote catalog."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor


CATALOG_REFRESH_INTERVAL = 60
MISSING_PRODUCT_COOLDOWN = 300
# A room with no catalog yet keeps retrying on the normal interval; without a cap a
# permanently failing room would request the catalog every minute for the whole stream.
INITIAL_ATTEMPT_LIMIT = 3


class ProductCatalogRefresher:
    def __init__(self, live, monitor_id: str, session_id: str, resolver,
                 stop_event: threading.Event, *, force: bool = False, clock=None):
        self.live, self.monitor_id, self.session_id = live, monitor_id, session_id
        self.resolver, self.stop_event = resolver, stop_event
        self.clock = clock or time.time
        state = live.product_catalog_state(monitor_id)
        self.initial = state["fetched"] is None
        self.initial_attempts = 0
        self.last_attempt = state["attempted"]
        self.completed_at = None
        self.misses = state["misses"]
        self.pending: set[str] = set()
        self.forced = force
        self.retrying = force
        self.running = False
        self.closed = False
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-product-catalog")
        self.future = None

    def request_missing(self, promotion_id: str):
        # A catalog response may have arrived since the message handler's lookup.
        name = self.live.product_name(self.monitor_id, promotion_id)
        if name:
            self.live.update_product_name(self.session_id, promotion_id, name)
            return
        with self.lock:
            if self.clock() >= self.misses.get(promotion_id, 0):
                self.pending.add(promotion_id)
        self.tick()

    def request_refresh(self, *, requested_at: float | None = None):
        with self.lock:
            # The worker may consume a retry after the refresh it joined has finished.
            if requested_at is not None and self.completed_at is not None and self.completed_at >= requested_at:
                self.live.finish_product_listener_retry(self.session_id)
                return
            self.retrying = True
            # A manual retry joins an in-flight refresh instead of submitting another.
            if not self.running:
                self.forced = True
        self.tick()

    def on_listening(self):
        with self.lock:
            status = "retrying" if self.retrying and self.running else "listening"
            detail = "商品消息连接已建立，正在刷新商品列表" if status == "retrying" else "商品消息连接已建立"
            self.live.set_product_listener_state(self.session_id, status, detail)

    def tick(self):
        """Called by the listener loop; time alone never creates a refresh request."""
        with self.lock:
            if self.closed or self.stop_event.is_set() or self.running:
                return
            if not (self.initial or self.forced or self.pending):
                return
            now = self.clock()
            if not self.forced and self.last_attempt is not None \
                    and now < self.last_attempt + CATALOG_REFRESH_INTERVAL:
                return
            self.live.mark_product_catalog_attempt(self.monitor_id, now)
            include_current = self.initial
            self.initial = self.forced = False
            self.last_attempt, self.running = now, True
            self.future = self.executor.submit(self._refresh, include_current, now)

    def _refresh(self, include_current: bool, requested_at: float):
        names, succeeded = {}, False
        try:
            names, current = self.resolver.lookup_catalog()
            if self.stop_event.is_set():
                return
            self.live.replace_monitor_products(self.monitor_id, names)
            self.live.set_product_catalog_error(self.session_id, None)
            if include_current and current is not None:
                promotion_id, title = current
                self.live.set_current_product(
                    self.session_id, promotion_id=promotion_id,
                    product_name=title or names.get(promotion_id, ""),
                    event_time=requested_at, received_at=requested_at,
                )
            succeeded = True
        except Exception as exc:
            if not self.stop_event.is_set():
                detail = getattr(exc, "detail", None) or {
                    "summary": "抖音商品列表获取失败", "stage": "catalog_refresh",
                    "reason": f"{type(exc).__name__}: {str(exc)[:1000]}",
                }
                self.live.set_product_catalog_error(self.session_id, detail)
                logging.getLogger(__name__).debug("直播间商品列表刷新失败 (%s)", type(exc).__name__)
        finally:
            try:
                if not self.stop_event.is_set():
                    with self.lock:
                        # Include IDs that arrived during HTTP pagination in this result.
                        pending, self.pending = self.pending, set()
                        missing = pending.difference(names) if succeeded else pending
                        retry_at = self.clock() + (
                            MISSING_PRODUCT_COOLDOWN if succeeded else CATALOG_REFRESH_INTERVAL
                        )
                        self.misses.update({product_id: retry_at for product_id in missing})
                        for product_id in names if succeeded else ():
                            self.misses.pop(product_id, None)
                        if succeeded:
                            self.initial_attempts = 0
                        elif include_current and self.initial_attempts + 1 < INITIAL_ATTEMPT_LIMIT:
                            # The room still has no usable catalog; the normal interval
                            # keeps the retry from becoming a tight loop.
                            self.initial_attempts += 1
                            self.initial = True
                    # Database writes stay outside the lock so close() is never blocked
                    # behind a slow SQLite call.
                    self.live.defer_missing_products(self.monitor_id, missing, retry_at)
                    for product_id in pending.difference(missing):
                        self.live.update_product_name(self.session_id, product_id, names[product_id])
            finally:
                with self.lock:
                    if self.retrying and not self.stop_event.is_set():
                        self.live.finish_product_listener_retry(self.session_id)
                    self.retrying = False
                    self.running = False
                    self.completed_at = self.clock()

    def close(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
