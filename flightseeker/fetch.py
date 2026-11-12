"""向 Google Flights 查價（透過 fast-flights），並換算含行李的估算總價。

之後若要換資料來源（Ignav、Kiwi…），只要再寫一個回傳 Quote 的 fetcher 即可。
"""

from __future__ import annotations

import time
import json
import os
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from dataclasses import dataclass, field
from typing import Callable

from .combos import Combo
from .config import Route, Settings


@dataclass
class Quote:
    fare: int                 # Google 顯示的來回票價（全部乘客）
    bag_add: int              # 估算的行李加價
    airlines: list[str]
    stops: int                # 去程轉機次數
    outbound: str             # 去程時間，例如 "08:20→12:45"
    duration_min: int         # 去程總時間（分鐘）
    url: str                  # Google Flights 搜尋連結
    options: int = 0          # 這次查到幾個選項
    direct_total: int | None = None       # 最便宜直飛的估算總價
    direct_airlines: list[str] = field(default_factory=list)
    direct_outbound: str | None = None
    notes: list[str] = field(default_factory=list)
    price_comparison: str = "未提供"
    price_insights: dict = field(default_factory=dict)
    return_reference: str | None = None

    @property
    def total(self) -> int:
        return self.fare + self.bag_add


class NoFlights(Exception):
    """這個組合查不到航班。"""


class EmptyResult(NoFlights):
    """Google 回傳的資料整個是空的：可能真的沒航班，也可能是被暫時限流。"""


class GoogleError(Exception):
    """Google 回傳錯誤頁（ErrorResponse，例如 code 13），重試後仍失敗。不是查無航班。"""


Fetcher = Callable[[Combo, Route, Settings], Quote]
NO_PRICE_INSIGHTS = "SerpApi 未提供歷史價格資訊"


def is_budget(airlines: list[str], settings: Settings) -> bool:
    names = " ".join(airlines).lower()
    return any(k.lower() in names for k in settings.budget_airlines)


def bag_add_for(airlines: list[str], route: Route, settings: Settings) -> int:
    return route.bag_estimate * settings.adults if is_budget(airlines, settings) else 0


def _hhmm(t: tuple[int, int]) -> str:
    return f"{t[0]:02d}:{t[1]:02d}"


def _within_arrival_hours(time, hours: tuple[int | None, int | None]) -> bool:
    earliest, latest = hours
    if earliest is None or latest is None:
        return True
    arrival_minutes = time[0] * 60 + time[1]
    return earliest * 60 <= arrival_minutes <= latest * 60


def _flight_times(segments) -> str:
    departure, arrival = segments[0].departure, segments[-1].arrival
    day_shift = "+1" if tuple(arrival.date) != tuple(departure.date) else ""
    return f"{_hhmm(departure.time)}→{_hhmm(arrival.time)}{day_shift}"


def _connection_error(reason: object) -> str:
    """只顯示錯誤分類及數字代碼，避免例外網址洩漏金鑰。"""
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "SerpApi SSL 憑證驗證失敗（請檢查 Python 憑證或公司代理憑證）"
    if isinstance(reason, ssl.SSLError):
        return "SerpApi SSL/TLS 連線失敗"
    if isinstance(reason, socket.gaierror):
        return "SerpApi DNS 解析失敗（無法解析 serpapi.com）"
    if isinstance(reason, TimeoutError):
        return "SerpApi 連線或讀取逾時（30 秒）"
    if isinstance(reason, ConnectionRefusedError):
        return "SerpApi 連線被拒絕（請檢查代理或防火牆）"
    if isinstance(reason, ConnectionResetError):
        return "SerpApi 連線被重設（請檢查網路或代理）"
    code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
    detail = f"（錯誤碼 {code}）" if isinstance(code, int) else ""
    return f"SerpApi 網路連線失敗{detail}"


