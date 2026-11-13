"""LINE flight search webhook. Run with python -m flightseeker.line_webhook."""

import base64
import datetime as dt
import hmac
import json
import logging
import os
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from unicodedata import normalize
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .airports import airport_codes
from .fetch import EmptyResult, _flight_times, _serpapi_ssl_context, google_query, google_results

EXAMPLE = """查機票
去程地點:台中/台北
回程地點:沖繩
最早去程時間:2027/3/20
最晚回程時間:2027/3/26
最短遊玩天數:7"""
SEARCH_COMMANDS = {"查機票", "查機票*"}


def parse_search(message):
    lines = normalize("NFKC", message).strip().splitlines()[1:]
    fields = {}
    for line in lines:
        if not line.strip():
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        if not value.strip() or name in fields:
            raise ValueError("請檢查欄位格式與重複欄位。")
        fields[name] = value.strip()
    if fields.keys() != {line.partition(":")[0] for line in EXAMPLE.splitlines()[1:]}:
        raise ValueError("請填寫範例中的五個欄位。")
    try:
        start, end = (dt.datetime.strptime(fields[field], "%Y/%m/%d").date()
                      for field in ("最早去程時間", "最晚回程時間"))
        days = int(fields["最短遊玩天數"])
    except ValueError as exc:
        raise ValueError("日期請使用 YYYY/MM/DD，遊玩天數請填整數。") from exc
    slack = (end - start).days + 1 - days
    if days < 1 or slack < 0:
        raise ValueError("日期範圍需容納最短遊玩天數。")
    routes = [(origin, dest) for origin in airport_codes(fields["去程地點"])
              for dest in airport_codes(fields["回程地點"]) if origin != dest]
    if not routes:
        raise ValueError("出發和目的地機場須不同。")
    if len(routes) * (slack + 1) * (slack + 2) // 2 > 30:
        raise ValueError("超過 30 組機場與日期查詢，請縮小日期或機場範圍。")
    return [(origin, dest, start + dt.timedelta(days=out), start + dt.timedelta(days=back), days)
            for origin, dest in routes
            for out in range(slack + 1)
            for back in range(out + days - 1, days + slack)]


def google_round_trip(origin, dest, depart, ret, days):
    from fast_flights import FlightQuery

    legs = [FlightQuery(date=depart.isoformat(), from_airport=origin, to_airport=dest, max_stops=1),
            FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin, max_stops=0)]
    try:
        results = google_results(google_query(legs, "round-trip"))
    except EmptyResult:
        return []

    rows = []
    for option in results:
        if len(option.flights or []) != 1 or not option.price or not option.airlines:
            continue
        arrival = dt.date(*option.flights[0].arrival.date)
        if (ret - arrival).days + 1 >= days:
            airline = "／".join(name for name in option.airlines if name)
            if airline:
                rows.append((int(option.price), depart, ret, origin, dest,
                             _flight_times(option.flights), None, airline))
    if not rows:
        return rows

    try:
        return_options = google_results(google_query([legs[1]], "one-way"))
        return_times = {}
        for option in return_options:
            if len(option.flights or []) != 1:
                continue
            if dt.date(*option.flights[0].departure.date) != ret:
                continue
            for name in option.airlines or []:
                if name:
                    return_times.setdefault(name.strip().casefold(), set()).add(
                        _flight_times(option.flights))
    except EmptyResult:
        return rows
    except Exception:
        logging.exception("Google Flights return reference failed for %s-%s %s", dest, origin, ret)
        return rows

    for index, row in enumerate(rows):
        airline = row[-1].strip().casefold()
        times = return_times.get(airline)
        if times:
            reference = "回程時間參考：" + "／".join(sorted(times))
            rows[index] = (*row[:6], reference, row[-1])
    return rows


def serpapi_options(params):
    with urlopen("https://serpapi.com/search.json?" + urlencode(params), timeout=30,
                 context=_serpapi_ssl_context()) as response:
        data = json.load(response)
    if data.get("error"):
        raise ValueError("SerpApi returned an error")
    return (data.get("best_flights") or []) + (data.get("other_flights") or [])


def serpapi_direct_segment(option, origin, dest, date):
    segments = option.get("flights") or []
    if len(segments) != 1:
        return None
    segment = segments[0]
    departure = segment.get("departure_airport") or {}
    arrival = segment.get("arrival_airport") or {}
    if departure.get("id") != origin or arrival.get("id") != dest:
        return None
    try:
        start = dt.datetime.fromisoformat(departure["time"])
        end = dt.datetime.fromisoformat(arrival["time"])
    except (KeyError, TypeError, ValueError):
        return None
    airline = (segment.get("airline") or "").strip()
    if not airline or start.date() != date:
        return None
    shift = "+1" if end.date() != start.date() else ""
    return airline, end.date(), f"{start:%H:%M}→{end:%H:%M}{shift}"


