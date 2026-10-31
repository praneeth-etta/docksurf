"""Header rows of a `RowGroup` (Volumes, Networks): pure cell builders."""

from typing import TYPE_CHECKING

from docksurf_py.core.render.markup import truncate
from docksurf_py.core.render.palette import Palette, fg
from docksurf_py.core.types import SafeMarkup

if TYPE_CHECKING:
    # Only for annotations: `core.state.tables` imports `render.volumes`.
    from docksurf_py.core.state.tables import RowGroup


def group_name_cell(group: "RowGroup", p: Palette, width: int) -> SafeMarkup:
    """`● IN USE · 8`, bold in the group's role colour, at most `width` cells.
    The note (`not mounted by any container`) follows in muted text only when
    all of it fits; it is never cut short."""
    head = f"{group.glyph} " if group.glyph else ""
    head += f"{group.label} · {len(group.items)}"
    title = SafeMarkup(f"[bold]{fg(p, group.role, truncate(head, width))}[/]")
    if group.note and len(head) + 3 + len(group.note) <= width:
        return SafeMarkup(f"{title}{fg(p, 'muted', f' · {group.note}')}")
    return title
