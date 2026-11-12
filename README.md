# ✈️ FlightSeeker 機票監控

> 最後執行：2026-11-12 10:50（台北時間）　本次查詢 3 組：成功 1、查無航班 2、錯誤 0
>
> 搜尋條件版本：2026-11-12 10:49 修改的 routes.yaml（[021537b](https://github.com/zzz756424-byte/Sean/commit/021537b9dcaf50717f45ad9232794abf77ffbe47) Update routes.yaml）

## 台中/台北 → 沖繩

RMQ → OKA，2027/03/20–2027/03/27，7/8 天，限直飛，低於自動低價線會通知（最近 30 天價格的第 0 百分位，紀錄不足時用 20,000）

已查過 3 / 3 組日期 × 機場組合（每次執行輪流查最久沒更新的組合）。

<details><summary>各航線的通知門檻</summary>

| 航線 | 低於多少會通知 | 來源 |
|---|---:|---|
| RMQ→OKA | 20,000 | 紀錄不足 |

</details>

### 目前最便宜 1 組

| 去 → 回 | 機場 | 航空公司 | 轉機 | 去程時間 | 票價 | 行李 | 估算總價 | 直飛最低 | 更新 | 連結 |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| 3/20(六) → 3/26(五) | RMQ→OKA | STARLUX Airlines | 直飛 | 13:15→15:45 | 12,021 | 已含 | **12,021** 🔥 | 同左 | 11/12 10:50 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjAoAGoFEgNSTVFyBRIDT0tBGhwSCjIwMjctMDMtMjYoAGoFEgNPS0FyBRIDUk1RQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |

---

- **票價**：Google Flights 顯示的來回總價（去程最便宜選項 + 搭配的回程）
- **直飛最低**：同一組日期中最便宜的直飛選項（也已含行李估算）。只想看直飛，把 `routes.yaml` 的 `max_stops` 改成 0
- **行李**：`routes.yaml` 的 `budget_airlines` 名單內的廉航，會加上 `bag_estimate` 估算；其他航空公司視為已含托運
- **改條件**：編輯 [`routes.yaml`](routes.yaml)；**改頻率**：編輯 [`.github/workflows/flightseeker.yml`](.github/workflows/flightseeker.yml) 的 `cron`
- **立刻查一次**：Actions → FlightSeeker → Run workflow

## LINE 查機票

### 用 PyCharm 測試

在 PyCharm 用專案的 Python 直譯器執行根目錄的 `try_line_flights.py`。將下方範例整段貼到 Run 視窗，最後送出一個空白行。結果會印在 Run 視窗。首次執行前請用同一個直譯器執行 `python -m pip install -r requirements.txt`。本機測試不需要 LINE 憑證。

在 LINE 官方帳號聊天室貼上一則多行訊息。只輸入「查機票」會收到範例。

```text
查機票
去程地點:台中/台北
回程地點:沖繩
最早去程時間:2027/4/10
最晚回程時間:2027/4/17
最短遊玩天數:7
```

「回程地點」是回程出發地。輸入城市或地區會選一座主要機場，例如沖繩選那霸（OKA），台北選桃園（TPE）。要指定機場，可填那霸、松山等已收錄別名、機場英文全名或三碼 IATA 代碼。全球索引取自 [OurAirports](https://ourairports.com/data/)，收錄有 IATA 代碼且未關閉的機場。城市或地區以有定期航班、機場規模、IATA 代碼依序決定。遊玩天數包含抵達目的地當天和離開目的地當天。每次最多接受 30 組機場和日期查詢。結果只列出同一家航空公司的直飛去回程組合，估算總價為 Google Flights 查到的兩段單程票價之和（1 位成人、新台幣）。相同去回日期跨機場比價，只保留最低價的 5 組，再將所有入選結果依總價由低到高排序。訊息過長時會分則傳送。

### 透過 GitHub Actions 查詢並推播回 LINE

LINE 將聊天室訊息送到 HTTPS Webhook。Webhook 驗證 LINE 簽章並觸發 **LINE Flight Search** 工作流程。GitHub Actions 執行 Google Flights 查詢，再將結果推播回收到指令的私人聊天室或群組。

1. 將程式與 `.github/workflows/LineFlightSearch.yml` 放到 GitHub repo 的預設分支。在 repo 的 **Settings → Secrets and variables → Actions** 新增 `LINE_CHANNEL_ACCESS_TOKEN` secret，可沿用原票價推播使用的 token。
2. 建立只授權該 repo 的 fine-grained GitHub token，給予 **Actions: Read and write**。在 HTTPS Webhook 主機設定 `GITHUB_DISPATCH_TOKEN`（GitHub token）、`GITHUB_REPOSITORY`（`擁有者/儲存庫`）和 `LINE_CHANNEL_SECRET`（LINE Channel Secret）。分支預設為 `main`，可用 `GITHUB_DISPATCH_REF` 調整。聊天室 ID 和查詢文字會作為工作流程輸入。
3. 在 Webhook 主機執行以下指令。服務預設監聽 8080，可用 `PORT` 調整。

   ```sh
   python -m pip install -r requirements.txt
   python -m flightseeker.line_webhook
   ```

4. 將公開 HTTPS 網址的 `/line/webhook` 轉發到服務。在 LINE Developers Console 設定 Webhook URL，按 **Verify**，開啟 **Use webhook**。可開啟 Webhook redelivery，讓 LINE 在觸發 Actions 失敗時重送事件。
5. 在 LINE 傳送查詢，從 GitHub 的 **Actions → LINE Flight Search** 檢查執行狀態。工作流程完成後會推播回原聊天室。
