"""Network-topology helpers: member join, bus diagram, endpoint rows, summary.

Sits beside `renderer/` and `actions/` rather than inside either, because both
consume it: `renderer/detail_pane_renderer.py` draws the bus diagram and the
endpoints table into the side column, and `actions/clipboard.py` yanks the
plaintext summary — and all of it derives from the same endpoint↔container
join (`network_members`).

The diagram is attachment topology only, by design — Docker records which
containers sit on a network, not who talks to whom.
"""

from dataclasses import dataclass, field

from rich.text import Text

from docksurf_py.core.docker import format_ports
from docksurf_py.core.models import Container, Network
from docksurf_py.core.render.palette import Palette


@dataclass(frozen=True, slots=True)
class _Member:
    """One container's view of its attachment to a specific network.

    The join of a `NetworkEndpoint` (IP, only present for running containers —
    Docker's network inspect omits stopped ones) with the snapshot `Container`
    of the same name (run state, ports, Compose service, other networks).
    Either side may be missing: a stopped container has no endpoint, and an
    endpoint can name a container the snapshot doesn't know.
    """

    name: str
    ipv4: str = ""
    mac: str = ""
    running: bool = True  # an endpoint with no snapshot container is running
    ports: str = ""
    service: str = ""
    other_networks: list[str] = field(default_factory=list)


def network_members(network: Network, containers: list[Container]) -> list[_Member]:
    """Every container attached to `network`, running or not, sorted by name."""
    by_name = {c.name: c for c in containers}
    ips = {ep.container_name: ep.ipv4 for ep in network.endpoints}
    macs = {ep.container_name: ep.mac for ep in network.endpoints}
    names = set(ips) | {c.name for c in containers if network.name in c.networks}
    members = []
    for name in sorted(names):
        c = by_name.get(name)
        members.append(
            _Member(
                name=name,
                ipv4=ips.get(name, ""),
                mac=macs.get(name, ""),
                running=c.running if c else True,
                ports=format_ports(c.ports) if c else "",
                service=c.compose_service if c else "",
                other_networks=(
                    [n for n in c.networks if n != network.name] if c else []
                ),
            )
        )
    return members


def _network_summary(network: Network, containers: list[Container]) -> str:
    """Plaintext one-network digest for the clipboard (`Y` on a network row)."""
    header = "  ".join(
        p
        for p in (
            network.name,
            network.driver,
            network.subnet,
            f"gateway {network.gateway}" if network.gateway else "",
        )
        if p
    )
    members = network_members(network, containers)
    if not members:
        return f"{header}\n  (no containers attached)"
    name_w = max(len(m.name) for m in members)
    ip_w = max(len(m.ipv4) for m in members)
    lines = [header]
    for m in members:
        parts = [
            m.name.ljust(name_w),
            m.ipv4.ljust(ip_w) if ip_w else "",
            m.ports,
            "running" if m.running else "stopped",
        ]
        if m.service and m.service != m.name:
            parts.append(f"(svc: {m.service})")
        if m.other_networks:
            parts.append(f"also on: {', '.join(m.other_networks)}")
        lines.append("  " + "  ".join(p for p in parts if p))
    return "\n".join(lines)


# --- Bus diagram ---------------------------------------------------------------

_MIN_NAME = 6  # shortest a member name is squeezed to before going single-column


def _elide(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(width - 1, 0)] + "…"


def plain_ip(ip: str) -> str:
    """`172.18.0.2/16` -> `172.18.0.2`; `—` when the container has no address."""
    return ip.split("/")[0] if ip else "—"


def running_first(members: list[_Member]) -> list[_Member]:
    """Running members first, each group by name."""
    return sorted(members, key=lambda m: (not m.running, m.name))


def _dot(m: _Member, p: Palette) -> Text:
    return Text("●", style=p.success) if m.running else Text("○", style=p.muted)


def _hub_lines(network: Network, count: int) -> list[tuple[str, str]]:
    """The hub box's lines with the colour each is drawn in."""
    meta = " · ".join(x for x in (network.driver, network.subnet) if x and x != "N/A")
    tail = f"{count} attached"
    if network.gateway and network.gateway != "N/A":
        tail = f"gateway {network.gateway} · {tail}"
    lines = [(network.name, "strong")]
    if meta:
        lines.append((meta, "muted"))
    lines.append((tail, "muted"))
    return lines


