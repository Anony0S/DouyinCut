import json
import logging
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..core.storage import SessionStopping
from .client import ProductPlatformError


ORIGIN = "https://qiepian.daduoduo.com"
PAGE_SIZE = 50
MAX_PAGES = 1000
logger = logging.getLogger(__name__)


def fetch_page(client, ip_id: str, page: int):
    try:
        client.cookies.clear()
        response = client.post("/ajax/DddClip/cutterAjax.ashx", params={
            "action": "GoodsListForPreview", "ipId": ip_id, "pageSize": PAGE_SIZE, "pageIndex": page,
        })
        if response.status_code in (401, 403) or response.is_redirect:
            raise ProductPlatformError("达多多拒绝访问，请检查直播间 ipId",
                                       code="DADUODUO_ACCESS_DENIED", status=403)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, UnicodeError, ValueError):
        raise ProductPlatformError("达多多商品列表请求失败，请检查网络或稍后重试") from None
    if not isinstance(body, dict) or body.get("code") not in (0, "0"):
        raise ProductPlatformError("达多多未返回商品列表，请检查直播间 ipId")
    data = body.get("data")
    total = data.get("total") if isinstance(data, dict) else None
    rows = data.get("data") if isinstance(data, dict) else None
    if isinstance(total, str) and total.isascii() and total.isdecimal() and len(total) <= 9:
        total = int(total)
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise ProductPlatformError("达多多商品分页格式无效")
    return total, rows


def normalize_product(row):
    goods = row.get("goodsInfo") if isinstance(row, dict) else None
    if not isinstance(goods, dict):
        raise ProductPlatformError("达多多返回了无效商品记录")
    product_id, title = row.get("goodsId"), goods.get("GoodName")
    if type(product_id) not in (str, int) or not str(product_id).strip() or not isinstance(title, str) or not title.strip():
        raise ProductPlatformError("达多多商品记录缺少商品 ID 或名称")
    product_id = str(product_id).strip()
    price = goods.get("SellPrice")
    product = {
        "id": product_id, "platform_product_id": product_id, "title": title.strip(),
        "price": price if type(price) in (int, float) else None,
        "cover": goods.get("GoodPic"), "shop_name": goods.get("ShopName"),
        "stock": goods.get("ProductStock"), "month_sales": goods.get("SaleCntLast30D"),
        "yesterday_sales": goods.get("SaleCntLast1D"), "total_sales": row.get("TotalSaleCnt"),
        "listing_time": goods.get("CoopBeginTime"),
        "last_explained_at": goods.get("LiveBeginTime"),
    }
    return (product_id, str(row.get("CoopGoodsId") or row.get("SysNo") or "")), product