def _serpapi_ssl_context():
    import truststore

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.set_alpn_protocols(["http/1.1"])
    # 公司提供 PEM 格式 CA 時，可在 PyCharm 環境變數指定路徑。
    cafile = os.environ.get("SERPAPI_CA_BUNDLE", "").strip()
    if cafile:
        context.load_verify_locations(cafile=cafile)
    return context


def serpapi_price_insights(combo: Combo, route: Route, settings: Settings) -> tuple[str, dict]:
    """補充 Google 的搜尋整體比較；不取代指定航班報價或本機通知門檻。"""
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return "未設定 SERPAPI_API_KEY", {}
    params = {
        "engine": "google_flights", "api_key": api_key,
        "departure_id": combo.origin, "arrival_id": combo.destination,
        "outbound_date": combo.depart.isoformat(), "return_date": combo.ret.isoformat(),
        "type": 1, "travel_class": 1, "adults": settings.adults,
        "currency": settings.currency, "hl": "en", "gl": "tw",
    }
    if route.max_stops is not None:
        # SerpApi: 0 = 不限，1 = 直飛，2 = 最多轉一次，3 = 最多轉兩次。
        if route.max_stops not in (0, 1, 2):
            return "不支援此轉機次數限制", {}
        params["stops"] = route.max_stops + 1
    if route.airlines:
        params["include_airlines"] = ",".join(route.airlines)
    try:
        context = _serpapi_ssl_context()
    except ImportError:
        return "缺少 truststore，請在目前 Python 環境執行 python -m pip install -r requirements.txt", {}
    except (OSError, ValueError):
        return "SerpApi 憑證初始化失敗（請檢查 SERPAPI_CA_BUNDLE 路徑及 PEM 憑證格式）", {}
    try:
        with urlopen("https://serpapi.com/search.json?" + urlencode(params), timeout=30,
                     context=context) as response:
            data = json.load(response)
        if data.get("error"):
            return "SerpApi 回傳錯誤（請查看服務後台）", {}
        insights = data.get("price_insights")
        if not isinstance(insights, dict) or not insights:
            return NO_PRICE_INSIGHTS, {}
        parts = []
        level = insights.get("price_level")
        if level in ("low", "typical", "high"):
            parts.append({"low": "偏低", "typical": "正常", "high": "偏高"}[level])
        typical = insights.get("typical_price_range")
        if isinstance(typical, list) and len(typical) == 2 and all(isinstance(v, (int, float)) for v in typical):
            parts.append(f"常態區間 {typical[0]:,}–{typical[1]:,} {settings.currency}")
        history = insights.get("price_history") or []
        prices = [point[1] for point in history if isinstance(point, list) and len(point) == 2
                  and all(isinstance(v, (int, float)) for v in point)]
        if prices:
            parts.append(f"歷史最低 {min(prices):,} {settings.currency}（{len(prices)} 筆）")
        return "；".join(parts) or "SerpApi 未提供可用的比較欄位", insights
    except HTTPError as exc:
        return f"SerpApi HTTP {exc.code}", {}
    except URLError as exc:
        return _connection_error(exc.reason), {}
    except (TimeoutError, OSError) as exc:
        return _connection_error(exc), {}
    except ValueError:
        return "SerpApi 回應不是有效的 JSON（可能收到代理或驗證頁面）", {}


