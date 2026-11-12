"""Run a dispatched flight search and push its result to LINE."""

import json
import os
from pathlib import Path

from .line_webhook import deliver_results


def main():
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("請在 GitHub Actions Secrets 設定 LINE_CHANNEL_ACCESS_TOKEN")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    inputs = event["inputs"]
    deliver_results(token, inputs["target"], inputs["message"])


if __name__ == "__main__":
    main()
