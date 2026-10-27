"""Container table cells: pure builders for the Containers tab rows (Tide)."""

import os
import re
from pathlib import Path

from rich.markup import escape

from docksurf_py.core.docker import format_size
from docksurf_py.core.models import (
    ComposeProject,
    Container,
    ContainerStats,
    PortBinding,
)
from docksurf_py.core.render.markup import (
    rollup_dots,
    state_cell,
    state_glyph,
    truncate,
)
from docksurf_py.core.render.palette import Palette, fg
from docksurf_py.core.types import SafeMarkup

# Content widths of the fixed-width columns.
NAME_WIDTH = 27
GAUGE_WIDTH = 15

_DEFAULT_COMPOSE_FILES = frozenset(
    {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}
)

_UNITS: dict[str, tuple[str, int]] = {
    "second": ("s", 1),
    "minute": ("m", 60),
    "hour": ("h", 3600),
    "day": ("d", 86400),
    "week": ("w", 604800),
    "month": ("mo", 2592000),
    "year": ("y", 31536000),
}
_AGE_RE = re.compile(
    r"(?P<n>\d+|an?|about an?|less than a)\s+(?P<unit>second|minute|hour|day|week"
    r"|month|year)s?",
    re.IGNORECASE,
)
_WORD_COUNT = {"a": 1, "an": 1}
_NO_VALUE = "—"


def _dash(p: Palette) -> SafeMarkup:
    return fg(p, "faint", _NO_VALUE)


def parse_age(status: str) -> tuple[int, str] | None:
    """`(seconds, compact text)` from a summary `Status` such as
    `Up 3 hours (healthy)` or `Exited (137) 14 minutes ago`, or None when the
    text has no duration (`Created`)."""
    match = _AGE_RE.search(status)
    if match is None:
        return None
    raw = match["n"].lower()
    if raw.startswith("less than"):
        return 0, "<1s"
    count = int(raw) if raw.isdigit() else _WORD_COUNT.get(raw.split()[-1], 1)
    suffix, per = _UNITS[match["unit"].lower()]
    return count * per, f"{count}{suffix}"


def compact_age(status: str, uptime_hint: str = "") -> str:
    """`3h` for a running container, `14m ago` for a stopped one. The summary
    carries no finished-at timestamp, so the status text is the only source
    without an inspect. Falls back to `uptime_hint`, then `—`."""
    parsed = parse_age(status)
    if parsed is not None:
        return parsed[1] if status.startswith("Up") else f"{parsed[1]} ago"
    parsed = parse_age(uptime_hint)
    return parsed[1] if parsed is not None else _NO_VALUE


def age_seconds(c: Container) -> int:
    """Sort key for the UPTIME column: running longest-first sorts as largest,
    stopped containers (time since exit) sort below every running one."""
    parsed = parse_age(c.status) or parse_age(c.uptime_hint)
    seconds = parsed[0] if parsed else 0
    return seconds if c.running else -seconds - 1


def compact_ports(ports: list[PortBinding]) -> str:
    """First published binding as `host→container` (no `/tcp`, no wildcard
    address) plus `+N` for the rest, or `—` when nothing is published."""
    seen: list[str] = []
    for port in ports:
        if not port.host_port:
            continue
        number, _, proto = port.container_port.partition("/")
        target = number if proto in ("", "tcp") else port.container_port
        host = port.host_port
        if port.host_ip not in ("", "0.0.0.0", "::"):
            host = f"{port.host_ip}:{host}"
        label = f"{host}→{target}"
        if label not in seen:  # IPv4 and IPv6 bindings repeat the same port
            seen.append(label)
    if not seen:
        return _NO_VALUE
    return seen[0] + (f" +{len(seen) - 1}" if len(seen) > 1 else "")


def project_location(project: ComposeProject, home: str | None = None) -> str:
    """`~`-shortened working dir; the config file's basename is appended only
    when it isn't a default compose file name."""
    home = home if home is not None else str(Path.home())
    where = project.working_dir
    if where and home and (where == home or where.startswith(home + os.sep)):
        where = "~" + where[len(home) :]
    config = (
        os.path.basename(project.config_files.split(",")[0].strip())
        if project.config_files
        else ""
    )
    if config in _DEFAULT_COMPOSE_FILES:
        config = ""
    return " · ".join(part for part in (where, config) if part)


def gauge(percent: float, width: int, p: Palette, role: str, label: str) -> SafeMarkup:
    """`━━━━━━  12%`: filled cells in `role`, a faint track, `label` right-aligned
    within `width` cells. A non-zero value under one cell shows `╸`."""
    label = label[:width]
    bar_width = width - len(label) - 1
    if bar_width < 1:
        return SafeMarkup(escape(label.rjust(width)))
    ratio = max(0.0, min(percent, 100.0)) / 100
    filled = int(ratio * bar_width)
    tiny = filled == 0 and percent > 0
    bar = ""
    if filled:
        bar += fg(p, role, "━" * filled)
    if tiny:
        bar += fg(p, role, "╸")
    track = bar_width - filled - (1 if tiny else 0)
    if track:
        bar += fg(p, "faint", "━" * track)
    return SafeMarkup(f"{bar} {escape(label)}")


def _stats_cells(
    stats: ContainerStats | None, p: Palette
) -> tuple[SafeMarkup, SafeMarkup]:
    if stats is None:
        return _dash(p), _dash(p)
    cpu = gauge(
        stats.cpu_percent, GAUGE_WIDTH, p, "accent", f"{stats.cpu_percent:.0f}%"
    )
    mem = gauge(
        stats.mem_percent, GAUGE_WIDTH, p, "primary", format_size(stats.mem_used)
    )
    return cpu, mem


def container_row_cells(
    c: Container,
    *,
    branch: str | None,
    p: Palette,
    stats: ContainerStats | None = None,
) -> tuple[SafeMarkup, ...]:
    """The seven non-gutter cells: name, state, uptime, ports, cpu, mem, image.
    `branch` is the tree connector (`├─`/`└─`) for a Compose service row."""
    prefix = fg(p, "faint", branch) + " " if branch else ""
    label = truncate(
        c.compose_service or c.name if branch else c.name,
        NAME_WIDTH - 2 - (len(branch) + 1 if branch else 0),
    )
    name = SafeMarkup(f"{prefix}{state_glyph(c, p)} {escape(label)}")
    ports = compact_ports(c.ports)
    cpu, mem = _stats_cells(stats if c.running else None, p)
    return (
        name,
        state_cell(c, p),
        fg(p, "muted", compact_age(c.status, c.uptime_hint)),
        fg(p, "muted" if ports != _NO_VALUE else "faint", ports),
        cpu,
        mem,
        fg(p, "muted", c.image_name),
    )


def project_row_cells(project: ComposeProject, p: Palette) -> tuple[SafeMarkup, ...]:
    """Seven cells for a Compose header row. The collapse chevron is the
    gutter's job (`chevron`)."""
    blank = SafeMarkup("")
    return (
        SafeMarkup(f"[bold {p.strong}]{escape(truncate(project.name, NAME_WIDTH))}[/]"),
        rollup_dots(project.containers, p),
        blank,
        blank,
        blank,
        blank,
        fg(p, "muted", project_location(project)),
    )


def chevron(collapsed: bool, p: Palette) -> SafeMarkup:
    """Gutter glyph for a project header row."""
    return fg(p, "muted", "▸" if collapsed else "▾")
