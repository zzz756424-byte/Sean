"""Resolve passenger airport codes using the bundled OurAirports index."""

import csv
from functools import cache
from pathlib import Path
from unicodedata import normalize

AIRPORTS = {
    "台中": "RMQ", "台中機場": "RMQ", "台北": "TPE", "taipei": "TPE",
    "桃園": "TPE", "桃園機場": "TPE", "松山": "TSA", "松山機場": "TSA",
    "沖繩": "OKA", "冲绳": "OKA", "okinawa": "OKA", "那霸": "OKA", "那覇": "OKA",
    "那霸機場": "OKA", "那霸國際機場": "OKA", "naha": "OKA", "東京": "HND", "tokyo": "HND",
    "羽田": "HND", "成田": "NRT", "大阪": "KIX", "osaka": "KIX", "關西": "KIX",
    "首爾": "ICN", "seoul": "ICN", "仁川": "ICN", "金浦": "GMP",
    "香港": "HKG", "新加坡": "SIN", "曼谷": "BKK", "bangkok": "BKK",
    "倫敦": "LHR", "london": "LHR", "希斯洛": "LHR", "巴黎": "CDG", "paris": "CDG",
    "紐約": "JFK", "new york": "JFK", "洛杉磯": "LAX", "雪梨": "SYD", "杜拜": "DXB",
    "高雄": "KHH", "福岡": "FUK", "札幌": "CTS", "新千歲": "CTS", "名古屋": "NGO",
    "聖保羅": "GRU", "são paulo": "GRU",
}
INDEX = Path(__file__).with_name("airports.csv")
SIZE = {"large_airport": 3, "medium_airport": 2, "small_airport": 1}


def place_key(value):
    return " ".join(normalize("NFKC", value).replace("臺", "台").casefold().split())


@cache
def airport_index():
    names = {}
    areas = {}
    aliases = {}
    codes = set()
    with INDEX.open(encoding="utf-8", newline="") as source:
        for code, kind, scheduled, name, city, region, keywords in csv.reader(source):
            priority = (-int(scheduled == "yes"), -SIZE.get(kind, 0), code)
            codes.add(code.casefold())
            for labels, index in (((name,), names), ((city, region), areas),
                                  (keywords.split(","), aliases)):
                for label in labels:
                    key = place_key(label)
                    if key and (key not in index or priority < index[key]):
                        index[key] = priority
    return codes, names, areas, aliases


def airport_codes(value):
    codes = []
    for item in value.split("/"):
        place = place_key(item)
        if place in AIRPORTS:
            codes.append(AIRPORTS[place])
        else:
            known_codes, names, areas, aliases = airport_index()
            if place in known_codes:
                codes.append(place.upper())
                continue
            match = names.get(place) or areas.get(place) or aliases.get(place)
            if not match:
                raise ValueError(f"無法辨識地點「{item.strip()}」，請輸入機場英文名稱或三碼 IATA 代碼。")
            codes.append(match[-1])
    return tuple(dict.fromkeys(codes))
