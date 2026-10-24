"""Log buffer filtering and export — plain text, no rendering."""

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from docksurf_py.core.paths import DATA_DIR
from docksurf_py.core.types import LogLine


def line_matches(line: LogLine, term: str) -> bool:
    # Search matches on the message only, ignoring timestamp/service prefix.
    return not term or term.lower() in line.text.lower()


def buffer_to_text(lines: Iterable[LogLine], show_ts: bool = True) -> str:
    """Flatten a log buffer to plain text for export (timestamps + markers)."""
    out = []
    for line in lines:
        parts = []
        if show_ts and line.ts:
            parts.append(line.ts)
        if line.service:
            parts.append(f"{line.service} |")
        if line.stream == "stderr":
            parts.append("[stderr]")
        parts.append(line.text)
        out.append(" ".join(parts))
    return "\n".join(out)


def write_log_export(name: str, text: str) -> Path:
    """Write a log buffer to the exports subdir of DATA_DIR and return the path."""
    export_dir = DATA_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "logs"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = export_dir / f"{safe}-{stamp}.log"
    path.write_text(text, encoding="utf-8")
    return path
