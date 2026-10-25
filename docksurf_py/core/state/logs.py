"""Log viewer state: the bounded line buffer, filter, match navigation and
export — everything about a log view except streaming and painting it."""

import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterable

from rich.markup import escape

from docksurf_py.core.paths import DATA_DIR
from docksurf_py.core.render.logs import render_log_line
from docksurf_py.core.types import LogLine

# Cap on buffered lines so tailing a noisy container can't grow memory without
# bound. Export only includes the most recent `LOG_BUFFER_MAXLEN` lines.
LOG_BUFFER_MAXLEN = 20_000


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


class LogBuffer:
    """The lines of one log view and how they're filtered and navigated.

    While a filter is active every visible line is a match, so the k-th match
    is visible row k (without wrapping): `match_count` is the visible count and
    `match_cursor` the jump position, -1 until the user navigates.
    """

    def __init__(self, maxlen: int = LOG_BUFFER_MAXLEN) -> None:
        self.lines: deque[LogLine] = deque(maxlen=maxlen)
        self.filter = ""
        self.show_timestamps = False
        self.match_count = 0
        self.match_cursor = -1

    def reset(self, *, keep_filter: bool) -> None:
        """Empty the buffer and match state — a new source (`keep_filter=False`)
        or the same source re-subscribed with new options (`True`)."""
        self.lines.clear()
        if not keep_filter:
            self.filter = ""
        self.match_count = 0
        self.match_cursor = -1

    def set_filter(self, term: str) -> None:
        """A new term resets the jump cursor; the count is recomputed by
        `render_all`."""
        self.filter = term
        self.match_cursor = -1

    def toggle_timestamps(self) -> bool:
        self.show_timestamps = not self.show_timestamps
        return self.show_timestamps

    def render_all(self) -> list[str]:
        """Markup for every line that passes the filter, recounting matches."""
        rendered = [
            render_log_line(line, self.filter, self.show_timestamps)
            for line in self.lines
            if line_matches(line, self.filter)
        ]
        self.match_count = len(rendered) if self.filter else 0
        if self.match_cursor >= self.match_count:
            self.match_cursor = self.match_count - 1
        return rendered

    def ingest(self, batch: Iterable[LogLine], *, render: bool) -> list[str]:
        """Store `batch`; when `render`, also return markup for the lines that
        pass the filter (counted as matches). Paused views store without
        rendering and catch up with `render_all` on resume."""
        batch = list(batch)
        self.lines.extend(batch)
        if not render:
            return []
        rendered = [
            render_log_line(line, self.filter, self.show_timestamps)
            for line in batch
            if line_matches(line, self.filter)
        ]
        if self.filter:
            self.match_count += len(rendered)
        return rendered

    def jump(self, delta: int) -> int | None:
        """Move the match cursor by `delta`, wrapping like a pager. Returns the
        new cursor row, or None when there are no matches."""
        if self.match_count == 0:
            return None
        if self.match_cursor < 0:
            self.match_cursor = 0 if delta > 0 else self.match_count - 1
        else:
            self.match_cursor = (self.match_cursor + delta) % self.match_count
        return self.match_cursor

    def filter_hint(self) -> str:
        """The header's filter segment (markup), or "" when no filter is set."""
        if not self.filter:
            return ""
        if self.match_count == 0:
            match = "  |  [dim]no matches[/]"
        elif self.match_cursor >= 0:
            match = f"  |  match {self.match_cursor + 1}/{self.match_count}"
        else:
            match = f"  |  {self.match_count} matches"
        return f"  |  filter: [bold]{escape(self.filter)}[/]{match}"

    def export_text(self) -> str:
        """The whole buffer as plain text, regardless of filter or toggles."""
        return buffer_to_text(self.lines, show_ts=True)
