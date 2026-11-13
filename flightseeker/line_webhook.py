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
from urllib.request import Request, urlopen

from .airports import airport_codes
from .fetch import EmptyResult, _flight_times, google_query, google_results

EXAMPLE = """查機票
去程地點:台中/台北
回程地點:沖繩
最早去程時間:2027/4/10
最晚回程時間:2027/4/17
最短遊玩天數:7"""


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


def direct_flights(origin, dest, date):
    from fast_flights import FlightQuery

    leg = FlightQuery(date=date.isoformat(), from_airport=origin, to_airport=dest, max_stops=0)
    started = perf_counter()
    retry_wait = 0

    def record_retry(delay):
        nonlocal retry_wait
        retry_wait += delay

    try:
        results = google_results(google_query([leg], "one-way"), on_retry=record_retry)
    except EmptyResult:
        return {}
    finally:
        print(f"Google Flights {origin}→{dest} {date}："
              f"{perf_counter() - started:.2f} 秒（重試等待 {retry_wait} 秒）", flush=True)

    flights = {}
    for flight in results:
        if len(flight.flights or []) != 1 or not flight.price:
            continue
        segment = flight.flights[0]
        times = (
            dt.date(*segment.departure.date),
            dt.date(*segment.arrival.date),
            _flight_times(flight.flights),
        )
        for name in (flight.airlines or []):
            if name and name.strip():
                airline = name.strip()
                key = (airline.casefold(), *times)
                fare = int(flight.price)
                if key not in flights or fare < flights[key][1]:
                    flights[key] = (airline, fare)
    return flights


def flight_results(queries):
    queries = tuple(queries)
    legs = dict.fromkeys(
        leg
        for origin, dest, depart, ret, _ in queries
        for leg in ((origin, dest, depart), (dest, origin, ret))
    )
    cache = {}
    failed_legs = set()
    if legs:
        with ThreadPoolExecutor(max_workers=4) as executor:
            pending = {executor.submit(direct_flights, *leg): leg for leg in legs}
            for future in as_completed(pending):
                leg = pending[future]
                try:
                    cache[leg] = future.result()
                except Exception:
                    logging.exception("Google Flights search failed for %s-%s %s", *leg)
                    cache[leg] = {}
                    failed_legs.add(leg)

    matches_by_date = {}
    pending_returns = {}
    page = ""
    outbound_failures = set()
    for origin, dest, depart, ret, days in queries:
        outbound_key = (origin, dest, depart)
        inbound_key = (dest, origin, ret)
        if outbound_key in failed_legs:
            outbound_failures.add(outbound_key)
        outbound = cache[outbound_key]
        inbound = cache[inbound_key]
        if inbound_key in failed_legs:
            for (_, date, arrival, times), (airline, fare) in outbound.items():
                if (ret - arrival).days + 1 >= days:
                    pending_returns.setdefault((date, ret), []).append(
                        (fare, date, ret, origin, dest, times, None, airline)
                    )
            continue
        returns_by_airline = {}
        for (code, date, _, times), (_, fare) in inbound.items():
            returns_by_airline.setdefault(code, []).append((date, times, fare))
        for (code, date, arrival, times), (airline, fare) in outbound.items():
            for back_date, back_times, back_fare in returns_by_airline.get(code, ()):
                if (back_date - arrival).days + 1 >= days:
                    matches_by_date.setdefault((date, back_date), []).append(
                        (fare + back_fare, origin, dest, times, back_times, airline)
                    )
    cheapest = [
        (total, date, back_date, origin, dest, times, back_times, airline)
        for (date, back_date), matches in matches_by_date.items()
        for total, origin, dest, times, back_times, airline in sorted(matches)[:5]
    ]
    remaining = [
        row
        for dates, matches in pending_returns.items()
        for row in sorted(matches)[:max(0, 5 - len(matches_by_date.get(dates, ())))]
    ]
    for total, date, back_date, origin, dest, times, back_times, airline in (
        sorted(cheapest) + sorted(remaining)
    ):
        back_time_text = back_times or "回程時間未查到"
        price_text = (f"本次估算總價：{total:,} TWD" if back_times
                      else f"去程單程票價：{total:,} TWD｜來回總價未取得")
        block = (
            f"{origin} → {dest}｜{date} {times}\n"
            f"{dest} → {origin}｜{back_date} {back_time_text}\n"
            f"航空公司：{airline}\n"
            f"{price_text}"
        )
        if len(page) + len(block) + 2 > 4500:
            yield page
            page = ""
        page = f"{page}\n\n{block}" if page else block
    if page:
        yield page
    if outbound_failures:
        yield f"有 {len(outbound_failures)} 個去程航段在重試後仍未取得 Google Flights 結果，結果可能不完整。"
    elif not page:
        yield "查無去回程皆直飛的同一家航空公司。"


def push(token, target, message):
    request = Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": target, "messages": [{"type": "text", "text": message}]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15):
        pass


def deliver_results(token, target, message):
    try:
        query = parse_search(message)
    except ValueError as exc:
        push(token, target, EXAMPLE if message.strip() == "查機票" else f"{exc}\n\n{EXAMPLE}")
        return
    try:
        search_started = perf_counter()
        pages = list(flight_results(query))
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
                    if not lines or lines[0].strip() != "查機票":
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
