"""指定飯店的 Google Hotels 訂房比價。"""

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import urlopen

import yaml

from .fetch import _connection_error, _serpapi_ssl_context
from .store import load_state, save_state

RECENT_PRICE_DAYS = 30


def localize_booking_link(link):
    url = urlsplit(link)
    host = url.hostname or ""
    if host == "www.google.com" and url.path == "/travel/lodging/clk":
        destination = dict(parse_qsl(url.query)).get("pcurl", "")
        if not destination.startswith("https://") or urlsplit(destination).hostname == host:
            return link
        localized = localize_booking_link(destination)
        return localized if localized != destination else link

    path = url.path
    netloc = url.netloc
    if host in ("agoda.com", "www.agoda.com"):
        parts = path.split("/")
        if len(parts) > 1 and len(parts[1]) == 5 and parts[1][2] == "-":
            parts[1] = "zh-tw"
            path = "/".join(parts)
        else:
            path = "/zh-tw" + path
        options = {"language": "zh-tw", "currency": "TWD"}
    elif host in ("booking.com", "www.booking.com"):
        options = {"lang": "zh-tw", "selected_currency": "TWD"}
    elif host in ("trip.com", "www.trip.com", "tw.trip.com"):
        netloc = "tw.trip.com"
        options = {"locale": "zh-TW", "curr": "TWD"}
    elif host in ("hotels.com", "www.hotels.com", "tw.hotels.com"):
        netloc = "tw.hotels.com"
        options = {"locale": "zh_TW", "pos": "HCOM.TW", "siteid": "300000045", "currency": "TWD"}
    else:
        return link
    query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
             if key.casefold() not in options]
    query.extend(options.items())
    return url._replace(netloc=netloc, path=path, query=urlencode(query)).geturl()


def request_hotels(params, key, context):
    query = urlencode(dict(params, engine="google_hotels", api_key=key))
    try:
        with urlopen("https://serpapi.com/search.json?" + query, timeout=30,
                     context=context) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise ValueError(f"SerpApi HTTP {exc.code}") from None
    except URLError as exc:
        raise ValueError(_connection_error(exc.reason)) from None
    except OSError as exc:
        raise ValueError(_connection_error(exc)) from None
    except ValueError:
        raise ValueError("SerpApi 回應不是有效的 JSON") from None
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("SerpApi 回傳錯誤（請查看服務後台）")
    return data


def resolve_property(data, name):
    def normalized(value):
        return " ".join(value.casefold().split())

    properties = data.get("properties") or []
    if data.get("name") and data.get("property_token"):
        properties = [data, *properties]
    matches = {
        hotel["property_token"]
        for hotel in properties
        if hotel.get("property_token") and normalized(hotel.get("name") or "") == normalized(name)
    }
    if len(matches) != 1:
        candidates = "；".join(
            f"{hotel.get('name', '')} ({hotel['property_token']})"
            for hotel in properties if hotel.get("property_token")
        )
        raise ValueError(f"無法以名稱唯一確認飯店。請在 routes.yaml 設定 search_name 或 property_token。候選：{candidates or '無'}")
    return matches.pop()


def rank_prices(details, providers=()):
    allowed = {provider.strip().casefold() for provider in providers}
    quotes = {}
    for offer in (details.get("prices") or []) + (details.get("featured_prices") or []):
        source = offer.get("source") or "未標示平台"
        if allowed and source.strip().casefold() not in allowed:
            continue
        total_rate = offer.get("total_rate") or {}
        for field, label in (("after_taxes_fees", "含稅費"), ("lowest", "稅費未標示"),
                             ("before_taxes_fees", "未稅費"), ("display_price", "稅費未標示")):
            total = total_rate.get("extracted_" + field)
            if type(total) in (int, float) and math.isfinite(total) and total > 0:
                break
        else:
            continue
        nightly_rate = offer.get("rate_per_night") or {}
        nightly = nightly_rate.get("extracted_" + field)
        link = offer.get("link") or ""
        if urlsplit(link).scheme not in ("http", "https"):
            link = ""
        identity = (source.casefold(), total, label)
        if identity not in quotes or (link and not quotes[identity][-1]):
            quotes[identity] = (total, label, nightly, source, link)
    return sorted(quotes.values(), key=lambda quote: quote[:2])


def typical_price_text(details, currency):
    price_range = details.get("typical_price_range") or {}
    low = price_range.get("extracted_lowest")
    high = price_range.get("extracted_highest")
    if all(type(value) in (int, float) and math.isfinite(value) and value > 0
           for value in (low, high)) and low <= high:
        return f"{low:,}–{high:,} {currency}"
    return "未提供"


