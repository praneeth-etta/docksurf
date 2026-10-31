"""Volume table helpers: pure builders for the Volumes tab (Tide)."""

import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, NamedTuple

from rich.markup import escape

from docksurf_py.core.docker.format import format_size
from docksurf_py.core.models import Volume
from docksurf_py.core.render.groups import group_name_cell
from docksurf_py.core.render.markup import truncate
from docksurf_py.core.render.palette import Palette, fg, key_chip
from docksurf_py.core.types import SafeMarkup

if TYPE_CHECKING:
    # Only for annotations: `core.state.tables` imports `render.volumes`.
    from docksurf_py.core.state.tables import RowGroup

# Key of the `RowGroup` holding the volumes no container mounts.
ORPHANED_KEY = "orphaned"

ANONYMOUS_LABEL = "com.docker.volume.anonymous"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"

# Anonymous volume names are long hashes; the cell keeps both ends of one.
ANON_HEAD = 8
ANON_TAIL = 14
ANON_GLYPH = "◇"

_HASH_NAME = re.compile(r"[0-9a-f]{64}")


def is_anonymous(v: Volume) -> bool:
    """Docker-generated volume: it carries the anonymous label (whatever its
    value), or its name is a 64-character hex hash."""
    return ANONYMOUS_LABEL in v.labels or _HASH_NAME.fullmatch(v.name) is not None


def volume_project(v: Volume) -> str:
    """The Compose project that owns the volume, or `—`."""
    return v.labels.get(COMPOSE_PROJECT_LABEL) or "—"


def volume_name_cell(v: Volume, p: Palette, width: int) -> SafeMarkup:
    """The NAME cell, at most `width` cells wide.

    A named volume is bold strong text, middle-truncated to fit. An anonymous
    one is `◇` (secondary), the first 8 characters (muted), a faint `…` and
    the last 14 (muted); a short name is shown whole, and a narrow `width`
    shortens the tail first, then the head.
    """
    if not is_anonymous(v):
        name = v.name
        if len(name) > width:
            keep = max(width - 1, 0)
            head = (keep + 1) // 2
            tail = keep - head
            name = name[:head] + "…" + (name[len(name) - tail :] if tail else "")
            name = name[:width]
        return SafeMarkup(f"[bold {p.strong}]{escape(name)}[/]")

    glyph = fg(p, "secondary", ANON_GLYPH + " ")
    room = max(width - 2, 0)
    name = v.name
    if len(name) <= room:
        return SafeMarkup(glyph + fg(p, "muted", name))
    keep = max(room - 1, 0)
    head = min(ANON_HEAD, keep)
    tail = min(ANON_TAIL, keep - head)
    return SafeMarkup(
        glyph
        + fg(p, "muted", name[:head])
        + fg(p, "faint", "…")
        + fg(p, "muted", name[len(name) - tail :] if tail else "")
    )


def volume_order_key(v: Volume, sizes: Mapping[str, int]) -> tuple:
    """Order inside a group: named volumes first (alphabetical), then anonymous
    ones by size descending, those with a size not yet computed last."""
    if not is_anonymous(v):
        return (0, 0, 0, v.name.lower())
    size = sizes.get(v.name)
    return (1, size is None, -(size or 0), v.name)


# --- Table cells -------------------------------------------------------------

# Content widths of the Volumes table columns (the NAME column can't be `1fr`
# in a DataTable, so it gets a fixed width like the image REPOSITORY).
VOLUME_NAME_WIDTH = 30
PROJECT_WIDTH = 16
DRIVER_WIDTH = 8
VOLUME_SIZE_WIDTH = 12
USED_BY_WIDTH = 26

_PENDING = "· · ·"


def size_cell(size: int | None, p: Palette, role: str = "foreground") -> SafeMarkup:
    """A right-aligned size, or a muted `· · ·` while it isn't computed."""
    if size is None:
        return fg(p, "muted", _PENDING.rjust(VOLUME_SIZE_WIDTH))
    return fg(p, role, format_size(size).rjust(VOLUME_SIZE_WIDTH))


def used_by_cell(v: Volume, p: Palette, width: int = USED_BY_WIDTH) -> SafeMarkup:
    """The first container's name and `+N` for the rest, or a muted `—`."""
    if not v.used_by:
        return fg(p, "muted", "—")
    extra = f" +{len(v.used_by) - 1}" if len(v.used_by) > 1 else ""
    first = fg(p, "foreground", truncate(v.used_by[0], width - len(extra)))
    return SafeMarkup(first + fg(p, "muted", extra) if extra else first)


