"""產生 README.md 報表與通知內容。"""

from __future__ import annotations

import datetime as dt

from .config import Config, ConfigVersion, Route, alert_threshold

WEEKDAYS = "一二三四五六日"
TOP_N = 15


def _d(iso: str) -> str:
    day = dt.date.fromisoformat(iso)
    return f"{day.month}/{day.day}({WEEKDAYS[day.weekday()]})"


def _money(n: int | None) -> str:
    return "—" if n is None else f"{n:,}"


def _ago(iso: str | None, now: dt.datetime) -> str:
    if not iso:
        return "—"
    then = dt.datetime.fromisoformat(iso)
    return f"{then.month}/{then.day} {then:%H:%M}"


def _version(version: ConfigVersion | None, repo: str | None, tz) -> str:
    if version is None:
        return "> 搜尋條件版本：無法取得（找不到 routes.yaml 的 git 紀錄）"
    sha = version.sha[:7]
    if repo:
        sha = f"[{sha}](https://github.com/{repo}/commit/{version.sha})"
    return (f"> 搜尋條件版本：{version.changed_at.astimezone(tz):%Y-%m-%d %H:%M} 修改的 routes.yaml"
            f"（{sha} {version.subject}）")


def _special(e: dict, route: Route) -> bool:
    line = route.highlight_below_by_dest.get(e["destination"])
    return line is not None and e["total"] <= line


def _row(e: dict, route: Route, now: dt.datetime) -> str:
    alert_below = e.get("alert_below", route.alert_below)
    hot = alert_below is not None and e["total"] <= alert_below
    if _special(e, route):
        total = f"🚨 **{_money(e['total'])} 特價**"
    elif hot:
        total = f"**{_money(e['total'])}** 🔥"
    else:
        total = _money(e["total"])
    bag = f"+{_money(e['bag_add'])}" if e.get("bag_add") else "已含"
    stops = "直飛" if e.get("stops") == 0 else f"轉 {e.get('stops')} 次"
    airlines = "、".join(e.get("airlines") or []) or "—"
    if e.get("stops") == 0:
        direct = "同左"
    elif e.get("direct_total"):
        direct = f"{_money(e['direct_total'])}（{'、'.join(e.get('direct_airlines') or [])}）"
    else:
        direct = "—"
    return (
        f"| {_d(e['depart'])} → {_d(e['ret'])} | {e['origin']}→{e['destination']} "
        f"| {airlines} | {stops} | {e.get('outbound', '—')} | {_money(e['fare'])} "
        f"| {bag} | {total} | {direct} | {_ago(e.get('checked_at'), now)} | [查看]({e['url']}) |"
    )


def build_readme(config: Config, state: dict[str, dict], totals: dict[str, int],
                 run: dict, now: dt.datetime, auto: dict[tuple[str, str, str], int] | None = None,
                 version: ConfigVersion | None = None, repo: str | None = None) -> str:
    lines = [
        "# ✈️ FlightSeeker 機票監控",
        "",
        f"> 最後執行：{now:%Y-%m-%d %H:%M}（台北時間）　"
        f"本次查詢 {run['attempted']} 組：成功 {run['ok']}、查無航班 {run['none']}、錯誤 {run['error']}",
        ">",
        _version(version, repo, now.tzinfo),
    ]
    if run.get("stopped_early"):
        lines += ["", "> ⚠️ 連續錯誤過多，本次提前停止，可能被 Google 暫時擋住了。"]
    lines.append("")

    for route in config.routes:
        if not route.enabled:
            continue
        entries = [e for e in state.values() if e.get("route") == route.name and e.get("status") == "ok"]
        checked = sum(1 for e in state.values() if e.get("route") == route.name and e.get("checked_at"))
        total = totals.get(route.name, 0)
        alert = f"，低於 {route.alert_below:,} 會通知" if route.alert_below else ""
        if route.auto_alert:
            s = config.settings
            alert = (f"，低於自動低價線會通知（最近 {s.auto_alert_days} 天價格的第 {s.auto_alert_percentile} 百分位"
                     f"{f'，紀錄不足時用 {route.alert_below:,}' if route.alert_below else ''}）")
        if route.alert_below_by_dest:
            alert += "（" + "、".join(f"{d} 低於 {v:,}" for d, v in route.alert_below_by_dest.items()) + "）"
        if route.alert_offset_by_origin:
            alert += "；手動門檻" + "、".join(f"{o} 出發 {v:+,}" for o, v in route.alert_offset_by_origin.items())
        if route.highlight_below_by_dest:
            alert += "；" + "、".join(f"{d} 低於 {v:,}" for d, v in route.highlight_below_by_dest.items()) + " 以 🚨 特別標示"
        dest_days = "".join(f"（{d} {'/'.join(map(str, v))} 天）" for d, v in route.trip_days_by_dest.items())
        extra = ""
        if route.max_stops == 0:
            extra += "，限直飛"
        if route.airlines:
            extra += f"，限 {'/'.join(route.airlines)}"
        if route.must_include:
            spans = " 或 ".join(f"{a.month}/{a.day}–{b.month}/{b.day}" for a, b in route.must_include)
            extra += f"，需包含 {spans}"
        lines += [
            f"## {route.name}",
            "",
            f"{'/'.join(route.origins)} → {'/'.join(route.destinations)}，"
            f"{route.window_start:%Y/%m/%d}–{route.window_end:%Y/%m/%d}，"
            f"{'/'.join(map(str, route.trip_days))} 天{dest_days}"
            f"{'（含完整週末）' if route.require_full_weekend else ''}{extra}{alert}",
            "",
            f"已查過 {checked} / {total} 組日期 × 機場組合（每次執行輪流查最久沒更新的組合）。",
            "",
        ]
        if route.auto_alert:
            lines += _threshold_table(route, auto or {})
        if not entries:
            lines += ["還沒有查價結果。", ""]
            continue
        entries.sort(key=lambda e: (e["total"], e["depart"]))
        lines += [
            f"### 目前最便宜 {min(TOP_N, len(entries))} 組",
            "",
            "| 去 → 回 | 機場 | 航空公司 | 轉機 | 去程時間 | 票價 | 行李 | 估算總價 | 直飛最低 | 更新 | 連結 |",
            "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
        ]
        lines += [_row(e, route, now) for e in entries[:TOP_N]]
        lines.append("")

    lines += [
        "---",
        "",
        "- **票價**：Google Flights 顯示的來回總價（去程最便宜選項 + 搭配的回程）",
        "- **直飛最低**：同一組日期中最便宜的直飛選項（也已含行李估算）。只想看直飛，把 `routes.yaml` 的 `max_stops` 改成 0",
        "- **行李**：`routes.yaml` 的 `budget_airlines` 名單內的廉航，會加上 `bag_estimate` 估算；其他航空公司視為已含托運",
        "- **改條件**：編輯 [`routes.yaml`](routes.yaml)；**改頻率**：編輯 [`.github/workflows/flightseeker.yml`](.github/workflows/flightseeker.yml) 的 `cron`",
        "- **立刻查一次**：Actions → FlightSeeker → Run workflow",
        "",
    ]
    return "\n".join(lines)


