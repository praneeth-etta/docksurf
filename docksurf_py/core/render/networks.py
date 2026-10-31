"""Networks tab: group-header cells, row cells, and the side panels' content."""

from typing import TYPE_CHECKING

from rich.console import RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from docksurf_py.core.models import Container, Network
from docksurf_py.core.render.groups import group_name_cell
from docksurf_py.core.render.markup import (
    project_state_counts,
    state_glyph,
    truncate,
)
from docksurf_py.core.render.palette import Palette, fg
from docksurf_py.core.render.topology import (
    _Member,
    bus_topology,
    network_members,
    plain_ip,
    running_first,
)
from docksurf_py.core.state.status import GLYPH, ROLE, RunState
from docksurf_py.core.types import SafeMarkup

if TYPE_CHECKING:
    # Only for annotations: `core.state.tables` imports this package's modules.
    from docksurf_py.core.state.tables import RowGroup

# NAME is a fixed width: a DataTable can't size a column as `1fr`.
NETWORK_NAME_WIDTH = 30
NETWORK_DRIVER_WIDTH = 9
SCOPE_WIDTH = 7
SUBNET_WIDTH = 17
CONTAINERS_WIDTH = 18
# Most dots drawn in the CONTAINERS cell; the rest collapse into `+N`.
MAX_DOTS = 8


def network_containers_cell(
    network: Network, containers: list[Container], p: Palette
) -> SafeMarkup:
    """CONTAINERS column: one dot per attached container (running first, then
    by name; `+N` past `MAX_DOTS`) and `2/7` running of attached, or a muted
    `empty`. Same endpoint↔container join as the topology and clipboard
    summary, so the count never drifts from them. An endpoint the snapshot
    doesn't know is shown as running, like `network_members` treats it."""
    members = network_members(network, containers)
    if not members:
        return SafeMarkup(fg(p, "muted", "empty"))
    by_name = {c.name: c for c in containers}
    ordered = sorted(members, key=lambda m: (not m.running, m.name))
    dots = ""
    for m in ordered[:MAX_DOTS]:
        c = by_name.get(m.name)
        dots += state_glyph(c, p) if c else fg(p, "success", "●")
    if len(ordered) > MAX_DOTS:
        dots += fg(p, "muted", f"+{len(ordered) - MAX_DOTS}")
    known = [by_name[m.name] for m in members if m.name in by_name]
    up, _down, _failed = project_state_counts(known)
    up += len(members) - len(known)
    role = "success" if up == len(members) else "warning" if up else "muted"
    return SafeMarkup(f"{dots} {fg(p, role, f'{up}/{len(members)}')}")


def network_group_cells(group: "RowGroup", p: Palette) -> tuple[SafeMarkup, ...]:
    """The five non-gutter cells of a group header: only the name cell is
    filled (`▾ COMPOSE PROJECTS · 5`, `BUILT-IN · 3 · can't be deleted` when
    the note fits)."""
    blank = SafeMarkup("")
    return (group_name_cell(group, p, NETWORK_NAME_WIDTH), blank, blank, blank, blank)


def network_subnet(n: Network) -> str:
    """The subnet, or `—` when the network has none (`N/A` from the fetcher)."""
    return n.subnet if n.subnet and n.subnet != "N/A" else "—"


def network_row_cells(
    n: Network, containers: list[Container], p: Palette
) -> tuple[SafeMarkup, ...]:
    """The five non-gutter cells: name, driver, scope, subnet, containers."""
    subnet = network_subnet(n)
    return (
        SafeMarkup(
            f"[bold {p.strong}]{escape(truncate(n.name, NETWORK_NAME_WIDTH))}[/]"
        ),
        fg(p, "muted", truncate(n.driver, NETWORK_DRIVER_WIDTH)),
        fg(p, "muted", truncate(n.scope, SCOPE_WIDTH)),
        fg(
            p,
            "muted" if subnet == "—" else "foreground",
            truncate(subnet, SUBNET_WIDTH),
        ),
        network_containers_cell(n, containers, p),
    )


