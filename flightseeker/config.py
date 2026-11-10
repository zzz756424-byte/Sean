"""讀取 routes.yaml，轉成型別明確的設定物件。"""

from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Settings:
    currency: str = "TWD"
    adults: int = 1
    queries_per_run: int = 50
    delay_seconds: tuple[float, float] = (2.0, 4.0)
    notify_drop: int = 500
    auto_alert_percentile: int = 10   # 自動低價線：該航線過去查到價格的第幾百分位
    auto_alert_days: int = 30         # 只看最近幾天的查價紀錄
    auto_alert_min_samples: int = 20  # 紀錄少於這個數量時，改用 alert_below
    budget_airlines: list[str] = field(default_factory=list)


@dataclass
class Route:
    name: str
    origins: list[str]
    destinations: list[str]
    window_start: dt.date
    window_end: dt.date
    trip_days: list[int]
    require_full_weekend: bool = True
    max_stops: int | None = None
    bag_estimate: int = 0
    alert_below: int | None = None
    enabled: bool = True
    # 出發機場 → 可飛的目的地；destinations 寫成對照表時只查表上的組合
    pairs: list[tuple[str, str]] = field(default_factory=list)
    airlines: list[str] = field(default_factory=list)  # 限定航空公司（IATA 代碼），空 = 不限
    # 連假區間：旅程必須完整包含其中一段，空 = 不限
    must_include: list[tuple[dt.date, dt.date]] = field(default_factory=list)
    weight: int = 1  # 每次執行分配名額的比重
    alert_below_by_dest: dict[str, int] = field(default_factory=dict)  # 個別目的地的通知門檻
    auto_alert: bool = False  # 依各「出發→目的地」的歷史價格自動算低價線
    trip_days_by_dest: dict[str, list[int]] = field(default_factory=dict)  # 個別目的地的旅程天數
    highlight_below_by_dest: dict[str, int] = field(default_factory=dict)  # 低於這個價格特別標示 🚨
    # 個別出發機場的手動門檻加減（例如高雄 +1000）；自動低價線本來就分出發地計算，不再加減
    alert_offset_by_origin: dict[str, int] = field(default_factory=dict)


@dataclass
class Config:
    settings: Settings
    routes: list[Route]


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value).strip())


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    s = raw.get("settings") or {}
    delay = s.get("delay_seconds", [2, 4])
    if isinstance(delay, (int, float)):
        delay = [delay, delay]
    settings = Settings(
        currency=str(s.get("currency", "TWD")).upper(),
        adults=int(s.get("adults", 1)),
        queries_per_run=int(s.get("queries_per_run", 50)),
        delay_seconds=(float(delay[0]), float(delay[1])),
        notify_drop=int(s.get("notify_drop", 500)),
        auto_alert_percentile=int(s.get("auto_alert_percentile", 10)),
        auto_alert_days=int(s.get("auto_alert_days", 30)),
        auto_alert_min_samples=int(s.get("auto_alert_min_samples", 20)),
        budget_airlines=[str(a) for a in (s.get("budget_airlines") or [])],
    )

    routes: list[Route] = []
    for i, r in enumerate(raw.get("routes") or []):
        window = r.get("window") or []
        if len(window) != 2:
            raise ValueError(f"routes[{i}] 的 window 需要 [開始日, 結束日]")
        trip_days = r.get("trip_days", [6])
        if isinstance(trip_days, int):
            trip_days = [trip_days]
        origins = [str(a).upper() for a in r["origins"]]
        dests = r["destinations"]
        if isinstance(dests, dict):
            by_origin = {str(o).upper(): [str(d).upper() for d in ds or []] for o, ds in dests.items()}
            pairs = [(o, d) for o in origins for d in by_origin.get(o, [])]
        else:
            pairs = [(o, str(d).upper()) for o in origins for d in dests]
        must_include = []
        for span in r.get("must_include") or []:
            if len(span) != 2:
                raise ValueError(f"routes[{i}] 的 must_include 每一段需要 [開始日, 結束日]")
            must_include.append((_as_date(span[0]), _as_date(span[1])))
        routes.append(
            Route(
                name=str(r.get("name") or f"route-{i + 1}"),
                origins=origins,
                destinations=list(dict.fromkeys(d for _, d in pairs)),
                window_start=_as_date(window[0]),
                window_end=_as_date(window[1]),
                trip_days=sorted({int(d) for d in trip_days}),
                require_full_weekend=bool(r.get("require_full_weekend", True)),
                max_stops=None if r.get("max_stops") is None else int(r["max_stops"]),
                bag_estimate=int(r.get("bag_estimate", 0)),
                alert_below=None if r.get("alert_below") is None else int(r["alert_below"]),
                enabled=bool(r.get("enabled", True)),
                pairs=[(o, d) for o, d in pairs if o != d],
                airlines=[str(a).upper() for a in (r.get("airlines") or [])],
                must_include=must_include,
                weight=max(1, int(r.get("weight", 1))),
                auto_alert=bool(r.get("auto_alert", False)),
                alert_offset_by_origin={str(o).upper(): int(v)
                                        for o, v in (r.get("alert_offset_by_origin") or {}).items()},
                highlight_below_by_dest={str(d).upper(): int(v)
                                         for d, v in (r.get("highlight_below_by_dest") or {}).items()},
                trip_days_by_dest={str(d).upper(): sorted({int(x) for x in ([v] if isinstance(v, int) else v)})
                                   for d, v in (r.get("trip_days_by_dest") or {}).items()},
                alert_below_by_dest={str(d).upper(): int(v)
                                     for d, v in (r.get("alert_below_by_dest") or {}).items()},
            )
        )
    return Config(settings=settings, routes=routes)


