# Douyin Product Events

This standalone Python module parses Douyin WebSocket `PushFrame` / `Response`
messages and sends ACK frames. It extracts `WebcastProductChangeMessage` fields
and, in the live monitor, treats a changed `WebcastLiveShoppingMessage`
product ID as a product event only when a matching `LivePopMessage` confirms
it within five seconds. The first observed product ID in each session is only
a baseline. The live monitor uses this single Python WebSocket connection as
the production event source; it does not start the Go bridge or a second
shadow connection. `WebcastProductChangeMessage` remains diagnostic-only until
its event semantics are independently verified.

The module includes:

- An offline WebSocket signature implementation based on `douyinLive` v2.3.1.
- An HTTP `a_bogus` implementation and HTTP requests to resolve a
  live-room URL, read `web/enter` room metadata, and request the initial IM
  state.
- WebSocket URL construction and `DouyinProductEventClient.start_prepared()`
  to pass the prepared URL, cookie, and user-agent to the listener.

The HTTP signature matches a fixed-clock vector from the installed `streamget`
Douyin signer. The room-entry query follows its public field order. The probe
first opens the room page to establish an anonymous HTTP session and accepts
cookies returned by that page; it does not require a user-provided login
cookie. The initial IM request and protobuf fields were verified against a
live Douyin session. The platform may change its signed endpoint or message
schema, so keep offline protocol tests and live diagnostics available.

Preparation errors include the failing stage, HTTP status, content type,
platform request ID when available, and a bounded response preview. Request
query strings are omitted from diagnostics; binary initial-IM responses show a
short hexadecimal prefix.

```python
from app.douyin_product_events import (
    DouyinProductEventClient,
    DouyinWebcastSessionPreparer,
)

preparer = DouyinWebcastSessionPreparer(cookie="", user_agent="...")
try:
    session = preparer.prepare("https://live.douyin.com/ROOM_ID")
finally:
    preparer.close()

client = DouyinProductEventClient(on_event=print, on_state=print)
client.start_prepared(session)
# Later, when stopping the listener:
client.stop()
```

If Douyin requires a logged-in browser session, pass its cookie explicitly.
The default user-agent is a fixed Windows Chrome profile; the module does not
read browser cookies or fingerprint data automatically.

## Live probe

The probe performs real requests and opens a WebSocket, but does not record
video or modify application data. It prints room metadata and product events;
the signed WebSocket query and cookie are not printed. Short-link redirects
are followed only while they stay on HTTPS Douyin domains.

```powershell
$env:DOUYIN_COOKIE = "sessionid=...; msToken=..."
uv run --project backend python -m app.douyin_product_events.probe "https://live.douyin.com/ROOM_ID" --listen-seconds 60
Remove-Item Env:DOUYIN_COOKIE
```

For a public room that works without login, omit `DOUYIN_COOKIE`. A successful
HTTP session preparation does not guarantee that product change events are
delivered. During live recording the monitor starts one Python product
listener. Confirmed LiveShopping changes are written to the official product
event timeline; they do not automatically drive recording cuts. The live
monitor does not collect or store product-message diagnostic samples. The
standalone probe remains available for connection troubleshooting and does
not modify application data. For product-message inspection it reports new
product-related message types with readable nested text fields; it only shows
those diagnostic samples in the terminal and does not save them.

The event schema was cross-checked against public Douyin protobuf declarations:
`ProductChangeMessage.update_timestamp` is field 2,
`update_product_info_list` is field 4, and `ProductInfo` uses fields 1, 2, and 4
for promotion ID, index, and explain type. Product names are not present in this
message.

Run the offline tests from the repository root with:

```powershell
uv run --project backend pytest backend/tests/test_douyin_product_events.py
```
