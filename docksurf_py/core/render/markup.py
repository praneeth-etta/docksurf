"""Rich-markup cell and field formatters shared by the tables and detail pane."""

from docksurf_py.core.docker import format_relative_time
from docksurf_py.core.models import Container, HealthProbe
from docksurf_py.core.render.palette import Palette, fg
from docksurf_py.core.state.status import (
    GLYPH,
    ROLE,
    RunState,
    classify,
    project_state_counts,
)
from docksurf_py.core.types import SafeMarkup


def truncate(text: str, limit: int, middle: bool = False) -> str:
    """Shorten `text` to at most `limit` chars"""
    if len(text) <= limit:
        return text
    if not middle:
        return text[: limit - 1] + "…"
    head = limit // 2
    tail = limit - 1 - head
    return text[:head] + "…" + (text[-tail:] if tail else "")


def format_health_log(log: list[HealthProbe], limit: int = 5) -> str | None:
    """Render the most recent healthcheck probes as plain text, or None if empty.

    Plain text (not markup): the detail pane escapes it like the env-vars block.
    """
    if not log:
        return None
    blocks = []
    for probe in log[-limit:]:
        marker = "✓ pass" if probe.exit_code == 0 else f"✗ exit {probe.exit_code}"
        when = format_relative_time(probe.start) if probe.start else "—"
        output = probe.output.strip() or "(no output)"
        blocks.append(f"[{marker}]  {when}\n{output}")
    return "\n\n".join(blocks)


def state_glyph(c: Container, p: Palette) -> SafeMarkup:
    """The container's state glyph (`●` `◐` `✕` `○`) in its palette colour."""
    state = classify(c)
    return fg(p, ROLE[state], GLYPH[state])


def state_cell(c: Container, p: Palette) -> SafeMarkup:
    """STATE column text: `running ✓`, `starting …`, `paused`, `exited 137`."""
    state = classify(c)
    if state is RunState.RUNNING:
        label = "running ✓" if c.health == "healthy" else "running"
    elif state is RunState.STARTING:
        label = "restarting" if c.state == "restarting" else "starting …"
    elif state is RunState.PAUSED:
        label = "paused"
    elif state is RunState.UNHEALTHY:
        label = "unhealthy"
    elif c.state == "exited":
        label = f"exited {c.exit_code}"
    else:
        label = c.state or "unknown"  # created, dead
    return fg(p, ROLE[state], label)


def rollup_dots(
    containers: list[Container],
    p: Palette,
    max_dots: int = 10,
    with_count: bool = True,
) -> SafeMarkup:
    """One glyph per member (service-name order, `+N` past `max_dots`) followed
    by `2/7 up`: success = all up, warning = some, muted = none, with a
    trailing `N failed` in the error colour when any member failed.
    `with_count=False` drops the text, for narrow (compact) columns."""
    if not containers:
        return SafeMarkup("")
    members = sorted(containers, key=lambda c: (c.compose_service, c.name))
    dots = "".join(state_glyph(c, p) for c in members[:max_dots])
    extra = len(members) - max_dots
    if extra > 0:
        dots += fg(p, "muted", f"+{extra}")
    if not with_count:
        return SafeMarkup(dots)
    up, _down, failed = project_state_counts(members)
    role = "success" if up == len(members) else "warning" if up else "muted"
    out = f"{dots} {fg(p, role, f'{up}/{len(members)} up')}"
    if failed:
        out += " " + fg(p, "error", f"{failed} failed")
    return SafeMarkup(out)
