"""向 Trip.com 查價（透過 headless Playwright），可依去回程出發時間篩選。

Trip.com 的 API 需要頁面 JS 產生的簽章，所以不直接打 API，而是讓瀏覽器開頁面、
攔截頁面自己發出的 FlightListSearch 回應：
  1. 搜尋頁（showfarefirst）→ 去程清單，每班的價格是「搭最便宜回程」的來回下限
  2. 選定去程後的回程頁（ShowFareNext）→ 回程清單，每班的價格就是整趟來回總價

單獨查一次：
  python -m flightseeker.trip --origins TPE KHH --dests HND NRT \
      --depart 2026-11-20 --ret 2026-11-24 --out-before 09:00 --ret-between 12:00 19:00
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import dataclass
from urllib.parse import urlencode

BASE = "https://tw.trip.com/flights"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
TIMEOUT_MS = 45_000


@dataclass
class Leg:
    depart: str               # "2026-11-20 06:50"
    arrive: str
    route: str                # 機場路徑，例如 "TPE→HKG→NRT"
    flights: str              # 航班號，例如 "UO111/UO650"
    stops: int
    duration_min: int


@dataclass
class TripOption:
    outbound: Leg
    inbound: Leg
    fare: int                 # 來回票價（全部乘客，未扣信用卡優惠）
    bag_included: bool        # 去回程都含免費托運行李
    bag_add: int              # 未含托運時加上的行李估算
    url: str                  # 該去程的回程選擇頁

    @property
    def total(self) -> int:
        return self.fare + self.bag_add


def _events(body: str, sse: bool) -> list[dict]:
    if not sse:
        return [json.loads(body)]
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def _itineraries(events: list[dict]) -> list[dict]:
    return [it for ev in events for it in (ev.get("itineraryList") or [])]


def _leg(itinerary: dict, journey_no: int) -> Leg:
    journeys = itinerary["journeyList"]
    journey = next((j for j in journeys if j.get("journeyNo") == journey_no), journeys[-1])
    segs = journey["transSectionList"]
    path = [segs[0]["departPoint"]["airportCode"]] + [s["arrivePoint"]["airportCode"] for s in segs]
    return Leg(
        depart=segs[0]["departDateTime"][:16],
        arrive=segs[-1]["arriveDateTime"][:16],
        route="→".join(path),
        flights="/".join(s["flightInfo"]["flightNo"] for s in segs),
        stops=len(segs) - 1,
        duration_min=int(journey.get("duration") or 0),
    )


def _fare(itinerary: dict, adults: int) -> int:
    price = itinerary["policies"][0]["price"]
    return int(price["adult"]["totalPrice"]) * adults


def _bag_free(itinerary: dict) -> bool:
    return "FREE_CHECKED_BAGGAGE" in (itinerary.get("flagList") or [])


def _in_window(when: str, start: str, end: str) -> bool:
    return start <= when[11:16] <= end


def _search_url(origin: str, dest: str, depart: dt.date, ret: dt.date, adults: int, currency: str) -> str:
    return f"{BASE}/showfarefirst?" + urlencode({
        "dcity": origin.lower(), "acity": dest.lower(), "ddate": depart.isoformat(),
        "rdate": ret.isoformat(), "triptype": "rt", "class": "y", "quantity": adults,
        "locale": "zh-TW", "curr": currency,
    })


def _return_url(origin: str, dest: str, depart: dt.date, ret: dt.date, adults: int, currency: str,
                product_id: str, policy_id: str) -> str:
    return f"{BASE}/ShowFareNext?" + urlencode({
        "pagesource": "list", "triptype": "RT", "class": "Y", "quantity": adults, "childqty": 0,
        "babyqty": 0, "jumptype": "GoToNextJournay", "dcity": origin.lower(), "acity": dest.lower(),
        "ddate": depart.isoformat(), "rdate": ret.isoformat(), "currentseqno": 2,
        "criteriaToken": product_id, "shoppingid": policy_id, "groupKey": policy_id,
        "airline": "", "locale": "zh-TW", "curr": currency,
    })


class TripBrowser:
    """共用一個 headless Chromium，避免每次查詢都重開瀏覽器。"""

    def __enter__(self) -> "TripBrowser":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # 本機 Playwright 版本與已下載的 Chromium 不合時，可用環境變數指定執行檔
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXE") or None
        self._browser = self._pw.chromium.launch(headless=True, executable_path=exe)
        ctx = self._browser.new_context(locale="zh-TW", user_agent=UA,
                                        viewport={"width": 1366, "height": 900})
        self.page = ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        self._browser.close()
        self._pw.stop()

    def load(self, url: str, journey_no: int) -> list[dict]:
        """開頁面，回傳該行程段 FlightListSearch 回應的所有事件。"""
        def is_list(resp) -> bool:
            if "FlightListSearch" not in resp.url or not resp.request.post_data:
                return False
            criteria = json.loads(resp.request.post_data).get("searchCriteria") or {}
            return criteria.get("journeyNo") == journey_no

        with self.page.expect_response(is_list, timeout=TIMEOUT_MS) as info:
            self.page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        resp = info.value
        return _events(resp.text(), "SSE" in resp.url)


def search_round_trip(browser: TripBrowser, origin: str, dest: str, depart: dt.date, ret: dt.date,
                      out_window: tuple[str, str] = ("00:00", "23:59"),
                      ret_window: tuple[str, str] = ("00:00", "23:59"),
                      adults: int = 1, currency: str = "TWD", bag_estimate: int = 0,
                      top: int = 10, log=print) -> list[TripOption]:
    """查一組機場＋日期，回傳符合時段、依含行李總價排序的前 top 個來回組合。"""
    events = browser.load(_search_url(origin, dest, depart, ret, adults, currency), journey_no=1)
    product_id = next((ev["basicInfo"]["productId"] for ev in events
                       if (ev.get("basicInfo") or {}).get("productId")), None)
    if product_id is None:
        raise RuntimeError(f"{origin}-{dest}: 搜尋結果沒有 productId，可能被擋或頁面改版")

    # 只留真的從 origin 出發、抵達 dest 的去程（城市搜尋會混入松山、羽田等同城機場）
    candidates = []
    for it in _itineraries(events):
        leg = _leg(it, 1)
        path = leg.route.split("→")
        if path[0] == origin and path[-1] == dest and _in_window(leg.depart, *out_window):
            candidates.append((_fare(it, adults), leg, it))
    candidates.sort(key=lambda c: c[0])
    log(f"  {origin}→{dest}：去程 {len(_itineraries(events))} 班，時段內 {len(candidates)} 班")

    results: list[TripOption] = []
    for floor, out_leg, out_it in candidates:
        # 去程清單的價格是搭最便宜回程的來回價，是這班去程所有組合的下限；
        # 下限已經比目前第 top 名還貴，後面的去程都不用再點進去看
        if len(results) >= top and floor >= results[top - 1].fare:
            break
        url = _return_url(origin, dest, depart, ret, adults, currency,
                          product_id, out_it["policies"][0]["policyId"])
        try:
            ret_events = browser.load(url, journey_no=2)
        except Exception as exc:  # 單一去程失敗不影響其他去程
            log(f"    ! {out_leg.flights}: {type(exc).__name__}: {exc}"[:200])
            continue
        matched = 0
        for it in _itineraries(ret_events):
            in_leg = _leg(it, 2)
            path = in_leg.route.split("→")
            if path[0] != dest or path[-1] != origin or not _in_window(in_leg.depart, *ret_window):
                continue
            matched += 1
            bag_included = _bag_free(out_it) and _bag_free(it)
            results.append(TripOption(
                outbound=out_leg, inbound=in_leg, fare=_fare(it, adults),
                bag_included=bag_included, bag_add=0 if bag_included else bag_estimate * adults,
                url=url,
            ))
        log(f"    {out_leg.depart[11:]} {out_leg.flights}：回程時段內 {matched} 班")
        results.sort(key=lambda o: (o.total, o.fare))
    return results[:top]


def _fmt_leg(leg: Leg) -> str:
    day = "+1" if leg.arrive[:10] != leg.depart[:10] else ""
    stops = "直飛" if leg.stops == 0 else f"轉{leg.stops}"
    return f"{leg.depart[11:]}→{leg.arrive[11:]}{day} {leg.route} {leg.flights}（{stops}）"


def main() -> None:
    parser = argparse.ArgumentParser(description="Trip.com 來回機票查價")
    parser.add_argument("--origins", nargs="+", required=True)
    parser.add_argument("--dests", nargs="+", required=True)
    parser.add_argument("--depart", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--ret", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--out-before", default="23:59", help="去程最晚出發時間（含）")
    parser.add_argument("--ret-between", nargs=2, default=["00:00", "23:59"], help="回程出發時段（含）")
    parser.add_argument("--bag", type=int, default=2000, help="未含托運時加上的來回行李估算（每人）")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    found: list[TripOption] = []
    with TripBrowser() as browser:
        for origin in args.origins:
            for dest in args.dests:
                try:
                    found += search_round_trip(
                        browser, origin.upper(), dest.upper(), args.depart, args.ret,
                        out_window=("00:00", args.out_before), ret_window=tuple(args.ret_between),
                        adults=args.adults, bag_estimate=args.bag, top=args.top)
                except Exception as exc:
                    print(f"  ! {origin}→{dest}: {type(exc).__name__}: {exc}"[:300])
    found.sort(key=lambda o: (o.total, o.fare))

    title = (f"最便宜 {min(args.top, len(found))} 組（{args.depart} → {args.ret}，"
             f"去程 {args.out_before} 前出發，回程 {args.ret_between[0]}–{args.ret_between[1]} 出發）")
    print(f"\n{title}")
    for i, o in enumerate(found[:args.top], 1):
        bag = "含托運" if o.bag_included else f"+行李 {o.bag_add:,}"
        print(f"{i:>2}. 總價 {o.total:>6,}（票價 {o.fare:,} {bag}）")
        print(f"    去 {_fmt_leg(o.outbound)}")
        print(f"    回 {_fmt_leg(o.inbound)}")

    # 在 GitHub Actions 上執行時，結果另外寫成表格顯示在執行頁的 Summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [f"### Trip.com {title}", "",
                 "| # | 含行李總價 | 票價 | 行李 | 去程 | 回程 | 連結 |",
                 "|---|---:|---:|---|---|---|---|"]
        for i, o in enumerate(found[:args.top], 1):
            bag = "含托運" if o.bag_included else f"+{o.bag_add:,}"
            path = o.outbound.route.split("→")
            # 回程頁連結帶有搜尋階段的 token，過一陣子就失效，表格改連到搜尋頁
            link = _search_url(path[0], path[-1], args.depart, args.ret, args.adults, "TWD")
            lines.append(f"| {i} | {o.total:,} | {o.fare:,} | {bag} | {_fmt_leg(o.outbound)} "
                         f"| {_fmt_leg(o.inbound)} | [查看]({link}) |")
        if not found:
            lines.append("| - | 查無符合條件的組合 | | | | | |")
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if not found:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
