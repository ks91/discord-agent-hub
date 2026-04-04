from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from discord_agent_hub.models import utc_now


class StructuredLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, **payload: Any) -> None:
        record = {
            "ts": utc_now(),
            "event": event_type,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
