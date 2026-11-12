"""Try a LINE flight search from the PyCharm Run console."""

from flightseeker.line_webhook import EXAMPLE, flight_results, parse_search


def main():
    print("請貼上完整的查機票訊息，貼完後再按 Enter 送出空白行。")
    print(f"\n{EXAMPLE}\n", flush=True)
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
    if lines[0].strip() != "查機票":
        raise SystemExit("第一行請輸入「查機票」。")
    try:
        queries = parse_search("\n".join(lines))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    print(f"正在查詢 {len(queries)} 組機場與日期…", flush=True)
    for page in flight_results(queries):
        print(f"\n{page}", flush=True)


if __name__ == "__main__":
    main()
