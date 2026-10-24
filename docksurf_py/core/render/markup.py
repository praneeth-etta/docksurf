"""Rich-markup cell and field formatters shared by the tables and detail pane."""

from rich.markup import escape

from docksurf_py.core.docker import format_relative_time
from docksurf_py.core.models import ComposeProject, Container, HealthProbe, Network
from docksurf_py.core.render.topology import _network_members
from docksurf_py.core.types import (
    STATUS_GREEN,
    STATUS_RED,
    STATUS_YELLOW,
    SafeMarkup,
    markup_green,
    markup_red,
    markup_yellow,
)


def status_color(c: Container) -> str:
    """The color a container's state maps to — shared by `status_markup`
    (Status column/field text) and `DetailPaneRenderer` (detail panel border),
    so the two never drift apart."""
    if c.running:
        return STATUS_GREEN
    if c.state in ("exited", "dead"):
        return STATUS_RED
    return STATUS_YELLOW  # paused, restarting, created


def status_markup(c: Container) -> SafeMarkup:
    if c.running:
        label = escape(c.status)
        if c.health == "healthy":
            label += " ✓"
        elif c.health == "unhealthy":
            label += " ✗"
        elif c.health == "starting":
            label += " …"
    elif c.state in ("exited", "dead"):
        suffix = f" ({c.exit_code})" if c.exit_code != 0 else ""
        label = escape(c.status) + suffix
    else:
        label = escape(c.status)  # paused, restarting, created
    return SafeMarkup(f"[{status_color(c)}]{label}[/]")


def status_dot(color: str) -> str:
    """Colored bullet prefixed to a Name cell — an at-a-glance state
    indicator so users don't need to scroll a wide table to see Status."""
    return f"[{color}]●[/]"


def truncate(text: str, limit: int, middle: bool = False) -> str:
    """Shorten `text` to at most `limit` chars"""
    if len(text) <= limit:
        return text
    if not middle:
        return text[: limit - 1] + "…"
    head = limit // 2
    tail = limit - 1 - head
    return text[:head] + "…" + (text[-tail:] if tail else "")


def health_markup(c: Container) -> SafeMarkup:
    """Colored health status for the Health column."""
    if c.health == "healthy":
        return markup_green("healthy")
    if c.health == "unhealthy":
        return markup_red("unhealthy")
    if c.health == "starting":
        return markup_yellow("starting")
    return SafeMarkup("[dim]—[/]")


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


def project_status_color(project: ComposeProject) -> str:
    return STATUS_GREEN if project.all_running else STATUS_YELLOW


def project_status_markup(project: ComposeProject) -> SafeMarkup:
    summary = f"{project.running_count}/{project.total_count} running"
    return SafeMarkup(f"[{project_status_color(project)}]{summary}[/]")


def image_status_markup(used_by: list[str]) -> SafeMarkup:
    return markup_green("In Use") if used_by else markup_yellow("Unused")


def network_containers_markup(
    network: Network, containers: list[Container]
) -> SafeMarkup:
    """`N attached (M running)` — the same endpoint↔container join the
    topology diagram and clipboard summary already use (`topology.py`), so
    the count here never drifts from what expanding the network shows."""
    members = _network_members(network, containers)
    if not members:
        return SafeMarkup("[dim]0[/]")
    running = sum(1 for m in members if m.running)
    color = STATUS_GREEN if running == len(members) else STATUS_YELLOW
    return SafeMarkup(f"[{color}]{len(members)} ({running} running)[/]")