def google_fetcher(combo: Combo, route: Route, settings: Settings) -> Quote:
    from fast_flights import (
        FlightQuery,
        FlightsNotFound,
        Passengers,
        create_query,
        get_flights,
    )

    return_leg = FlightQuery(
        date=combo.ret.isoformat(),
        from_airport=combo.destination,
        to_airport=combo.origin,
        max_stops=route.max_stops,
        airlines=route.airlines or None,
    )
    query = create_query(
        flights=[
            FlightQuery(
                date=combo.depart.isoformat(),
                from_airport=combo.origin,
                to_airport=combo.destination,
                max_stops=route.max_stops,
                airlines=route.airlines or None,
            ),
            return_leg,
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=settings.adults),
        language="en",
        currency=settings.currency,
        hide_separate_and_self_transfer=True,
    )
    url = query.url().replace("&hl=en", "&hl=zh-TW")

    google_errors = 0
    empty_retried = False
    while True:
        try:
            results = get_flights(query)
            break
        except FlightsNotFound as exc:
            # fast-flights 把 Google 的 ErrorResponse 當成「查無航班」，但實測同一個查詢會隨機在
            # 正常結果和 ErrorResponse 之間跳動，所以是暫時性錯誤：重試，最後仍失敗就當成錯誤
            google_errors += 1
            if google_errors >= 3:
                raise GoogleError(f"Google 回傳錯誤，重試 {google_errors} 次仍失敗: {exc}") from exc
            time.sleep(2 * google_errors)
        except TypeError as exc:
            # 真的沒有符合條件的航班時，Google 回傳的資料是空的（fast-flights 解析會丟 TypeError）。
            # 再試一次確認
            if empty_retried:
                raise EmptyResult(f"empty result: {exc}") from exc
            empty_retried = True
            time.sleep(3)

    # Google 的航空公司篩選會混入共掛班機（例如限定華航卻出現大韓），再用名稱過濾一次
    meta = getattr(results, "metadata", None)
    allowed = {a.name for a in (meta.airlines if meta else []) if a.code in route.airlines}

    best: Quote | None = None
    best_direct: Quote | None = None
    count = 0
    for f in results:
        if not f.price or not f.flights:
            continue
        airlines = [a for a in (f.airlines or []) if a]
        if allowed and not set(airlines) <= allowed:
            continue
        if not _within_arrival_hours(f.flights[-1].arrival.time, route.outbound_arrival_hours):
            continue
        count += 1
        quote = Quote(
            fare=int(f.price),
            bag_add=bag_add_for(airlines, route, settings),
            airlines=airlines,
            stops=len(f.flights) - 1,
            outbound=_flight_times(f.flights),
            duration_min=sum(int(s.duration or 0) for s in f.flights),
            url=url,
        )
        if best is None or quote.total < best.total:
            best = quote
        if quote.stops == 0 and (best_direct is None or quote.total < best_direct.total):
            best_direct = quote

    if best is None:
        raise NoFlights("no priced flights in result")
    best.options = count
    # 回程單程查詢僅用於顯示，查詢失敗時保留來回報價。
    try:
        return_query = create_query(
            flights=[return_leg],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=settings.adults),
            language="en",
            currency=settings.currency,
            hide_separate_and_self_transfer=True,
        )
        for attempt in range(1, 4):
            try:
                return_results = get_flights(return_query)
                break
            except Exception as exc:
                if attempt == 3:
                    raise
                print(f"  回程 {combo.destination}→{combo.origin} {combo.ret}："
                      f"第 {attempt} 次查詢失敗（{type(exc).__name__}），3 秒後重試")
                time.sleep(3)
        return_meta = getattr(return_results, "metadata", None)
        return_allowed = {a.name for a in (return_meta.airlines if return_meta else [])
                          if a.code in route.airlines}
        cheapest_return = min(
            (f for f in return_results
             if f.price and f.flights
             and (not return_allowed or {a for a in (f.airlines or []) if a} <= return_allowed)
             and _within_arrival_hours(f.flights[-1].arrival.time, route.return_arrival_hours)),
            key=lambda f: int(f.price),
            default=None,
        )
        if cheapest_return is not None:
            best.return_reference = _flight_times(cheapest_return.flights)
        else:
            print(f"  回程 {combo.destination}→{combo.origin} {combo.ret}：查無符合條件的班次")
    except Exception as exc:
        print(f"  回程 {combo.destination}→{combo.origin} {combo.ret}：查詢或解析失敗（{type(exc).__name__}）")
        best.return_reference = "查詢失敗"
    if best_direct is not None:
        best.direct_total = best_direct.total
        best.direct_airlines = best_direct.airlines
        best.direct_outbound = best_direct.outbound
    return best
