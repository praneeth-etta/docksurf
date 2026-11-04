"""The `?` help screen's content: which scope each binding belongs to, how the
rows group, and the markup. Pure — the tui reflects `BINDINGS` into rows."""

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from rich.markup import escape

from docksurf_py.core.render.palette import Palette, fg, key_chip
from docksurf_py.core.types import SafeMarkup

GLOBAL = "Global"
CONTAINERS = "Containers tab"
COMPOSE = "Compose project"
LOGS = "Containers tab (logs panel)"
IMAGES = "Images tab"
VOLUMES = "Volumes tab"
NETWORKS = "Networks tab"
JUMP = "Images, Volumes, Networks tabs"

# Display order of the sections.
SCOPE_ORDER: tuple[str, ...] = (
    GLOBAL,
    CONTAINERS,
    COMPOSE,
    LOGS,
    IMAGES,
    VOLUMES,
    NETWORKS,
    JUMP,
)

# Keys that drive the logs panel. They are implemented by the container action
# handler, so reflection can't tell them from the rest of its actions; a test
# checks every name here still exists there.
LOG_PANEL_ACTIONS: frozenset[str] = frozenset(
    {
        "follow_logs",
        "clear_logs",
        "log_options",
        "toggle_timestamps",
        "toggle_log_wrap",
        "next_match",
        "prev_match",
        "log_top",
        "log_bottom",
        "export_logs",
    }
)


class HelpRow(NamedTuple):
    key: str  # a Textual key name (`plus`, `ctrl+u`) or an already-friendly label
    description: str
    scope: str


# Real shortcuts that never go through BINDINGS (the table and the log pane
# handle them directly), so they can't be reflected.
EXTRA_ROWS: tuple[HelpRow, ...] = (
    HelpRow("Tab", "Switch between panels", GLOBAL),
    HelpRow("↑ / ↓", "Move between rows", GLOBAL),
    HelpRow("enter", "Jump to the related container", JUMP),
    HelpRow("/", "Filter logs (Esc clears)", LOGS),
)

_KEY_NAMES = {
    "plus": "+",
    "minus": "-",
    "question_mark": "?",
    "slash": "/",
    "escape": "esc",
    "enter": "⏎",
    "space": "space",
    "tilde": "~",
}


def display_key(key: str) -> str:
    """`plus` -> `+`, `ctrl+u` -> `^u`, `escape` -> `esc`; anything else as is."""
    if key in _KEY_NAMES:
        return _KEY_NAMES[key]
    if key.startswith("ctrl+"):
        return "^" + key.removeprefix("ctrl+")
    return key


def scope_for(
    action: str,
    container_actions: frozenset[str],
    project_actions: frozenset[str],
    tab_actions: Mapping[str, frozenset[str]],
) -> str:
    """The section a bound action belongs in: Compose project, logs panel,
    Containers tab, one of the per-tab handlers, or Global."""
    if action in project_actions:
        return COMPOSE
    if action in LOG_PANEL_ACTIONS and action in container_actions:
        return LOGS
    if action in container_actions:
        return CONTAINERS
    for label, actions in tab_actions.items():
        if action in actions:
            return label
    return GLOBAL


def group_rows(rows: Iterable[HelpRow]) -> list[tuple[str, list[HelpRow]]]:
    """Rows grouped by scope in `SCOPE_ORDER` (unknown scopes last, in the
    order first seen), keeping row order inside a section and dropping exact
    duplicates."""
    by_scope: dict[str, list[HelpRow]] = {}
    for row in rows:
        section = by_scope.setdefault(row.scope, [])
        if row not in section:
            section.append(row)
    known = [s for s in SCOPE_ORDER if s in by_scope]
    extra = [s for s in by_scope if s not in SCOPE_ORDER]
    return [(scope, by_scope[scope]) for scope in known + extra]


def help_markup(
    sections: Sequence[tuple[str, Sequence[HelpRow]]], p: Palette
) -> SafeMarkup:
    """Each section as an accent heading over `key chip  description` lines, the
    chips padded to one width so the descriptions line up."""
    width = max(
        (len(display_key(r.key)) for _, rows in sections for r in rows), default=0
    )
    blocks: list[str] = []
    for scope, rows in sections:
        lines = [f"[bold {p.accent}]{escape(scope)}[/]"]
        for row in rows:
            chip = key_chip(p, display_key(row.key).ljust(width))
            lines.append(f"{chip}  {fg(p, 'foreground', row.description)}")
        blocks.append("\n".join(lines))
    return SafeMarkup("\n\n".join(blocks))
