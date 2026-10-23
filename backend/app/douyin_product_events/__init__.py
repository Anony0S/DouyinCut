"""Python protocol primitives for Douyin live product-change events."""

from .client import DouyinProductEventClient
from .protocol import (
    ProductChangeEvent,
    decode_push_frame,
    parse_live_pop_product_id,
    parse_live_shopping_product,
    parse_product_change,
)
from .signing import (
    WebSocketSignatureGenerator,
    sign_stub_with_values,
    websocket_signature_stub,
)
from .session import (
    DouyinWebcastSessionPreparer,
    InitialIMState,
    PreparedWebSocketSession,
    RoomInfo,
    build_initial_im_query,
    build_room_enter_query,
    build_websocket_url,
    live_id_from_url,
    normalize_push_url,
    parse_initial_im_response,
    parse_room_enter_response,
)

__all__ = [
    "DouyinProductEventClient",
    "ProductChangeEvent",
    "decode_push_frame",
    "parse_live_pop_product_id",
    "parse_live_shopping_product",
    "parse_product_change",
    "WebSocketSignatureGenerator",
    "sign_stub_with_values",
    "websocket_signature_stub",
    "DouyinWebcastSessionPreparer",
    "InitialIMState",
    "PreparedWebSocketSession",
    "RoomInfo",
    "build_initial_im_query",
    "build_room_enter_query",
    "build_websocket_url",
    "live_id_from_url",
    "normalize_push_url",
    "parse_initial_im_response",
    "parse_room_enter_response",
]