def auto_thresholds(config: Config, history: list[dict], now: dt.datetime) -> dict[tuple[str, str, str], int]:
    """依最近的查價紀錄，替 auto_alert 航線的每個「出發→目的地」算出低價線（百分位數）。"""
    s = config.settings
    auto_routes = {r.name for r in config.routes if r.auto_alert}
    since = (now - dt.timedelta(days=s.auto_alert_days)).isoformat()
    prices: dict[tuple[str, str, str], list[int]] = {}
    for row in history:
        if row.get("route") in auto_routes and row.get("checked_at", "") >= since and row.get("total"):
            prices.setdefault((row["route"], row["origin"], row["destination"]), []).append(int(row["total"]))
    lines = {}
    for key, values in prices.items():
        if len(values) >= s.auto_alert_min_samples:
            values.sort()
            lines[key] = values[(len(values) - 1) * s.auto_alert_percentile // 100]
    return lines


def alert_threshold(route: Route, origin: str, destination: str,
                    auto: dict[tuple[str, str, str], int]) -> int | None:
    """通知門檻：個別目的地手動設定 > 自動低價線 > 航線的 alert_below。"""
    offset = route.alert_offset_by_origin.get(origin, 0)
    if destination in route.alert_below_by_dest:
        return route.alert_below_by_dest[destination] + offset
    if route.auto_alert and (route.name, origin, destination) in auto:
        return auto[(route.name, origin, destination)]
    return None if route.alert_below is None else route.alert_below + offset


@dataclass
class ConfigVersion:
    changed_at: dt.datetime
    sha: str
    subject: str


def config_version(path: str | Path) -> ConfigVersion | None:
    """routes.yaml 最後一次修改的 commit（搜尋條件的版本）。拿不到 git 紀錄時回傳 None。"""
    path = Path(path)

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=path.parent, capture_output=True,
                              text=True, check=True).stdout.strip()

    try:
        if git("rev-parse", "--is-shallow-repository") == "true":
            return None  # 淺層 clone 只有最新一筆 commit，查不出真正的修改時間
        out = git("log", "-1", "--format=%cI%x09%H%x09%s", "--", path.name)
    except (OSError, subprocess.CalledProcessError):
        return None
    if not out:
        return None
    changed_at, sha, subject = out.split("\t", 2)
    return ConfigVersion(dt.datetime.fromisoformat(changed_at), sha, subject)