def recent_prices(history, args, token, quotes, now):
    cutoff = (now - dt.timedelta(days=RECENT_PRICE_DAYS)).isoformat()
    checked = now.isoformat()
    recent = {}
    observed = {}
    for total, taxes, _, source, _ in quotes:
        provider = source.strip().casefold()
        group = (provider, taxes)
        if group in observed:
            continue
        key = json.dumps([token, args.check_in.isoformat(), args.check_out.isoformat(),
                          args.adults, args.currency, provider, taxes], ensure_ascii=False)
        past = [price for checked_at, price in history.get(key, {}).get("prices", [])
                if cutoff <= checked_at < checked]
        if past:
            recent[group] = (min(past), max(past), len(past))
        observed[group] = (key, total)
    for key, total in observed.values():
        prices = [row for row in history.get(key, {}).get("prices", []) if cutoff <= row[0] < checked]
        history[key] = {"prices": [*prices, [checked, total]]}
    return recent


def load_searches(path):
    with path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    entries = config.get("hotels", [])
    if not isinstance(entries, list):
        raise ValueError("hotels 必須是清單")
    settings = config.get("settings") or {}
    searches = []
    for index, entry in enumerate(entries, 1):
        try:
            hotel = entry.get("name")
            token = entry.get("property_token")
            search_name = entry.get("search_name")
            hotels_com_tw_url = entry.get("hotels_com_tw_url")
            if hotels_com_tw_url is not None:
                if not isinstance(hotels_com_tw_url, str):
                    raise ValueError("hotels_com_tw_url 必須是 Hotels.com 台灣站的飯店網址")
                url = urlsplit(hotels_com_tw_url)
                if url.scheme != "https" or url.netloc != "tw.hotels.com" or not url.path.startswith("/ho"):
                    raise ValueError("hotels_com_tw_url 必須是 Hotels.com 台灣站的飯店網址")
            if any(value is not None and (not isinstance(value, str) or not value.strip())
                   for value in (hotel, token, search_name)) or not (hotel or token):
                raise ValueError("請填寫 name 或 property_token")
            check_in = dt.date.fromisoformat(str(entry["check_in"]))
            check_out = dt.date.fromisoformat(str(entry["check_out"]))
            adults = entry.get("adults", settings.get("adults", 2))
            top = entry.get("top", 10)
            notify_drop = entry.get("notify_drop")
            if notify_drop is not None and (type(notify_drop) is not int or notify_drop < 1):
                raise ValueError("notify_drop 必須是正整數或 null")
            if notify_drop is None and "notify_drop" in entry:
                notify_drop = 0
            providers = entry.get("providers", [])
            if not isinstance(providers, list) or any(
                not isinstance(provider, str) or not provider.strip() for provider in providers
            ):
                raise ValueError("providers 必須是非空白的平台名稱清單")
            currency = entry.get("currency", settings.get("currency", "TWD")).upper()
            if check_out <= check_in or any(type(value) is not int or value < 1
                                           for value in (adults, top)):
                raise ValueError("退房須晚於入住，成人數及排名筆數須為正整數")
        except (AttributeError, KeyError, ValueError) as exc:
            raise ValueError(f"hotels 第 {index} 筆設定有誤：{exc}") from None
        searches.append(SimpleNamespace(hotel=hotel, property_token=token, search_name=search_name, check_in=check_in,
                                        check_out=check_out, adults=adults, top=top, currency=currency,
                                        providers=providers, hotels_com_tw_url=hotels_com_tw_url,
                                        notify_drop=notify_drop))
    return searches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="包含 routes.yaml 的專案根目錄")
    args = parser.parse_args()
    try:
        searches = load_searches(Path(args.root) / "routes.yaml")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.exit(1, f"無法讀取飯店設定：{exc}\n")
    if not searches:
        print("routes.yaml 尚未設定飯店查詢")
        return
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not key:
        parser.error("未設定 SERPAPI_API_KEY")

    try:
        context = _serpapi_ssl_context()
    except ImportError:
        parser.exit(1, "缺少 truststore，請安裝 requirements.txt\n")
    except (OSError, ValueError):
        parser.exit(1, "SerpApi 憑證初始化失敗，請檢查 SERPAPI_CA_BUNDLE\n")
    history_path = Path(args.root) / "data" / "hotel_history.json"
    history = load_state(history_path)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=RECENT_PRICE_DAYS)).isoformat()
    history = {key: value for key, value in history.items()
               if any(row[0] >= cutoff for row in value["prices"])}
    all_found = True
    alerts = []
    line_alerts = []
    for search in searches:
        try:
            if not run_search(search, key, context, history, now, alerts, line_alerts):
                all_found = False
        except ValueError as exc:
            print(f"{search.hotel or search.property_token}：{exc}")
            all_found = False
    if history or history_path.exists():
        save_state(history_path, history)
    alerts_file = os.environ.get("HOTEL_ALERTS_FILE")
    if alerts_file and alerts:
        Path(alerts_file).write_text("\n".join(alerts) + "\n", encoding="utf-8")
    line_file = os.environ.get("HOTEL_LINE_ALERTS_FILE")
    if line_file and line_alerts:
        Path(line_file).write_text("\n\n".join(line_alerts) + "\n", encoding="utf-8")
        print(f"已準備 LINE 推播，共 {len(line_alerts)} 組飯店與入住日期")
    if alerts:
        print(f"\n🔔 降價通知：共 {len(alerts)} 筆")
        print("\n".join(alerts))
    if not all_found:
        raise SystemExit(1)


