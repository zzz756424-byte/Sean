# SerpApi 查價設定

在 GitHub 儲存庫的 **Settings → Secrets and variables → Actions** 新增 `SERPAPI_API_KEY`。本機 PowerShell 可用 `$env:SERPAPI_API_KEY = '你的 API key'` 設定。金鑰只放環境變數或 GitHub Secret。

## 機票歷史價格比較

航班報價透過 fast-flights 取得。成功查價後，程式另用 SerpApi Google Flights 取得 `price_insights`，補充 Google 的價格比較。可用 `python -m flightseeker --limit 1` 試查。

若遇到憑證問題，先確認已執行 `python -m pip install -r requirements.txt`。公司網路使用自訂 CA 時，可設定 `SERPAPI_CA_BUNDLE` 指向經 IT 核准的 PEM 憑證。

## 指定飯店查價

在 `routes.yaml` 的 `hotels` 清單填寫 `name` 或 `property_token`、`check_in` 和 `check_out`。可為同一飯店設定多組日期。成人數和幣別預設沿用 `settings`，每筆可覆寫 `adults`、`currency` 或 `top`。可用 `search_name` 指定英文搜尋名稱。

本機執行 `python -m flightseeker.hotels`。GitHub Actions 可手動執行 **HotelSeeker**，也會在台灣時間每天 08:00、20:00 排程查價。

`providers: [Agoda, Booking.com]` 會篩選 SerpApi 回傳的平台，名稱不分大小寫。省略或設定空清單會顯示所有平台。結果按全程總價排序，並顯示每晚價格、稅費標示和訂房連結。可用 `hotels_com_tw_url` 指定 Hotels.com 台灣站的住宿頁。

程式將各平台的最低全程總價寫入 `data/hotel_history.json`，並顯示相同查詢條件和稅費口徑下前 30 天的最低價、最高價及筆數。GitHub Actions 透過快取延續紀錄。若 Google Hotels 提供常態價格區間，也會顯示。

`notify_drop: 500` 表示本次全程總價比前 30 天最低價至少低 500（以查詢幣別計）才通知。`notify_drop: null` 表示追平或低於前 30 天最低價就通知。首次查價只建立紀錄。符合門檻時，終端會在輸出最後列出通知，GitHub Actions 會建立 Issue。省略欄位可停用。