def _hub_box(
    network: Network, count: int, p: Palette, width: int
) -> tuple[list[Text], int]:
    """The hub box and the column of its bottom border's `┬`."""
    lines = _hub_lines(network, count)
    inner = max(min(max(len(t) for t, _ in lines), width - 4), 1)
    edge = p.faint
    stem = (inner + 3) // 2
    rows = [Text(f"╭{'─' * (inner + 2)}╮", style=edge)]
    for text, role in lines:
        shown = _elide(text, inner)
        colour = p.strong if role == "strong" else p.muted
        row = Text("│ ", style=edge)
        row.append(
            shown.ljust(inner), style=f"bold {colour}" if role == "strong" else colour
        )
        row.append(" │", style=edge)
        rows.append(row)
    bottom = f"╰{'─' * (inner + 2)}╯"
    rows.append(Text(bottom[:stem] + "┬" + bottom[stem + 1 :], style=edge))
    return rows, stem


def _shift(text: Text, by: int) -> Text:
    return Text(" " * by).append_text(text) if by else text


def _member_cell(m: _Member, name: str, p: Palette) -> Text:
    return Text(name, style=p.strong if m.running else p.muted)


def _single_column(
    network: Network, ordered: list[_Member], p: Palette, width: int
) -> Text:
    hub, _ = _hub_box(network, len(ordered), p, width)
    stem = 1
    hub[-1] = Text(f"╰┬{'─' * (hub[0].cell_len - 3)}╯", style=p.faint)
    out = list(hub)
    out.append(Text(" " * stem + "│", style=p.faint))
    ip_w = max(len(plain_ip(m.ipv4)) for m in ordered)
    for i, m in enumerate(ordered):
        last = i == len(ordered) - 1
        row = Text(" " * stem + ("└── " if last else "├── "), style=p.faint)
        row.append(plain_ip(m.ipv4).ljust(ip_w), style=p.foreground)
        row.append("  ")
        row.append_text(_dot(m, p))
        row.append(" ")
        room = max(width - row.cell_len, 1)
        row.append_text(_member_cell(m, _elide(m.name, room), p))
        out.append(row)
    for row in out:
        row.truncate(width, overflow="ellipsis")
    return Text("\n").join(out)


def bus_topology(
    network: Network, members: list[_Member], p: Palette, width: int
) -> Text:
    """The network as a hub box on a vertical bus with its members paired left
    and right of it (running first); a single column when two don't fit
    `width`. An empty network is the hub plus `no containers attached`."""
    width = max(width, 12)
    ordered = running_first(members)
    if not ordered:
        hub, _ = _hub_box(network, 0, p, width)
        hub[-1] = Text(hub[-1].plain.replace("┬", "─"), style=p.faint)
        hub.append(Text(_elide("no containers attached", width), style=p.muted))
        return Text("\n").join(hub)
    longest = max(len(m.name) for m in ordered)
    for cap in range(longest, min(_MIN_NAME, longest) - 1, -1):
        built = _paired(network, ordered, p, width, cap)
        if built is not None:
            return built
    return _single_column(network, ordered, p, width)


def _paired(
    network: Network, ordered: list[_Member], p: Palette, width: int, cap: int
) -> Text | None:
    """The two-column bus with names squeezed to `cap`, or None if wider than
    `width`."""
    left, right = ordered[0::2], ordered[1::2]
    name_l = max(min(len(m.name), cap) for m in left)
    name_r = max((min(len(m.name), cap) for m in right), default=0)
    ip_l = max(len(plain_ip(m.ipv4)) for m in left)
    ip_r = max((len(plain_ip(m.ipv4)) for m in right), default=0)
    bus = 2 + name_l + 2 + ip_l + 3
    hub, stem = _hub_box(network, len(ordered), p, width)
    shift = max(stem - bus, 0)  # hub reaches further left than the bus
    bus += shift
    hub_left = bus - stem
    total = max(
        bus + 1 + (3 + ip_r + 4 + name_r if right else 0), hub_left + hub[0].cell_len
    )
    if total > width:
        return None
    out = [_shift(row, hub_left) for row in hub]
    spacer = Text(" " * bus + "│", style=p.faint)
    out.append(spacer)
    for i, a in enumerate(left):
        b = right[i] if i < len(right) else None
        row = Text(" " * shift)
        row.append_text(_dot(a, p))
        row.append(" ")
        row.append_text(_member_cell(a, _elide(a.name, cap).ljust(name_l), p))
        row.append("  " + plain_ip(a.ipv4).rjust(ip_l) + " ──", style=p.faint)
        row.stylize(p.foreground, row.cell_len - 3 - ip_l, row.cell_len - 3)
        if b is None:
            row.append("┘", style=p.faint)
        else:
            row.append("┼", style=p.faint)
            row.append("── ", style=p.faint)
            row.append(plain_ip(b.ipv4).ljust(ip_r), style=p.foreground)
            row.append("  ")
            row.append_text(_dot(b, p))
            row.append(" ")
            row.append_text(_member_cell(b, _elide(b.name, cap), p))
        out.append(row)
        if i < len(left) - 1:
            out.append(spacer)
    return Text("\n").join(out)
