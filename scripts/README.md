# scripts

Small operational and analysis utilities for `discord-agent-hub`.

## `hubctl.sh`

Background process control for the hub.

Usage:

```bash
scripts/hubctl.sh start
scripts/hubctl.sh stop
scripts/hubctl.sh restart
scripts/hubctl.sh status
scripts/hubctl.sh logs
```

Defaults:

- Python executable: `./.venv/bin/python`
- PID file: `./run/hub.pid`
- Log file: `./logs/hub.log`

Optional environment variables:

- `HUB_PYTHON_BIN`
- `HUB_PID_FILE`
- `HUB_LOG_FILE`

## `render-events-md.py`

Render exported JSONL event logs into a more readable Markdown timeline.

Usage:

```bash
scripts/render-events-md.py path/to/events.jsonl
scripts/render-events-md.py path/to/events.jsonl output.md
```

Notes:

- This script can be run directly from the repository root.
- It reads `src/` automatically, so it does not require an editable install just to render exported logs.
