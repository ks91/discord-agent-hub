#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
SRC_DIR = REPO_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from discord_agent_hub.event_markdown import render_event_markdown


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print("Usage: scripts/render-events-md.py <events.jsonl> [output.md]", file=sys.stderr)
        return 1

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    events = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))

    markdown = render_event_markdown(events)
    if len(sys.argv) == 3:
        output_path = Path(sys.argv[2])
        output_path.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
