"""查價狀態（每個組合的最新結果）與歷史紀錄的讀寫。

- data/state.json   每個組合最新一次查價結果，排程輪替與報表都靠它
- data/history.csv  每次成功查價都追加一行，之後可以畫價格走勢
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

HISTORY_FIELDS = [
    "checked_at", "route", "origin", "destination", "depart", "ret",
    "fare", "bag_add", "total", "airlines", "stops", "outbound",
    "direct_total", "direct_airlines",
]


def history_flight_key(row: dict) -> tuple[str, ...]:
    """現有 CSV 沒有航班編號，以日期、機場、去程時間、航空公司及轉機數比對。"""
    airlines = row.get("airlines") or []
    if isinstance(airlines, str):
        airlines = airlines.split("/")
    carriers = "/".join(sorted({str(a).strip().casefold() for a in airlines if str(a).strip()}))
    return tuple(str(row.get(k, "")).strip() for k in
                 ("origin", "destination", "depart", "ret", "outbound", "stops")) + (carriers,)


def history_price_ranges(rows: list[dict]) -> dict[tuple[str, ...], tuple[int, int, int]]:
    """彙整既有紀錄的含行李總價：最低、最高、筆數。"""
    ranges = {}
    for row in rows:
        try:
            price = int(row.get("total", ""))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        key = history_flight_key(row)
        low, high, count = ranges.get(key, (price, price, 0))
        ranges[key] = (min(low, price), max(high, price), count + 1)
    return ranges


def load_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(state.items(), key=lambda kv: (kv[1].get("depart", ""), kv[0])))
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def append_history(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # 欄位有增減時，先把舊檔改寫成新欄位格式
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != HISTORY_FIELDS:
                old_rows = list(reader)
                with path.open("w", encoding="utf-8", newline="") as out:
                    writer = csv.DictWriter(out, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(old_rows)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerows(rows)
