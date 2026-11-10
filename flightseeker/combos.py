"""把航線設定展開成要查詢的「機場 × 去回日期」組合。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .config import Route


@dataclass(frozen=True)
class Combo:
    route: str
    origin: str
    destination: str
    depart: dt.date
    ret: dt.date
    filters: str = ""  # 航空公司／轉機限制，條件不同的航線各自查價

    @property
    def key(self) -> str:
        key = f"{self.origin}-{self.destination}|{self.depart.isoformat()}|{self.ret.isoformat()}"
        return f"{key}|{self.filters}" if self.filters else key

    @property
    def days(self) -> int:
        return (self.ret - self.depart).days + 1


def covers_full_weekend(depart: dt.date, ret: dt.date) -> bool:
    """旅程（含首尾兩天）是否包含相鄰的週六與週日。"""
    day = depart
    while day < ret:
        if day.weekday() == 5:  # 週六，隔天週日也在旅程內
            return True
        day += dt.timedelta(days=1)
    return False


def includes_span(depart: dt.date, ret: dt.date, spans: list[tuple[dt.date, dt.date]]) -> bool:
    """旅程是否完整包含任一段連假（沒設定連假時一律通過）。"""
    return not spans or any(depart <= start and end <= ret for start, end in spans)


def date_pairs(route: Route, today: dt.date,
               trip_days: list[int] | None = None) -> list[tuple[dt.date, dt.date]]:
    first = max(route.window_start, today + dt.timedelta(days=1))
    pairs: list[tuple[dt.date, dt.date]] = []
    depart = first
    while depart <= route.window_end:
        for days in trip_days or route.trip_days:
            ret = depart + dt.timedelta(days=days - 1)
            if ret > route.window_end:
                continue
            if route.require_full_weekend and not covers_full_weekend(depart, ret):
                continue
            if not includes_span(depart, ret, route.must_include):
                continue
            pairs.append((depart, ret))
        depart += dt.timedelta(days=1)
    return pairs


def build_combos(route: Route, today: dt.date) -> list[Combo]:
    filters = ""
    if route.airlines or route.max_stops is not None:
        filters = "+".join(route.airlines) + ("" if route.max_stops is None else f"~{route.max_stops}stop")
    dates: dict[tuple[int, ...], list[tuple[dt.date, dt.date]]] = {}
    combos: list[Combo] = []
    for origin, destination in route.pairs:
        days = tuple(route.trip_days_by_dest.get(destination, route.trip_days))
        if days not in dates:
            dates[days] = date_pairs(route, today, list(days))
        for depart, ret in dates[days]:
            combos.append(Combo(route.name, origin, destination, depart, ret, filters))
    return combos