def volume_row_cells(v: Volume, size: int | None, p: Palette) -> tuple[SafeMarkup, ...]:
    """The five non-gutter cells: name, project, driver, size, used by."""
    project = volume_project(v)
    return (
        volume_name_cell(v, p, VOLUME_NAME_WIDTH),
        fg(p, "muted" if project == "—" else "foreground", truncate(project, 16)),
        fg(p, "muted", truncate(v.driver, DRIVER_WIDTH)),
        size_cell(size, p),
        used_by_cell(v, p),
    )


def known_sizes(volumes: Iterable[Volume], sizes: Mapping[str, int]) -> list[int]:
    """The computed sizes of `volumes` (those still pending are left out)."""
    return [sizes[v.name] for v in volumes if v.name in sizes]


def volume_group_cells(
    group: "RowGroup", sizes: Mapping[str, int], p: Palette
) -> tuple[SafeMarkup, ...]:
    """The five non-gutter cells of a group header: `● IN USE · 8`, the sum of
    its known sizes (in warning colour for a warning group) and, on the
    orphaned group, the `P prune orphans` chip."""
    blank = SafeMarkup("")
    known = known_sizes(group.items, sizes)
    role = "warning" if group.role == "warning" else "muted"
    action = (
        SafeMarkup(key_chip(p, "P") + fg(p, "muted", " prune orphans"))
        if group.key == ORPHANED_KEY
        else blank
    )
    return (
        group_name_cell(group, p, VOLUME_NAME_WIDTH),
        blank,
        blank,
        size_cell(sum(known) if known else None, p, role),
        action,
    )


# --- Panel chrome --------------------------------------------------------------


def volumes_footer(p: Palette) -> SafeMarkup:
    """The legend under the table."""
    return SafeMarkup(
        fg(p, "secondary", ANON_GLYPH)
        + fg(p, "muted", " anonymous volume (hash name)   ")
        + f"[bold {p.strong}]bold[/]"
        + fg(p, "muted", " named volume   ")
        + fg(p, "muted", f"{_PENDING} size not computed yet")
    )


def volumes_toolbar_line(known: int, total: int, width: int, p: Palette) -> SafeMarkup:
    """`named first · anonymous after` on the left and `sizes 14/29  b size all`
    right-aligned; the left note goes first when `width` is short."""
    left = "named first · anonymous after"
    right = f"sizes {known}/{total}  b size all"
    right_markup = SafeMarkup(
        fg(p, "muted", "sizes ")
        + fg(p, "foreground", f"{known}/{total}")
        + "  "
        + fg(p, "strong", "b")
        + fg(p, "muted", " size all")
    )
    gap = width - len(left) - len(right)
    if gap < 2:
        return SafeMarkup(" " * max(width - len(right), 0) + right_markup)
    return SafeMarkup(fg(p, "muted", left) + " " * gap + right_markup)


# --- Safe-to-delete card -------------------------------------------------------


class OrphanSummary(NamedTuple):
    """The orphaned volumes as a whole: how many, and the disk they hold so far
    as it is known (`all_known` is False while some sizes are still pending)."""

    total: int
    known_bytes: int
    all_known: bool


def orphan_summary(
    volumes: Iterable[Volume], sizes: Mapping[str, int]
) -> OrphanSummary:
    orphans = [v for v in volumes if not v.used_by]
    known = known_sizes(orphans, sizes)
    return OrphanSummary(len(orphans), sum(known), len(known) == len(orphans))


def orphan_total_text(orphans: OrphanSummary) -> str:
    """The disk the orphans hold: `4.6GB`, `≥ 4.6GB` while some sizes are
    still pending, or `` when none is known yet."""
    if not (orphans.all_known or orphans.known_bytes):
        return ""
    approx = "" if orphans.all_known else "≥ "
    return f"{approx}{format_size(orphans.known_bytes)}"


def safe_delete_body(
    v: Volume, size: int | None, orphans: OrphanSummary, p: Palette
) -> SafeMarkup:
    """The text of the `safe to delete` card for an orphaned volume `v`."""
    data = f"its {format_size(size)} of data" if size is not None else "its data"
    total = f" · {orphan_total_text(orphans)}" if orphan_total_text(orphans) else ""
    actions = (
        fg(p, "strong", "d")
        + fg(p, "muted", " delete this   ")
        + fg(p, "strong", "P")
        + fg(p, "muted", f" prune all {orphans.total} orphans{total}")
    )
    return SafeMarkup(
        fg(p, "foreground", "No container mounts this volume, running or stopped.")
        + "\n"
        + fg(p, "foreground", f"Deleting removes {data} for good.")
        + "\n"
        + actions
    )