def search_text(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


class DaduoduoCatalog:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.progress = {}
        self.stopping = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daduoduo-catalog")

    def close(self):
        self.stopping.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _rooms(self):
        with self.store.connection() as conn:
            return conn.execute(
                "SELECT m.id,m.name,m.url,p.binding_id FROM live_room_platforms p "
                "JOIN live_monitors m ON m.id=p.monitor_id WHERE json_extract(p.body,'$.platform')='daduoduo' "
                "ORDER BY m.created"
            ).fetchall()

    def settings(self):
        return {"rooms": [{"id": row["id"], "name": row["name"] or row["url"],
                           **self._status(row["binding_id"])} for row in self._rooms()]}

    def _room(self, monitor_id):
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT binding_id,json_extract(body,'$.ip_id') AS ip_id FROM live_room_platforms "
                "WHERE monitor_id=? AND json_extract(body,'$.platform')='daduoduo'", (monitor_id,)
            ).fetchone()
        if row is None:
            raise ProductPlatformError("该直播间未绑定达多多", status=409)
        return row["binding_id"], row["ip_id"]

    def start(self):
        for room in self._rooms():
            try:
                self.update(room["id"])
            except ProductPlatformError:
                continue
            except SessionStopping:
                break

    def status(self, monitor_id):
        binding_id, _ = self._room(monitor_id)
        return self._status(binding_id)

    def _status(self, binding_id):
        with self.lock, self.store.connection() as conn:
            state = self.progress.get(binding_id, {"state": "idle"})
            saved = conn.execute("SELECT json_array_length(products) AS count,synced_at "
                                 "FROM daduoduo_catalogs WHERE binding_id=?", (binding_id,)).fetchone()
        return {"downloaded": 0, "total": None, "error": "", **state,
                "count": saved["count"] if saved else 0, "synced_at": saved["synced_at"] if saved else None}

    def update(self, monitor_id):
        binding_id, ip_id = self._room(monitor_id)
        with self.lock:
            if self.stopping.is_set() or self.store.is_shutting_down():
                raise SessionStopping("应用正在退出")
            if self.progress.get(binding_id, {}).get("state") not in ("queued", "running"):
                self.progress[binding_id] = {"state": "queued"}
                self.executor.submit(self._sync, binding_id, ip_id)
        return self._status(binding_id)

    def _sync(self, binding_id, ip_id):
        products = []
        seen = set()
        expected = None
        try:
            with httpx.Client(base_url=ORIGIN, timeout=12, follow_redirects=False) as client:
                for page in range(1, MAX_PAGES + 1):
                    with self.lock, self.store.connection() as conn:
                        self._assert_current(conn, binding_id)
                    total, rows = fetch_page(client, ip_id, page)
                    if expected is None:
                        expected = total
                    # Later pages can carry total=0 even when they contain products.
                    elif total not in (0, expected):
                        raise ProductPlatformError("达多多商品总数在分页期间变化，请重新更新")
                    received = len(products) + len(rows)
                    if received > expected or (not rows and received < expected):
                        raise ProductPlatformError(
                            f"达多多第 {page} 页返回 {len(rows)} 条，累计 {received} 条，与首页总数 {expected} 条不一致"
                        )
                    for row in rows:
                        identity, product = normalize_product(row)
                        if identity in seen:
                            raise ProductPlatformError("达多多返回了重复的分页商品，请重新更新")
                        seen.add(identity)
                        products.append(product)
                    with self.lock:
                        self.progress[binding_id] = {"state": "running", "downloaded": received, "total": expected}
                    if received == expected:
                        break
                else:
                    raise ProductPlatformError("达多多商品目录超过同步页数上限")
            with self.lock:
                with self.store.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._assert_current(conn, binding_id)
                    conn.execute("INSERT INTO daduoduo_catalogs VALUES (?,?,?) "
                                 "ON CONFLICT(binding_id) DO UPDATE SET products=excluded.products,synced_at=excluded.synced_at",
                                 (binding_id, json.dumps(products, ensure_ascii=False), time.time()))
                self.progress.pop(binding_id, None)
        except ProductPlatformError as exc:
            with self.lock:
                self.progress[binding_id] = {"state": "failed", "error": str(exc)}
        except Exception as exc:
            logger.warning("达多多目录同步异常 (%s)", type(exc).__name__)
            with self.lock:
                self.progress[binding_id] = {"state": "failed", "error": "商品目录更新失败，请重试"}

    def _assert_current(self, conn, binding_id):
        if self.stopping.is_set():
            raise ProductPlatformError("应用已停止目录更新")
        if not conn.execute("SELECT 1 FROM live_room_platforms WHERE binding_id=?", (binding_id,)).fetchone():
            raise ProductPlatformError("直播间平台绑定已修改")

    def search(self, monitor_id, keyword):
        binding_id, _ = self._room(monitor_id)
        with self.store.connection() as conn:
            saved = conn.execute("SELECT products FROM daduoduo_catalogs WHERE binding_id=?", (binding_id,)).fetchone()
        if saved is None:
            raise ProductPlatformError("商品列表尚未同步，请在直播间点击更新商品", status=409)
        query = search_text(keyword)
        products = json.loads(saved["products"])
        matches = [(search_text(item["title"]), item) for item in products]
        product = next((item for title, item in matches if title == query), None)
        if product is None:
            product = next((item for title, item in matches if query in title), None)
        return {"product": product, "scope_name": "", "can_add_to_showcase": False}