def serpapi_round_trip(origin, dest, depart, ret, days, api_key):
    params = dict(engine="google_flights", api_key=api_key, type=1, stops=1,
                  departure_id=origin, arrival_id=dest, outbound_date=depart.isoformat(),
                  return_date=ret.isoformat(), adults=1, travel_class=1, currency="TWD", hl="en", gl="tw")
    rows = []
    failed = False
    seen_tokens = set()
    for option in serpapi_options(params):
        outbound = serpapi_direct_segment(option, origin, dest, depart)
        token = option.get("departure_token")
        if not outbound or not token or token in seen_tokens:
            continue
        airline, arrival, times = outbound
        if (ret - arrival).days + 1 < days:
            continue
        seen_tokens.add(token)
        try:
            returns = serpapi_options(dict(params, departure_token=token))
        except Exception as exc:
            logging.warning("SerpApi return search failed (%s)", type(exc).__name__)
            failed = True
            continue
        for offer in returns:
            inbound = serpapi_direct_segment(offer, dest, origin, ret)
            fare = offer.get("price")
            if inbound and inbound[0].casefold() == airline.casefold() and type(fare) in (int, float) and fare > 0:
                rows.append((int(fare), depart, ret, origin, dest, times, inbound[2], airline))
    return rows, failed


def fill_serpapi_return_times(rows, api_key):
    missing = {(dest, origin, ret) for _, _, ret, origin, dest, _, times, _ in rows if not times}

    def lookup(leg):
        origin, dest, date = leg
        params = dict(engine="google_flights", api_key=api_key, type=2, stops=1,
                      departure_id=origin, arrival_id=dest, outbound_date=date.isoformat(),
                      adults=1, travel_class=1, currency="TWD", hl="en", gl="tw")
        times = {}
        for option in serpapi_options(params):
            segment = serpapi_direct_segment(option, origin, dest, date)
            if segment:
                airline, _, time = segment
                times.setdefault(airline.casefold(), set()).add(time)
        return times

    references = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = {executor.submit(lookup, leg): leg for leg in missing}
        for future in as_completed(pending):
            leg = pending[future]
            try:
                references[leg] = future.result()
            except Exception as exc:
                logging.warning("SerpApi return reference failed for %s-%s %s (%s)",
                                *leg, type(exc).__name__)

    for index, row in enumerate(rows):
        _, _, ret, origin, dest, _, times, airline = row
        if not times:
            matches = references.get((dest, origin, ret), {}).get(airline.strip().casefold())
            if matches:
                reference = "回程時間參考（SerpApi）：" + "／".join(sorted(matches))
                rows[index] = (*row[:6], reference, airline)


def flight_results(queries, *, serpapi_only=False):
    queries = tuple(dict.fromkeys(queries))
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if serpapi_only and not api_key:
        yield "查機票* 需要設定 SERPAPI_API_KEY。"
        return
    estimates = []
    failed_queries = set()
    if queries and not serpapi_only:
        print(f"Google Flights 先查詢 {len(queries)} 組來回日期", flush=True)
        with ThreadPoolExecutor(max_workers=4) as executor:
            pending = {executor.submit(google_round_trip, *query): query for query in queries}
            for future in as_completed(pending):
                query = pending[future]
                try:
                    estimates.extend(future.result())
                except Exception:
                    logging.exception("Google Flights round-trip search failed for %s-%s %s–%s", *query[:4])
                    failed_queries.add(query)

        print(f"Google Flights 查詢完成：{len(estimates)} 筆符合條件的來回起價，"
              f"{len(failed_queries)} 組查詢失敗", flush=True)

    confirmed = []
    fallback_failed = False
    if api_key and any(not row[6] for row in estimates):
        fill_serpapi_return_times(estimates, api_key)
    if queries and not estimates and api_key:
        action = "直接查詢" if serpapi_only else "補查"
        print(f"SerpApi {action} {len(queries)} 組來回日期", flush=True)
        with ThreadPoolExecutor(max_workers=4) as executor:
            pending = {executor.submit(serpapi_round_trip, *query, api_key): query for query in queries}
            for future in as_completed(pending):
                query = pending[future]
                try:
                    rows, partial_failure = future.result()
                    confirmed.extend(rows)
                    fallback_failed |= partial_failure
                    failed_queries.discard(query)
                except Exception as exc:
                    logging.warning("SerpApi round-trip search failed for %s-%s %s–%s (%s)",
                                    *query[:4], type(exc).__name__)
                    fallback_failed = True
    yield from format_flights(confirmed or estimates, failed_queries, fallback_failed)



