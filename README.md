# ✈️ FlightSeeker 機票監控

> 最後執行：2026-11-13 15:07（台北時間）　本次查詢 3 組：成功 1、查無航班 1、錯誤 1
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
| 3/20(六) → 3/26(五) | RMQ→OKA | STARLUX Airlines | 直飛 | 13:15→15:45 | 12,011 | 已含 | **12,011** 🔥 | 同左 | 11/13 15:07 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjAoAGoFEgNSTVFyBRIDT0tBGhwSCjIwMjctMDMtMjYoAGoFEgNPS0FyBRIDUk1RQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |

---

- **票價**：Google Flights 顯示的來回總價（去程最便宜選項 + 搭配的回程）
- **直飛最低**：同一組日期中最便宜的直飛選項（也已含行李估算）。只想看直飛，把 `routes.yaml` 的 `max_stops` 改成 0
- **行李**：`routes.yaml` 的 `budget_airlines` 名單內的廉航，會加上 `bag_estimate` 估算；其他航空公司視為已含托運
- **改條件**：編輯 [`routes.yaml`](routes.yaml)；**改頻率**：編輯 [`.github/workflows/flightseeker.yml`](.github/workflows/flightseeker.yml) 的 `cron`
- **立刻查一次**：Actions → FlightSeeker → Run workflow
