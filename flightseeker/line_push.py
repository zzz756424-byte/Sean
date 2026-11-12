"""Send an existing price alert to LINE."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

path = Path(os.environ["LINE_ALERTS_FILE"])
if os.environ.get("LINE_TEST") == "true":
    alerts = "HotelSeeker LINE 推播測試"
else:
    alerts = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
if not alerts:
    print("本次沒有符合通知條件的報價，未送出 LINE 推播")
    raise SystemExit(0)

token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip()
user_id = os.environ["LINE_USER_ID"].strip()
if not token or not user_id:
    raise SystemExit("請設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_USER_ID")

message = alerts
if len(message.encode("utf-16-le")) > 10000:
    suffix = "\n…其餘報價請查看 GitHub 執行結果"
    budget = 10000 - len(suffix.encode("utf-16-le"))
    alerts = alerts.encode("utf-16-le")[:budget].decode("utf-16-le", errors="ignore")
    message = alerts + suffix
payload = {"to": user_id, "messages": [{"type": "text", "text": message}]}
request = Request(
    "https://api.line.me/v2/bot/message/push",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urlopen(request, timeout=30) as response:
        print(f"LINE API 已接受推播（HTTP {response.status}）")
except HTTPError as exc:
    hints = {
        400: "檢查 User ID 是否來自同一個 LINE Provider",
        401: "檢查 Channel access token 是否有效",
        403: "檢查 Messaging API Channel 的權限",
        429: "檢查 LINE 訊息額度或請求頻率",
    }
    hint = hints.get(exc.code, "請稍後重試或檢查 LINE API 狀態")
    raise SystemExit(f"LINE 推播失敗（HTTP {exc.code}）：{hint}") from None
except (URLError, TimeoutError):
    raise SystemExit("LINE 推播連線失敗") from None