def _threshold_table(route: Route, auto: dict[tuple[str, str, str], int]) -> list[str]:
    rows = []
    for origin, dest in sorted(route.pairs, key=lambda p: (p[1], p[0])):
        line = alert_threshold(route, origin, dest, auto)
        if dest in route.alert_below_by_dest:
            source = "手動設定"
        elif (route.name, origin, dest) in auto:
            source = "自動"
        else:
            source = "紀錄不足"
        rows.append(f"| {origin}→{dest} | {_money(line)} | {source} |")
    return [
        "<details><summary>各航線的通知門檻</summary>",
        "",
        "| 航線 | 低於多少會通知 | 來源 |",
        "|---|---:|---|",
        *rows,
        "",
        "</details>",
        "",
    ]


def build_deals(deals: list[tuple[dict, Route]], now: dt.datetime, mention: str | None,
                version: ConfigVersion | None = None, repo: str | None = None,
                *, test: bool = False) -> tuple[str, str]:
    deals = sorted(deals, key=lambda d: d[0]["total"])
    special = [d for d in deals if _special(*d)]
    normal = [d for d in deals if not _special(*d)]
    best = (special or deals)[0][0]
    title = (
        f"{'🚨 特價 ' + str(len(special)) + ' 組｜' if special else ''}"
        f"✈️ {len(deals)} 組低價機票｜{'特價最低' if special else '最低'} {best['total']:,} "
        f"{best['origin']}→{best['destination']} {_d(best['depart'])}"
    )
    header = [
        "| 去 → 回 | 機場 | 航空公司 | 轉機 | 去程時間 | 票價 | 行李 | 估算總價 | 直飛最低 | 更新 | 連結 |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    body = [f"{now:%Y-%m-%d %H:%M} 查到以下組合低於通知門檻：", ""]
    if test:
        title = f"🧪 測試通知｜{len(deals)} 組機票"
        body = [f"{now:%Y-%m-%d %H:%M} 測試通知，列出本次成功查價的組合。", "",
                *header, *[_row(e, route, now) for e, route in deals]]
    elif special:
        body += ["## 🚨 特別低價", "", *header, *[_row(e, route, now) for e, route in special], ""]
        if normal:
            body += ["## 其他低於門檻", ""]
    if normal and not test:
        body += [*header, *[_row(e, route, now) for e, route in normal]]
    body += ["", _version(version, repo, now.tzinfo), "", "價格隨時會變，請以 Google Flights 頁面為準。"]
    if mention:
        body += ["", f"cc @{mention}"]
    return title, "\n".join(body) + "\n"


def build_line_deals(deals: list[tuple[dict, Route]], now: dt.datetime, currency: str,
                     *, test: bool = False) -> str:
    label = "🧪 機票測試通知" if test else "✈️ 機票降價通知"
    sections = [f"{label}｜查價時間 {now:%Y-%m-%d %H:%M}"]
    for entry, _ in sorted(deals, key=lambda deal: deal[0]["total"]):
        origin, destination = entry["origin"], entry["destination"]
        airlines = "／".join(entry["airlines"]) or "未提供"
        sections.append("\n".join([
            f"{origin} → {destination}｜{entry['depart']} {entry['outbound']}",
            f"{destination} → {origin}｜{entry['ret']} {entry['return_time']}",
            f"航空公司：{airlines}",
            f"本次估算總價：{entry['total']:,} {currency}"
            f"（票價 {entry['fare']:,}＋行李 {entry['bag_add']:,}）",
            f"近期搜尋價格：{entry['recent_price']}",
            f"Google Flights 歷史價格比較：{entry['price_comparison']}",
        ]))
    return "\n\n".join(sections) + "\n"
