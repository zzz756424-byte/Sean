"""Resolve passenger airport codes using the bundled OurAirports index."""

import csv
import re
from difflib import get_close_matches
from functools import cache
from pathlib import Path
from unicodedata import normalize

AIRPORTS = {
    "台中": "RMQ", "台中機場": "RMQ", "台北": "TPE", "taipei": "TPE",
    "桃園": "TPE", "桃園機場": "TPE", "松山": "TSA", "松山機場": "TSA",
    "沖繩": "OKA", "okinawa": "OKA", "那霸": "OKA",
    "那霸機場": "OKA", "那霸國際機場": "OKA", "naha": "OKA", "東京": "HND", "tokyo": "HND",
    "羽田": "HND", "成田": "NRT", "大阪": "KIX", "osaka": "KIX", "關西": "KIX",
    "首爾": "ICN", "seoul": "ICN", "仁川": "ICN", "金浦": "GMP",
    "香港": "HKG", "新加坡": "SIN", "曼谷": "BKK", "bangkok": "BKK",
    "倫敦": "LHR", "london": "LHR", "希斯洛": "LHR", "巴黎": "CDG", "paris": "CDG",
    "紐約": "JFK", "new york": "JFK", "洛杉磯": "LAX", "雪梨": "SYD", "杜拜": "DXB",
    "高雄": "KHH", "福岡": "FUK", "札幌": "CTS", "新千歲": "CTS", "名古屋": "NGO",
    "聖保羅": "GRU", "são paulo": "GRU",
    "吉隆坡": "KUL", "吉隆坡機場": "KUL", "吉隆坡國際機場": "KUL", "kuala lumpur": "KUL",
    "新山": "JHB", "檳城": "PEN", "亞庇": "BKI", "古晉": "KCH", "蘭卡威": "LGK",
    "新加坡樟宜": "SIN", "樟宜": "SIN", "素萬那普": "BKK", "廊曼": "DMK",
    "清邁": "CNX", "普吉": "HKT", "峴港": "DAD", "河內": "HAN", "胡志明市": "SGN",
    "富國島": "PQC", "富國": "PQC", "phu quoc": "PQC", "phú quốc": "PQC",
    "馬尼拉": "MNL", "宿霧": "CEB", "雅加達": "CGK", "峇里島": "DPS", "巴里島": "DPS",
    "北京": "PEK", "北京首都": "PEK", "北京大興": "PKX", "上海": "PVG",
    "上海浦東": "PVG", "上海虹橋": "SHA", "廣州": "CAN", "深圳": "SZX",
    "澳門": "MFM", "釜山": "PUS", "濟州": "CJU", "仙台": "SDJ",
    "舊金山": "SFO", "西雅圖": "SEA", "溫哥華": "YVR", "多倫多": "YYZ",
    "法蘭克福": "FRA", "阿姆斯特丹": "AMS", "羅馬": "FCO", "墨爾本": "MEL",
}
INDEX = Path(__file__).with_name("airports.csv")
SIZE = {"large_airport": 3, "medium_airport": 2, "small_airport": 1}


def place_key(value):
    value = normalize("NFKC", value).translate(str.maketrans("臺覇国际场机绳冲岛", "台霸國際場機繩沖島"))
    value = value.replace("空港", "機場")
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def airport_name_key(value):
    return re.sub(r"(?:\s+(?:(?:international|intl) )?airport|(?:國際)?機場)$", "", value)


@cache
def airport_index():
    names = {}
    areas = {}
    aliases = {}
    short_names = {}
    codes = set()
    with INDEX.open(encoding="utf-8", newline="") as source:
        for code, kind, scheduled, name, city, region, keywords in csv.reader(source):
            priority = (-int(scheduled == "yes"), -SIZE.get(kind, 0), code)
            codes.add(code.casefold())
            keywords = re.split(r"[,;、，]", keywords)
            for labels, index in (((name,), names), ((city, region), areas), (keywords, aliases)):
                for label in labels:
                    key = place_key(label)
                    if key and (key not in index or priority < index[key]):
                        index[key] = priority
            for label in (name, *keywords):
                key = place_key(label)
                short = airport_name_key(key)
                if short and short != key:
                    short_names.setdefault(short, set()).add(code)
    return codes, names, areas, aliases, short_names


def airport_codes(value):
    codes = []
    # Some official names contain a slash. Try the whole name before treating it as a list.
    known_codes, names, areas, aliases, short_names = airport_index()
    full_name = names.get(place_key(value))
    if full_name:
        return (full_name[-1],)
    for item in normalize("NFKC", value).split("/"):
        place = place_key(item)
        alias = AIRPORTS.get(place) or AIRPORTS.get(airport_name_key(place))
        if alias:
            codes.append(alias)
        else:
            if place in known_codes:
                codes.append(place.upper())
                continue
            match = names.get(place) or areas.get(place) or aliases.get(place)
            if not match:
                candidates = short_names.get(airport_name_key(place), set())
                if len(candidates) == 1:
                    codes.append(next(iter(candidates)))
                    continue
                if not candidates and len(place) >= 3:
                    candidates = {names[key][-1] for key in get_close_matches(place, names, n=3, cutoff=0.82)}
                hint = f"可能的機場代碼：{'／'.join(sorted(candidates))}。" if candidates else ""
                raise ValueError(f"無法辨識地點「{item.strip()}」。{hint}請輸入機場全名或三碼 IATA 代碼。")
            codes.append(match[-1])
    return tuple(dict.fromkeys(codes))
