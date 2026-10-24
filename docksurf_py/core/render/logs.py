"""Rich-markup rendering of log lines and search-term highlighting."""

import re

from rich.markup import escape

from docksurf_py.core.types import LogLine, SafeMarkup


def highlight_match(line: str, term: str) -> str:
    """Return a Rich markup string with every occurrence of term highlighted."""
    if isinstance(line, SafeMarkup):
        return line
    if not term:
        return escape(line)
    parts = re.split(f"({re.escape(term)})", line, flags=re.IGNORECASE)
    return "".join(
        f"[bold yellow]{escape(p)}[/]" if p.lower() == term.lower() else escape(p)
        for p in parts
    )


def render_log_line(line: LogLine, term: str, show_ts: bool) -> str:
    """Build a Rich-markup string for one `LogLine`.

    Composes an optional colour-coded service prefix (merged project logs), an
    optional dim timestamp, the message with search matches highlighted, and a
    dim-red wrap for stderr lines. Search-highlight is layered *inside* the
    stderr style so matches stay visible on error lines.
    """
    prefix = f"[{line.color}]{escape(line.service):>14}[/] │ " if line.service else ""
    ts = f"[dim]{escape(line.ts)}[/] " if show_ts and line.ts else ""
    body = highlight_match(line.text, term)
    if line.stream == "stderr":
        body = f"[dim red]{body}[/]"
    return f"{prefix}{ts}{body}"