def run_search(args, key, context, history, now, alerts, line_alerts):
    nights = (args.check_out - args.check_in).days
    params = dict(check_in_date=args.check_in.isoformat(), check_out_date=args.check_out.isoformat(),
                  adults=args.adults, currency=args.currency, gl="tw", hl="zh-tw")
    token = args.property_token
    if not token:
        name = args.search_name or args.hotel
        search_params = dict(params, q=name)
        if args.search_name:
            search_params["hl"] = "en"
        search = request_hotels(search_params, key, context)
        token = resolve_property(search, name)
    details = request_hotels(dict(params, property_token=token), key, context)
    if not details.get("name"):
        raise ValueError("SerpApi 未回傳指定飯店的詳細資料")
    ranked = rank_prices(details, args.providers)
    recent = recent_prices(history, args, token, ranked, now)
    quotes = ranked[:args.top]

    title = (f"{details['name']}｜{args.check_in} → {args.check_out}｜"
             f"{nights} 晚、{args.adults} 位成人｜最便宜 {len(quotes)} 筆（{args.currency}）")
    print(title)
    typical = f"Google Hotels 常態價格區間：{typical_price_text(details, args.currency)}"
    print(typical)
    recent_label = f"近期價格範圍（近 {RECENT_PRICE_DAYS} 天）"
    lines = [f"### {title}", "", typical, "",
             f"| # | 全程總價 | 每晚 | 訂房平台 | {recent_label} | 報價連結 |",
             "|---|---:|---:|---|---|---|"]
    for index, (total, taxes, nightly, source, link) in enumerate(quotes, 1):
        total_text = f"{total:,}（{taxes}）"
        nightly_text = f"{nightly:,}" if type(nightly) in (int, float) else "—"
        past = recent.get((source.strip().casefold(), taxes))
        price_history = "無歷史紀錄"
        if past:
            low, high, count = past
            price_history = f"{low:,}–{high:,} {args.currency}（{count} 筆）"
        print(f"{index:>2}. 總價 {total_text}｜每晚 {nightly_text}｜{source}｜{recent_label}：{price_history}")
        if source.strip().casefold() == "hotels.com" and args.hotels_com_tw_url:
            url = urlsplit(args.hotels_com_tw_url)
            stay = urlencode({"chkin": args.check_in, "chkout": args.check_out, "adults": args.adults})
            link = url._replace(query=stay).geturl()
        link = localize_booking_link(link)
        if link:
            print(f"    報價連結：{link}")
        source = source.replace("|", "&#124;").replace("\n", " ")
        booking = f"[查看](<{link}>)" if link else "—"
        lines.append(f"| {index} | {total_text} | {nightly_text} | {source} | {price_history} | {booking} |")
    seen = set()
    line_rows = []
    if args.notify_drop is None:
        print("通知判斷｜未設定 notify_drop")
    for total, taxes, _, source, _ in ranked:
        group = (source.strip().casefold(), taxes)
        if group in seen:
            continue
        seen.add(group)
        past = recent.get(group)
        if not past:
            print(f"通知判斷｜{source}（{taxes}）：近期搜尋價格 0 筆，加入 LINE 推播")
            history_text = "0 筆"
        elif args.notify_drop is None:
            continue
        elif past[0] - total < args.notify_drop:
            print(f"通知判斷｜{source}（{taxes}）：本次 {total:,}，前 {RECENT_PRICE_DAYS} 天最低 "
                  f"{past[0]:,}，需低至少 {args.notify_drop:,} {args.currency}，未達門檻")
            continue
        else:
            low, high, count = past
            message = (f"{details['name']}｜{args.check_in} → {args.check_out}｜"
                       f"{args.adults} 位成人｜{source}（{taxes}）｜全程總價 {total:,} {args.currency}，"
                       f"比近 {RECENT_PRICE_DAYS} 天最低價 {low:,} 低 {low - total:,}")
            alerts.append(f"- {message}")
            history_text = f"{low:,}–{high:,} {args.currency}（{count} 筆）"
        line_rows.append(
            f"{source}（{taxes}）｜全程總價 {total:,} {args.currency}\n"
            f"近期搜尋價格（近 {RECENT_PRICE_DAYS} 天）：{history_text}"
        )
    if line_rows:
        heading = f"{details['name']}｜{args.check_in} → {args.check_out}"
        line_alerts.append(heading + "\n\n" + "\n\n".join(line_rows))
    if not quotes:
        message = "查無附全程總價的訂房平台報價"
        if args.providers:
            message = "指定平台查無附全程總價的報價（" + "、".join(args.providers) + "）"
        print(message)
        lines.append(f"| — | {message} | | | | |")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n\n")
    return bool(quotes)


if __name__ == "__main__":
    main()
