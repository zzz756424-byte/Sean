# SerpApi 歷史價格比較

航班報價仍透過 fast-flights 取得；每組成功查價後，另外呼叫一次 SerpApi Google Flights，讀取 `price_insights`。這會使用 SerpApi 查詢額度，沒有設定金鑰時不呼叫。

## 設定

GitHub：在儲存庫 Settings → Secrets and variables → Actions 新增 repository secret，名稱為 `SERPAPI_API_KEY`，值為你的 SerpApi API key。Workflow 已接上此 secret。

本機 PowerShell：

```powershell
$env:SERPAPI_API_KEY = '你的 API key'
python -m flightseeker --limit 1
```

金鑰只放環境變數或 GitHub Secret，不放進 routes.yaml。先以一組查詢確認額度與回傳資料。

## 輸出與儲存

成功查價的 console 會附上價格等級、常態價格區間，以及回傳歷史序列的最低價與筆數（欄位存在時）。沒有資訊或 API 失敗時顯示原因，保留原本航班報價。

完整 `price_insights` 儲存在 `data/state.json` 各組合內，包含 `price_history` 的 Unix timestamp／價格配對。每次成功查價會更新該組合的快照；不會把外部歷史序列寫入自行累積的 `data/history.csv`。

請求會帶入機場、去回日期、成人數、經濟艙、幣別、航空公司與轉機限制。比較資訊屬於 Google 搜尋整體，不保證是特定航班的歷史；也未加上程式估算的行李費。歷史涵蓋範圍由 Google／SerpApi 決定，缺值不代表沒有歷史報價。現有通知門檻算法不變。

文件：https://serpapi.com/google-flights-price-insights

## SSL 憑證驗證失敗

SerpApi 連線使用 truststore 讀取系統憑證庫，仍驗證憑證及主機名稱。請先在 PyCharm 專案使用的 Python 環境安裝更新後的套件：

```powershell
python -m pip install -r requirements.txt
```

若仍失敗且在公司網路，請向 IT 取得核准的根／中繼 CA 憑證（PEM 格式），在 PyCharm Run → Edit Configurations → Environment variables 新增 `SERPAPI_CA_BUNDLE`，值為憑證檔案的完整路徑（不加引號）。該憑證會加入系統信任來源。不要使用停用 SSL 驗證的方式。