def networks_footer(p: Palette) -> SafeMarkup:
    """The legend under the table."""
    return SafeMarkup(
        fg(p, ROLE[RunState.RUNNING], GLYPH[RunState.RUNNING])
        + fg(p, "muted", " running  ")
        + fg(p, ROLE[RunState.STOPPED], GLYPH[RunState.STOPPED])
        + fg(p, "muted", " stopped  ")
        + fg(p, ROLE[RunState.FAILED], GLYPH[RunState.FAILED])
        + fg(p, "muted", " exited with error   one dot per attached container")
    )


# --- Side panels ---------------------------------------------------------------

# Narrower than these and the ALSO ON / MAC columns are dropped from the
# endpoints table (MAC goes first: which other networks a container bridges is
# the more useful of the two).
_ALSO_ON_MIN_WIDTH = 44
_MAC_MIN_WIDTH = 64
_ALSO_ON_WIDTH = 16


def topology_title(count: int) -> str:
    return f"topology · {count} endpoint{'' if count == 1 else 's'}"


def topology_body(
    network: Network, containers: list[Container], p: Palette, width: int
) -> Text:
    """The bus diagram at `width`."""
    return bus_topology(network, network_members(network, containers), p, width)


def endpoint_window(
    members: list[_Member], height: int
) -> tuple[list[_Member], int, str]:
    """Which endpoints fit in `height` rows (one is the header): all of them,
    else the running ones with the stopped ones folded into a `+ N stopped`
    line, else as many as fit with `+ N more`. Returns `(shown, hidden, kind)`
    where `kind` is `stopped`, `more` or `` (nothing hidden)."""
    ordered = running_first(members)
    room = max(height - 1, 1)
    if len(ordered) <= room:
        return ordered, 0, ""
    running = [m for m in ordered if m.running]
    if len(running) < len(ordered) and len(running) <= room - 1:
        return running, len(ordered) - len(running), "stopped"
    shown = ordered[: room - 1]
    rest = ordered[room - 1 :]
    return shown, len(rest), "stopped" if not any(m.running for m in rest) else "more"


def endpoints_body(
    members: list[_Member], p: Palette, width: int, height: int
) -> RenderableType:
    """The endpoints table: state glyph, CONTAINER, IPV4, ALSO ON (the other
    networks the container is on), MAC (running first; MAC then ALSO ON are
    dropped as `width` gets short)."""
    if not members:
        return Text("no containers attached", style=p.muted)
    shown, hidden, kind = endpoint_window(members, height)
    with_also = width >= _ALSO_ON_MIN_WIDTH
    with_mac = width >= _MAC_MIN_WIDTH
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(width=1)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(width=15, no_wrap=True)
    if with_also:
        grid.add_column(width=_ALSO_ON_WIDTH, no_wrap=True, overflow="ellipsis")
    if with_mac:
        grid.add_column(width=17, no_wrap=True)
    head = [Text(""), Text("CONTAINER", style=p.muted), Text("IPV4", style=p.muted)]
    if with_also:
        head.append(Text("ALSO ON", style=p.muted))
    if with_mac:
        head.append(Text("MAC", style=p.muted))
    grid.add_row(*head)
    for m in shown:
        dot = Text("●", style=p.success) if m.running else Text("○", style=p.muted)
        ip = plain_ip(m.ipv4)
        cells = [
            dot,
            Text(m.name, style=p.strong if m.running else p.muted),
            Text(ip, style=p.muted if ip == "—" else p.foreground),
        ]
        if with_also:
            also = ", ".join(m.other_networks)
            cells.append(
                Text(f"⇄ {also}", style=p.secondary)
                if also
                else Text("—", style=p.muted)
            )
        if with_mac:
            cells.append(Text(m.mac or "—", style=p.muted))
        grid.add_row(*cells)
    if hidden:
        note = Text(f"+ {hidden} {kind}", style=p.muted)
        extra = 1 + with_also + with_mac
        grid.add_row(*[Text(""), note, *[Text("")] * extra])
    return grid
