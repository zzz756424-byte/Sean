"""Try a LINE flight search from the PyCharm Run console."""

from pathlib import Path

from flightseeker import line_webhook
from flightseeker.line_webhook import SEARCH_COMMANDS, flight_results, parse_search


def main():
    print("請貼上完整的查機票訊息，貼完後再按 Enter 送出空白行。")
    print(f"執行檔：{Path(__file__).resolve()}", flush=True)
    print(f"查詢模組：{Path(line_webhook.__file__).resolve()}", flush=True)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)

    if not lines:
        return
    command = lines[0].strip()
    if command not in SEARCH_COMMANDS:
        raise SystemExit("第一行請輸入「查機票」或「查機票*」。")
    print("收到的查詢內容：", *lines, sep="\n", flush=True)
    try:
        queries = parse_search("\n".join(lines))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    routes = "、".join(dict.fromkeys(f"{origin}→{dest}" for origin, dest, *_ in queries))
    if len({origin for origin, *_ in queries}) > 1:
        try:
            confirmation = input(f"收到多個出發機場（{routes}）。確認查詢請輸入 y：")
        except EOFError:
            return
        if confirmation.strip().lower() != "y":
            print("已取消查詢。若只查台中，請重新執行並填「去程地點:RMQ」。")
            return
    print(f"正在查詢 {len(queries)} 組機場與日期（{routes}）…", flush=True)
    for page in flight_results(queries, serpapi_only=command == "查機票*"):
        print(f"\n{page}", flush=True)


if __name__ == "__main__":
    main()
