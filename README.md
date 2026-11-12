# ✈️ FlightSeeker 機票監控

> 最後執行：2026-11-12 10:11（台北時間）　本次查詢 6 組：成功 5、查無航班 0、錯誤 1
>
> 搜尋條件版本：2026-11-11 16:40 修改的 routes.yaml（[b4ddd62](https://github.com/zzz756424-byte/Sean/commit/b4ddd62f9c86a5d5474472bab96ae5eeb69dd4e9) Update routes.yaml）

## 台中/台北 → 首爾

RMQ/TPE → ICN，2027/03/20–2027/03/27，7/8 天，限直飛，低於自動低價線會通知（最近 30 天價格的第 0 百分位，紀錄不足時用 20,000）

已查過 6 / 6 組日期 × 機場組合（每次執行輪流查最久沒更新的組合）。

<details><summary>各航線的通知門檻</summary>

| 航線 | 低於多少會通知 | 來源 |
|---|---:|---|
| RMQ→ICN | 9,493 | 自動 |
| TPE→ICN | 9,512 | 自動 |

</details>

### 目前最便宜 6 組

| 去 → 回 | 機場 | 航空公司 | 轉機 | 去程時間 | 票價 | 行李 | 估算總價 | 直飛最低 | 更新 | 連結 |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| 3/20(六) → 3/26(五) | TPE→ICN | Korean Air | 直飛 | 12:25→15:50 | 10,438 | 已含 | 10,438 | 同左 | 11/12 10:11 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjAoAGoFEgNUUEVyBRIDSUNOGhwSCjIwMjctMDMtMjYoAGoFEgNJQ05yBRIDVFBFQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |
| 3/20(六) → 3/27(六) | TPE→ICN | Korean Air | 直飛 | 12:25→15:50 | 10,438 | 已含 | 10,438 | 同左 | 11/12 10:11 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjAoAGoFEgNUUEVyBRIDSUNOGhwSCjIwMjctMDMtMjcoAGoFEgNJQ05yBRIDVFBFQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |
| 3/20(六) → 3/26(五) | RMQ→ICN | Jin Air | 直飛 | 11:00→14:40 | 9,726 | +2,000 | 11,726 | 同左 | 11/12 10:11 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjAoAGoFEgNSTVFyBRIDSUNOGhwSCjIwMjctMDMtMjYoAGoFEgNJQ05yBRIDUk1RQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |
| 3/20(六) → 3/27(六) | RMQ→ICN | Jin Air | 直飛 | 11:00→14:40 | 9,726 | +2,000 | 11,726 | 同左 | 11/12 10:11 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjAoAGoFEgNSTVFyBRIDSUNOGhwSCjIwMjctMDMtMjcoAGoFEgNJQ05yBRIDUk1RQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |
| 3/21(日) → 3/27(六) | RMQ→ICN | Jin Air | 直飛 | 11:00→14:40 | 9,726 | +2,000 | 11,726 | 同左 | 11/12 10:11 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjEoAGoFEgNSTVFyBRIDSUNOGhwSCjIwMjctMDMtMjcoAGoFEgNJQ05yBRIDUk1RQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |
| 3/21(日) → 3/27(六) | TPE→ICN | Korean Air | 直飛 | 11:05→14:40 | 12,281 | 已含 | 12,281 | 同左 | 11/12 10:11 | [查看](https://www.google.com/travel/flights/search?tfs=GhwSCjIwMjctMDMtMjEoAGoFEgNUUEVyBRIDSUNOGhwSCjIwMjctMDMtMjcoAGoFEgNJQ05yBRIDVFBFQgEBSAGIAQGYAQE=&hl=zh-TW&curr=TWD) |

---

- **票價**：Google Flights 顯示的來回總價（去程最便宜選項 + 搭配的回程）
- **直飛最低**：同一組日期中最便宜的直飛選項（也已含行李估算）。只想看直飛，把 `routes.yaml` 的 `max_stops` 改成 0
- **行李**：`routes.yaml` 的 `budget_airlines` 名單內的廉航，會加上 `bag_estimate` 估算；其他航空公司視為已含托運
- **改條件**：編輯 [`routes.yaml`](routes.yaml)；**改頻率**：編輯 [`.github/workflows/flightseeker.yml`](.github/workflows/flightseeker.yml) 的 `cron`
- **立刻查一次**：Actions → FlightSeeker → Run workflow