def format_flights(rows, failed_queries, fallback_failed):
    best_by_trip = {}
    for fare, *itinerary in rows:
        key = tuple(itinerary)
        best_by_trip[key] = min(fare, best_by_trip.get(key, fare))
    by_date = {}
    for itinerary, fare in best_by_trip.items():
        by_date.setdefault(itinerary[:2], []).append((fare, *itinerary))
    cheapest = [row for group in by_date.values() for row in sorted(group)[:5]]

    page = ""
    for total, date, back_date, origin, dest, times, back_times, airline in sorted(cheapest):
        back_time_text = back_times or "回程時間未查到"
        price_label = "來回起價" if not back_times or back_times.startswith("回程時間參考") else "來回總價"
        block = (
            f"{origin} → {dest}｜{date} {times}\n"
            f"{dest} → {origin}｜{back_date} {back_time_text}\n"
            f"航空公司：{airline}\n"
            f"{price_label}：{total:,} TWD"
        )
        if len(page) + len(block) + 2 > 4500:
            yield page
            page = ""
        page = f"{page}\n\n{block}" if page else block
    if page:
        yield page
    if failed_queries:
        yield f"有 {len(failed_queries)} 組日期未取得 Google Flights 來回結果。"
    if fallback_failed:
        yield "部分 SerpApi 補查未取得資料。"
    if not page and not failed_queries and not fallback_failed:
        yield "查無符合條件的來回票價。"



def push(token, target, message):
    request = Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": target, "messages": [{"type": "text", "text": message}]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15):
        pass


def deliver_results(token, target, message):
    command = message.strip().partition("\n")[0].strip()
    serpapi_only = command == "查機票*"
    example = EXAMPLE.replace("查機票", "查機票*", 1) if serpapi_only else EXAMPLE
    try:
        query = parse_search(message)
    except ValueError as exc:
        push(token, target, example if message.strip() in SEARCH_COMMANDS else f"{exc}\n\n{example}")
        return
    try:
        search_started = perf_counter()
        pages = list(flight_results(query, serpapi_only=serpapi_only))
    except Exception:
        logging.exception("Flight search unavailable")
        push(token, target, "暫時無法查詢機票，請稍後再試。")
        raise
    print(f"Google Flights 查價與整理：{perf_counter() - search_started:.2f} 秒", flush=True)
    push_started = perf_counter()
    for page in pages:
        push(token, target, page)
    print(f"LINE 推播（{len(pages)} 則）：{perf_counter() - push_started:.2f} 秒", flush=True)


def dispatch_workflow(token, repository, ref, target, message):
    request = Request(
        f"https://api.github.com/repos/{repository}/actions/workflows/LineFlightSearch.yml/dispatches",
        data=json.dumps({"ref": ref, "inputs": {"target": target, "message": message}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=8):
        pass


def make_handler(secret, dispatch):
    seen = {}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/line/webhook":
                self.send_error(404)
                return
            length = self.headers.get("Content-Length", "")
            if not length.isdecimal() or not 0 < int(length) <= 1024 * 1024:
                self.send_error(400)
                return
            body = self.rfile.read(int(length))
            signature = base64.b64encode(hmac.digest(secret, body, "sha256"))
            if not hmac.compare_digest(signature, self.headers.get("X-Line-Signature", "").encode()):
                self.send_error(403)
                return
            try:
                for event in json.loads(body)["events"]:
                    message = event.get("message", {})
                    if (event.get("type") != "message" or message.get("type") != "text"
                            or event.get("mode") == "standby"):
                        continue
                    lines = message["text"].strip().splitlines()
                    if not lines or lines[0].strip() not in SEARCH_COMMANDS:
                        continue
                    source = event["source"]
                    target = source[{"user": "userId", "group": "groupId", "room": "roomId"}[source["type"]]]
                    event_id = event["webhookEventId"]
                    with lock:
                        if event_id in seen:
                            continue
                        dispatch(target, message["text"])
                        seen[event_id] = None
                        if len(seen) > 4096:
                            del seen[next(iter(seen))]
            except OSError:
                logging.exception("GitHub Actions dispatch failed")
                self.send_error(503)
                return
            except (ValueError, KeyError, TypeError, AttributeError):
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def main():
    secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
    token = os.environ.get("GITHUB_DISPATCH_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    ref = os.environ.get("GITHUB_DISPATCH_REF", "main").strip()
    if not secret or not token or not repository or not ref:
        raise SystemExit("請設定 LINE_CHANNEL_SECRET、GITHUB_DISPATCH_TOKEN 和 GITHUB_REPOSITORY")

    def dispatch(target, message):
        dispatch_workflow(token, repository, ref, target, message)

    with ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))),
                             make_handler(secret.encode(), dispatch)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
