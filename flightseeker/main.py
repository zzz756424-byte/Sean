"""一次排程執行：挑出最久沒查的組合 → 查價 → 更新狀態、報表、通知。"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .combos import Combo, build_combos
from .config import Config, Route, alert_threshold, auto_thresholds, config_version, load_config
from .fetch import NO_PRICE_INSIGHTS, EmptyResult, Fetcher, NoFlights, google_fetcher, serpapi_price_insights
from .report import build_deals, build_readme
from .store import append_history, history_flight_key, history_price_ranges, load_history, load_state, save_state

TZ = ZoneInfo("Asia/Taipei")
MAX_CONSECUTIVE_ERRORS = 5


class SuspectedBlock(Exception):
    """上次還查得到價格，這次卻是空結果，多半是被 Google 暫時擋住。"""


def fetch_checked(fetcher: Fetcher, combo: Combo, route: Route, settings, entry: dict):
    """空結果時，若這組上次還有價格，先當成錯誤（保留舊價格）；連續兩次空結果才算查無航班。"""
    try:
        return fetcher(combo, route, settings)
    except EmptyResult as exc:
        if entry.get("status") == "ok" and not entry.get("suspect_none"):
            entry["suspect_none"] = True
            raise SuspectedBlock("查無航班，但上次有價格，可能被 Google 暫時擋住") from exc
        raise


def pick_combos(combos: dict[str, Combo], state: dict[str, dict], limit: int,
                weights: dict[str, int] | None = None) -> list[Combo]:
    """各航線依 weight 輪流挑，避免組合多的航線把名額吃光。

    同一條航線內：優先查從沒查過的，再來是最久沒更新的；同樣情況下日期近的先查。
    """
    def order(c: Combo):
        return (state.get(c.key, {}).get("checked_at") or "", c.depart, c.key)

    queues: dict[str, list[Combo]] = {}
    for c in sorted(combos.values(), key=order):
        queues.setdefault(c.route, []).append(c)
    picked: list[Combo] = []
    while len(picked) < limit and any(queues.values()):
        for name, q in queues.items():
            take = min((weights or {}).get(name, 1), len(q), limit - len(picked))
            picked += q[:take]
            del q[:take]
    return picked


def cached_price_insights(combo, route, settings, entry, now):
    search_settings = [settings.currency, settings.adults]
    cache = entry.get("serpapi_cache") or {}
    if cache.get("search_settings") == search_settings:
        checked_at = dt.datetime.fromisoformat(cache["checked_at"])
        if dt.timedelta(0) <= now - checked_at < dt.timedelta(days=10):
            return cache["comparison"], cache["insights"]

    try:
        comparison, insights = serpapi_price_insights(combo, route, settings)
    except Exception:
        return "SerpApi 資料解析失敗", {}
    if insights or comparison == NO_PRICE_INSIGHTS:
        entry["serpapi_cache"] = {
            "checked_at": now.isoformat(),
            "search_settings": search_settings,
            "comparison": comparison,
            "insights": insights,
        }
    return comparison, insights


def run(root: Path, fetcher: Fetcher = google_fetcher, limit: int | None = None,
        now: dt.datetime | None = None, sleep=time.sleep) -> int:
    now = now or dt.datetime.now(TZ).replace(microsecond=0)
    config: Config = load_config(root / "routes.yaml")
    version = config_version(root / "routes.yaml")
    repo = os.environ.get("GITHUB_REPOSITORY")
    settings = config.settings
    routes = {r.name: r for r in config.routes if r.enabled}

    combos: dict[str, Combo] = {}
    totals: dict[str, int] = {}
    for route in routes.values():
        # 同一組機場＋日期出現在多條航線時，歸給排在前面的航線
        built = [c for c in build_combos(route, now.date()) if c.key not in combos]
        totals[route.name] = len(built)
        combos.update({c.key: c for c in built})

    state_path = root / "data" / "state.json"
    state = {k: v for k, v in load_state(state_path).items() if k in combos}
    history_path = root / "data" / "history.csv"
    previous_history = load_history(history_path)
    auto = auto_thresholds(config, previous_history, now)
    price_ranges = history_price_ranges(previous_history)

    limit = settings.queries_per_run if limit is None else limit
    todo = pick_combos(combos, state, limit, {r.name: r.weight for r in routes.values()})
    print(f"[{now:%Y-%m-%d %H:%M}] 共 {len(combos)} 組，本次查 {len(todo)} 組")

    run_info = {"attempted": 0, "ok": 0, "none": 0, "error": 0, "stopped_early": False}
    history: list[dict] = []
    deals: list[tuple[dict, Route]] = []
    consecutive_errors = 0

    for i, combo in enumerate(todo):
        if i:
            sleep(random.uniform(*settings.delay_seconds))
        route = routes[combo.route]
        entry = state.get(combo.key, {})
        entry.update({
            "route": combo.route, "origin": combo.origin, "destination": combo.destination,
            "depart": combo.depart.isoformat(), "ret": combo.ret.isoformat(),
            "checked_at": now.isoformat(),
        })
        run_info["attempted"] += 1
        try:
            quote = fetch_checked(fetcher, combo, route, settings, entry)
        except NoFlights:
            entry.pop("suspect_none", None)
            entry.pop("error", None)
            entry.update({"status": "none"})
            run_info["none"] += 1
            consecutive_errors = 0
            print(f"  - {combo.key}: 查無航班")
        except Exception as exc:  # 網路錯誤、被擋、解析失敗…
            # 保留上一次成功的價格，只記錄錯誤原因
            entry.setdefault("status", "error")
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            run_info["error"] += 1
            consecutive_errors += 1
            print(f"  ! {combo.key}: {type(exc).__name__}: {exc}", file=sys.stderr)
            state[combo.key] = entry
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                run_info["stopped_early"] = True
                print("連續錯誤過多，提前停止本次執行", file=sys.stderr)
                break
            continue
        else:
            consecutive_errors = 0
            run_info["ok"] += 1
            if fetcher is google_fetcher:
                quote.price_comparison, quote.price_insights = cached_price_insights(
                    combo, route, settings, entry, now)
            entry.pop("error", None)
            entry.pop("suspect_none", None)
            entry.update({
                "status": "ok", "fare": quote.fare, "bag_add": quote.bag_add,
                "total": quote.total, "airlines": quote.airlines, "stops": quote.stops,
                "outbound": quote.outbound, "duration_min": quote.duration_min,
                "url": quote.url, "options": quote.options,
                "direct_total": quote.direct_total, "direct_airlines": quote.direct_airlines,
                "direct_outbound": quote.direct_outbound,
                "price_comparison": quote.price_comparison,
                "price_insights": quote.price_insights,
            })
            history.append({**entry, "airlines": "/".join(quote.airlines)})
            previous_range = price_ranges.get(history_flight_key(entry))
            history_text = (f"{previous_range[0]:,}～{previous_range[1]:,} {settings.currency}"
                            f"（{previous_range[2]} 筆，含行李估算）"
                            if previous_range else "無歷史紀錄")
            return_time = quote.return_reference or "未查到"
            filters = f"{combo.filters}|" if combo.filters else ""
            print(f"  ✓ {combo.origin}-{combo.destination}|"
                  f"{combo.depart.isoformat()} {quote.outbound}|"
                  f"{combo.ret.isoformat()} {return_time}|"
                  f"{filters} {quote.total:,} （票價 {quote.fare:,} + 行李 {quote.bag_add:,}）"
                  f" {'/'.join(quote.airlines)}"
                  f"｜近期價格範圍：{history_text}"
                  f"｜歷史價格比較：{quote.price_comparison}")

            # 通知判斷：低於門檻，且比上次通知的價格再便宜 notify_drop 以上
            alert_below = alert_threshold(route, combo.origin, combo.destination, auto)
            entry["alert_below"] = alert_below
            if settings.test_force_deals:
                deals.append((dict(entry), route))
            elif alert_below is not None and quote.total <= alert_below:
                last = entry.get("notified_total")
                if last is None or quote.total <= last - settings.notify_drop:
                    entry["notified_total"] = quote.total
                    deals.append((dict(entry), route))
            elif alert_below is not None:
                entry.pop("notified_total", None)  # 漲回門檻以上，下次再跌破時重新通知
        state[combo.key] = entry

    save_state(state_path, state)
    append_history(history_path, history)
    (root / "README.md").write_text(build_readme(config, state, totals, run_info, now, auto, version, repo), encoding="utf-8")

    out = root / "out"
    out.mkdir(exist_ok=True)
    for name in ("deals.md", "deals_title.txt"):
        (out / name).unlink(missing_ok=True)
    if deals:
        title, body = build_deals(deals, now, os.environ.get("GITHUB_REPOSITORY_OWNER"), version, repo,
                                  test=settings.test_force_deals)
        (out / "deals_title.txt").write_text(title, encoding="utf-8")
        (out / "deals.md").write_text(body, encoding="utf-8")
        if settings.test_force_deals:
            print(f"🧪 {len(deals)} 組已加入測試通知")
        else:
            print(f"🔥 {len(deals)} 組低於門檻，將發出通知")

    print(f"完成：成功 {run_info['ok']}、查無 {run_info['none']}、錯誤 {run_info['error']}")
    if run_info["attempted"] and run_info["ok"] == 0 and run_info["none"] == 0:
        return 1  # 全部失敗，讓 Actions 標示紅燈
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="FlightSeeker 機票監控")
    parser.add_argument("--root", default=".", help="專案根目錄")
    parser.add_argument("--limit", type=int, default=None, help="本次最多查幾組（預設看 routes.yaml）")
    args = parser.parse_args()
    env_limit = os.environ.get("QUERY_LIMIT", "").strip()
    limit = args.limit if args.limit is not None else (int(env_limit) if env_limit else None)
    sys.exit(run(Path(args.root), limit=limit))


if __name__ == "__main__":
    main()
