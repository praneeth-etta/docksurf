"""App chrome as Rich markup: the top bar, the bottom key-hint/counts bar and
list panel titles. Pure builders; the tui widgets only paint what they return."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

from rich.markup import escape
from rich.text import Text

from docksurf_py.core.models import DockerSnapshot
from docksurf_py.core.render.palette import Palette, blend, fg, key_chip, pill
from docksurf_py.core.state.status import partition_counts
from docksurf_py.core.state.tables import ALL_CHIP, Chip
from docksurf_py.core.types import SafeMarkup, TabID

TAB_LABELS: dict[TabID, str] = {
    TabID.CONTAINERS: "Containers",
    TabID.IMAGES: "Images",
    TabID.VOLUMES: "Volumes",
    TabID.NETWORKS: "Networks",
}

TAB_NUMBERS: dict[TabID, int] = {tab: i for i, tab in enumerate(TAB_LABELS, start=1)}

Connection = Literal["live", "offline", "connecting"]

_BAR_GAP = 2


def _cells(markup: str) -> int:
    return Text.from_markup(markup).cell_len


@dataclass(frozen=True, slots=True)
class TabSummary:
    tab: TabID
    number: int
    label: str
    count: int | None  # None before the first snapshot


def tab_summaries(snapshot: DockerSnapshot | None) -> list[TabSummary]:
    counts: dict[TabID, int | None] = {tab: None for tab in TabID}
    if snapshot is not None:
        counts = {
            TabID.CONTAINERS: len(snapshot.containers),
            TabID.IMAGES: len(snapshot.images),
            TabID.VOLUMES: len(snapshot.volumes),
            TabID.NETWORKS: len(snapshot.networks),
        }
    return [
        TabSummary(tab, TAB_NUMBERS[tab], TAB_LABELS[tab], counts[tab]) for tab in TabID
    ]


# ---------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------


def _tab_text(t: TabSummary, with_label: bool) -> str:
    count = "…" if t.count is None else str(t.count)
    parts = [str(t.number)]
    if with_label:
        parts += [t.label, count]
    return " ".join(parts)


def _tabs_markup(
    tabs: Sequence[TabSummary], active: TabID, p: Palette, compact: bool
) -> str:
    left = [pill(p, "◆ docksurf", "accent")]
    for t in tabs:
        is_active = t.tab == active
        text = _tab_text(t, with_label=is_active or not compact)
        label = pill(p, text, "primary") if is_active else fg(p, "muted", f" {text} ")
        left.append(SafeMarkup(f"[@click=app.switch_tab_{t.number}]{label}[/]"))
    return " ".join(left)


def top_bar(
    tabs: Sequence[TabSummary],
    active: TabID,
    context: str,
    connection: Connection,
    theme_label: str,
    p: Palette,
    compact: bool = False,
    width: int = 0,
) -> SafeMarkup:
    """`◆ docksurf  [1 Containers 15] 2 Images 58 …   context  ● live  M tide`.

    Tabs carry `@click` actions so they switch on click. In `compact` mode only
    the active tab keeps its label. With a `width`, the right side is pushed
    to the edge; if it can't fit, it is trimmed piece by piece (theme, then
    context), and only then are the inactive tab labels collapsed to numbers —
    the connection state is never dropped while there is any room for it.
    """
    conn: str = {
        "live": fg(p, "success", "● live"),
        "offline": fg(p, "error", "○ offline"),
        "connecting": fg(p, "warning", "◐ connecting"),
    }[connection]
    context_chip = f"[{p.strong} on {p.panel}] {escape(context)} [/]"
    theme_part = f"{key_chip(p, 'M')} {fg(p, 'muted', theme_label)}"
    candidates: list[list[str]] = [
        [fg(p, "muted", "context"), context_chip, conn, theme_part],
        [fg(p, "muted", "context"), context_chip, conn],
        [conn],
    ]
    if not context:
        candidates = [[conn, theme_part], [conn]]

    if width <= 0:
        left = _tabs_markup(tabs, active, p, compact)
        return SafeMarkup(f"{left}   {'  '.join(candidates[0])}")
    for tabs_compact in (False, True) if not compact else (True,):
        left = _tabs_markup(tabs, active, p, tabs_compact)
        used = _cells(left)
        for right_parts in candidates:
            right = "  ".join(right_parts)
            if used + _BAR_GAP + _cells(right) <= width:
                pad = width - used - _cells(right)
                return SafeMarkup(f"{left}{' ' * pad}{right}")
    return SafeMarkup(_tabs_markup(tabs, active, p, True))


# ---------------------------------------------------------------------------
# Bottom bar
# ---------------------------------------------------------------------------

RowKind = Literal[
    "none",
    "container_running",
    "container_stopped",
    "project",
    "image",
    "volume",
    "volume_group",
    "network",
    "network_group",
]


@dataclass(frozen=True, slots=True)
class HintContext:
    tab: TabID
    row: RowKind
    logs_full: bool = False
    has_marks: bool = False
    compose: bool = False  # the focused container belongs to a Compose project
    filtering: bool = False  # the filter input has focus and takes the keys
    # The focused image/volume/network has a container to jump to (`enter`).
    related: bool = False


Hint = tuple[str, str]

# Shown after the context hints, dropped first when the line is too narrow.
GLOBAL_HINTS: tuple[Hint, ...] = (("/", "filter"), ("?", "all keys"), ("^p", "palette"))

_MARK: Hint = ("space", "mark")
_DELETE: Hint = ("d", "delete")
_JUMP: Hint = ("⏎", "jump")


def key_hints(ctx: HintContext) -> list[Hint]:
    """Ordered `(key, label)` hints for the focused row. Only names keys that
    are actually bound (a test checks each against `DockSurfApp.BINDINGS`)."""
    if ctx.filtering:
        return [("enter", "done"), ("esc", "back to list")]
    if ctx.logs_full:
        return [
            ("f", "follow"),
            ("n", "next match"),
            ("g", "top"),
            ("G", "bottom"),
            ("T", "timestamps"),
            ("W", "wrap"),
            ("l", "close logs"),
        ]
    hints: list[Hint] = []
    if ctx.has_marks:
        hints.append(("esc", "clear marks"))
    if ctx.tab == TabID.CONTAINERS:
        if ctx.row == "container_running":
            hints += [("s", "stop"), ("x", "restart"), ("l", "logs"), ("e", "shell")]
            if ctx.compose:
                hints.append(("B", "rebuild"))
            hints += [_MARK, _DELETE]
        elif ctx.row == "container_stopped":
            hints += [("S", "start"), ("l", "logs"), _MARK, _DELETE]
        elif ctx.row == "project":
            hints += [
                ("s", "stop"),
                ("S", "start"),
                ("x", "restart"),
                ("^u", "up"),
                ("^k", "down"),
                ("l", "logs"),
                ("space", "collapse"),
            ]
    elif ctx.tab == TabID.IMAGES:
        if ctx.row == "image":
            if ctx.related:
                hints.append(_JUMP)
            hints += [
                ("h", "history"),
                ("y", "tag"),
                ("+", "pull"),
                ("a", "mark dangling"),
                _DELETE,
                ("P", "prune"),
            ]
        else:
            hints += [("+", "pull"), ("P", "prune")]
    elif ctx.tab == TabID.VOLUMES:
        if ctx.row == "volume" and ctx.related:
            hints.append(_JUMP)
        hints += [("b", "size"), ("+", "new")]
        if ctx.row == "volume":
            hints += [_MARK, _DELETE]
        elif ctx.row == "volume_group":
            hints.append(("space", "collapse"))
        hints.append(("P", "prune"))
    elif ctx.tab == TabID.NETWORKS:
        if ctx.row == "network" and ctx.related:
            hints.append(_JUMP)
        if ctx.row == "network":
            hints += [("v", "connect"), ("m", "disconnect")]
        hints.append(("+", "new"))
        if ctx.row == "network":
            hints += [_MARK, _DELETE]
        elif ctx.row == "network_group":
            hints.append(("space", "collapse"))
        hints.append(("P", "prune"))
    return hints


class GlobalCounts(NamedTuple):
    up: int
    down: int
    failed: int
    images: int
    orphaned_volumes: int


def global_counts(snapshot: DockerSnapshot | None) -> GlobalCounts | None:
    if snapshot is None:
        return None
    up, down, failed = partition_counts(snapshot.containers)
    orphaned = sum(1 for v in snapshot.volumes if not v.used_by)
    return GlobalCounts(up, down, failed, len(snapshot.images), orphaned)


def _hint_markup(h: Hint, p: Palette) -> str:
    return f"{key_chip(p, h[0])} {fg(p, 'muted', h[1])}"


def _counts_markup(c: GlobalCounts, p: Palette) -> str:
    def part(role: str, text: str, n: int) -> str:
        return fg(p, role if n else "muted", text)

    sep = fg(p, "faint", "│")
    return (
        f"{part('success', f'● {c.up} up', c.up)}  "
        f"{part('muted', f'○ {c.down} down', c.down)}  "
        f"{part('error', f'✕ {c.failed} failed', c.failed)} {sep} "
        f"{fg(p, 'muted', f'{c.images} images')} "
        f"{fg(p, 'faint', '·')} "
        f"{part('warning', f'{c.orphaned_volumes} orphaned volumes', c.orphaned_volumes)}"  # noqa: E501
    )


def bottom_bar(
    hints: Sequence[Hint],
    counts: GlobalCounts | None,
    p: Palette,
    width: int = 0,
    message: str = "",
    global_hints: Sequence[Hint] = GLOBAL_HINTS,
) -> SafeMarkup:
    """Key hints on the left, global counts (or a disconnect `message`) on the
    right. With a `width`, hints are dropped from the end (global ones first)
    until the line fits; the right side is always kept.
    Drop order: the palette hint, then context hints from the end, then the
    remaining global hints."""
    right: str
    if message:
        right = fg(p, "error", f"● {message}")
    elif counts is not None:
        right = _counts_markup(counts, p)
    else:
        right = ""
    ctx_h, glob_h = list(hints), list(global_hints)

    def build() -> str:
        left = "  ".join(_hint_markup(h, p) for h in ctx_h)
        if glob_h:
            glob = "  ".join(_hint_markup(h, p) for h in glob_h)
            left = f"{left} {fg(p, 'faint', '│')} {glob}" if left else glob
        return left

    left = build()
    if width > 0:
        while (ctx_h or glob_h) and _cells(left) + _BAR_GAP + _cells(right) > width:
            # Palette hint first, then the context hints from the end, then the
            # rest — `/ filter` and `? all keys` are what a newcomer needs most.
            if len(glob_h) > 2:
                glob_h.pop()
            elif ctx_h:
                ctx_h.pop()
            else:
                glob_h.pop(0)
            left = build()
        pad = max(_BAR_GAP, width - _cells(left) - _cells(right))
    else:
        pad = _BAR_GAP
    return SafeMarkup(f"{left}{' ' * pad}{right}")


# ---------------------------------------------------------------------------
# List panel title
# ---------------------------------------------------------------------------

_PLURALS: dict[TabID, str] = {tab: label.lower() for tab, label in TAB_LABELS.items()}


def list_title(
    tab: TabID,
    count: int,
    sort_state: tuple[str, bool] | None,
    grouped: bool | str,
    total: int | None = None,
) -> str:
    """`containers · 15 · grouped by project`, `images · 58 · sorted by size ▾`.
    `grouped` is True for Compose projects or what the rows are grouped by
    (`usage`). `total` is the unfiltered count; when it differs, shows
    `3 of 15`."""
    shown = f"{count} of {total}" if total is not None and total != count else count
    parts = [_PLURALS[tab], str(shown)]
    if grouped:
        parts.append(f"grouped by {'project' if grouped is True else grouped}")
    if sort_state is not None:
        column, reverse = sort_state
        parts.append(f"sorted by {column.lower()} {'▾' if reverse else '▴'}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Filter chips
# ---------------------------------------------------------------------------


def chip_bar(
    chips: Sequence[Chip],
    counts: Mapping[str, int],
    active: str,
    p: Palette,
) -> SafeMarkup:
    """`all 7  running 5  stopped 1  failed 1`, each chip clickable
    (`app.set_chip`). The active chip is a pill on primary; an inactive chip
    in an alarm role with a non-zero count gets a tinted background. Empty
    for a tab without chips."""
    if not chips:
        return SafeMarkup("")
    parts: list[str] = []
    for key, label, role in [(ALL_CHIP, "all", "muted")] + [
        (c.key, c.label, c.role) for c in chips
    ]:
        count = counts.get(key, 0)
        text = f"{label} {count}"
        if key == active:
            body = pill(p, text, "primary")
        elif role != "muted" and count:
            tint = blend(getattr(p, role), p.background, 0.2)
            body = SafeMarkup(f"[{getattr(p, role)} on {tint}] {escape(text)} [/]")
        else:
            body = fg(p, "muted", f" {text} ")
        parts.append(f"[@click=app.set_chip('{key}')]{body}[/]")
    return SafeMarkup(" ".join(parts))
