"""Detail cards for the side panel: pure builders that turn a resource into a
`CardModel` (title, subtitle, body renderable, `▸` sections). The tui's
`DetailPane` only paints what they return."""

from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from docksurf_py.core.docker import (
    format_env,
    format_relative_time,
    format_size,
    format_uptime,
    secret_env_count,
)
from docksurf_py.core.models import (
    ComposeProject,
    Container,
    ContainerDetail,
    ContainerTop,
    Image,
    ImagePlatform,
    Volume,
)
from docksurf_py.core.render.containers import compact_age
from docksurf_py.core.render.images import image_status, status_glyph
from docksurf_py.core.render.markup import format_health_log, state_cell, state_glyph
from docksurf_py.core.render.observability import render_top
from docksurf_py.core.render.palette import Palette, fg, key_chip, pill
from docksurf_py.core.render.volumes import ANON_GLYPH, is_anonymous
from docksurf_py.core.state.status import ROLE, classify, project_state_counts
from docksurf_py.core.types import SafeMarkup

_LABEL_WIDTH = 8


@dataclass(frozen=True)
class CardSection:
    """One `▸` row under MORE. `content` is plain text (escaped by the pane), a
    Rich renderable, or None to leave whatever the section already shows."""

    key: str
    summary: str
    hint: str = ""
    content: str | RenderableType | None = None


@dataclass(frozen=True)
class CardModel:
    """`key` identifies the thing shown: the pane keeps each section's
    open/closed state across re-renders of the same key and resets it when the
    key changes."""

    key: str
    title: SafeMarkup
    subtitle: str
    body: RenderableType
    sections: tuple[CardSection, ...] = ()


def section_rule(label: str, p: Palette) -> Rule:
    """`NETWORK ─────────`. Expands to whatever width it is rendered at, so the
    card needs no resize handling."""
    return Rule(Text(label, style=p.muted), align="left", characters="─", style=p.faint)


def _kv(
    p: Palette,
    rows: Sequence[tuple[str, str | RenderableType]],
    label_width: int = _LABEL_WIDTH,
) -> Table:
    """Label/value grid; a string value is Rich markup (already escaped), any
    other value a renderable."""
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(style=p.muted, no_wrap=True, width=label_width)
    grid.add_column(ratio=1, overflow="fold")
    for label, value in rows:
        grid.add_row(
            label, Text.from_markup(value) if isinstance(value, str) else value
        )
    return grid


def _facts_line(p: Palette, head: str, facts: Sequence[str]) -> Text:
    """`head` (a pill) then the facts separated by ` · `."""
    sep = f"  {fg(p, 'faint', '·')}  "
    return Text.from_markup(head + ("   " + sep.join(facts) if facts else ""))


def _interval(seconds: float) -> str:
    return f"{seconds:g}s" if seconds < 60 else f"{seconds / 60:g}m"


def _plural(n: int, noun: str) -> str:
    if n == 1:
        return f"{n} {noun}"
    return f"{n} {noun}{'es' if noun.endswith('s') else 's'}"


# --- Container card ------------------------------------------------------------


def _container_pill_line(
    c: Container, detail: ContainerDetail | None, p: Palette
) -> Text:
    state = classify(c)
    facts: list[str] = []
    health = {
        "healthy": ("success", "✓ healthy"),
        "unhealthy": ("error", "✕ unhealthy"),
        "starting": ("warning", "◐ starting"),
    }.get(c.health)
    if c.running:
        if health is not None:
            facts.append(fg(p, *health))
        started = format_uptime(detail.started_at) if detail else "—"
        up = started if started != "—" else compact_age(c.status, c.uptime_hint)
        if up != "—":
            facts.append(fg(p, "muted", f"up {up}"))
    else:
        if c.state == "exited":
            facts.append(fg(p, "muted", f"exited {c.exit_code}"))
        age = compact_age(c.status)
        if age != "—":
            facts.append(fg(p, "muted", age))
    if detail is not None:
        n = detail.restart_count
        facts.append(fg(p, "warning" if n else "muted", _plural(n, "restart")))
    return _facts_line(p, pill(p, state.value.upper(), ROLE[state]), facts)


def _port_rows(c: Container, p: Palette) -> list[str]:
    published: list[str] = []
    for port in c.ports:
        if not port.host_port:
            continue
        ip = port.host_ip if port.host_ip not in ("", "::") else "0.0.0.0"
        line = f"{ip}:{port.host_port} → {port.container_port}"
        if line not in published:  # IPv4 and IPv6 bindings repeat the same port
            published.append(line)
    if published:
        lines = [escape(line) for line in published]
        lines[0] += f"   {fg(p, 'muted', 'O open')}"
        return lines
    exposed = sorted({port.container_port for port in c.ports})
    if exposed:
        return [fg(p, "muted", f"{', '.join(exposed)} (not published)")]
    return [fg(p, "faint", "none")]


def _network_rows(
    c: Container, detail: ContainerDetail | None, p: Palette
) -> list[str]:
    if not c.networks:
        return [fg(p, "faint", "none")]
    ips = detail.network_ips if detail else {}
    return [
        escape(name)
        + (f" {fg(p, 'faint', '·')} {escape(ips[name])}" if name in ips else "")
        for name in c.networks
    ]


def _env_section(detail: ContainerDetail | None, reveal: bool) -> CardSection | None:
    if detail is None:
        return CardSection("env", "env · …", "", "…")
    if not detail.env:
        return None
    masked = 0 if reveal else secret_env_count(detail.env)
    summary = _plural(len(detail.env), "var")
    if reveal:
        summary += " · revealed"
    elif masked:
        summary += f" · {masked} masked"
    hint = "R mask" if reveal else "R reveal" if secret_env_count(detail.env) else ""
    return CardSection(
        "env", f"env · {summary}", hint, format_env(detail.env, reveal=reveal)
    )


def _health_section(c: Container, detail: ContainerDetail | None) -> CardSection | None:
    if detail is None or not (detail.health_log or c.health):
        return None
    probes = detail.health_log
    if probes:
        ok = sum(1 for probe in probes if probe.exit_code == 0)
        summary = f"{ok}/{len(probes)} ok"
    else:
        summary = c.health or "no probes yet"
    if detail.health_interval_s:
        summary += f" · every {_interval(detail.health_interval_s)}"
    return CardSection(
        "health", f"health · {summary}", "", format_health_log(probes) or "—"
    )


def _top_section(
    c: Container, top: ContainerTop | None, loading: bool
) -> CardSection | None:
    if not c.running:
        return None
    if top is not None:
        return CardSection(
            "top",
            f"top · {_plural(len(top.processes), 'process')}",
            "t toggle",
            render_top(top, c.name),
        )
    if loading:
        return CardSection("top", "top · …", "t toggle", "fetching processes…")
    return CardSection("top", "top", "t toggle", "")


def container_card(
    c: Container,
    detail: ContainerDetail | None,
    p: Palette,
    reveal: bool = False,
    *,
    top: ContainerTop | None = None,
    top_loading: bool = False,
) -> CardModel:
    """The container detail card. `detail` is None until the lazy inspect
    lands (env/health/restarts/IPs show as pending); `top` is the last
    process snapshot fetched with `t`."""
    ident: list[tuple[str, str]] = []
    if c.is_compose:
        ident.append(
            (
                "project",
                f"{escape(c.compose_project)} {fg(p, 'faint', '›')} "
                f"{escape(c.compose_service or c.name)}",
            )
        )
    sha = c.image_id.removeprefix("sha256:")[:8]
    ident.append(
        (
            "image",
            escape(c.image_name)
            + (
                f" {fg(p, 'faint', '·')} {fg(p, 'muted', f'sha256:{sha}…')}"
                if sha
                else ""
            ),
        )
    )
    created = format_relative_time(c.created)
    ident.append(
        (
            "id",
            f"{escape(c.id[:12])} {fg(p, 'faint', '·')} "
            f"{fg(p, 'muted', f'created {created}')}",
        )
    )
    ports = _port_rows(c, p)
    network = _network_rows(c, detail, p)
    sections = tuple(
        s
        for s in (
            _env_section(detail, reveal),
            _top_section(c, top, top_loading),
            _health_section(c, detail),
        )
        if s is not None
    )
    parts: list[RenderableType] = [
        _container_pill_line(c, detail, p),
        Text(""),
        _kv(p, ident),
        Text(""),
        section_rule("NETWORK", p),
        _kv(p, [("ports", "\n".join(ports)), ("network", "\n".join(network))]),
    ]
    if sections:
        parts += [Text(""), section_rule("MORE", p)]
    return CardModel(
        key=c.id,
        title=SafeMarkup(f"{state_glyph(c, p)} {escape(c.name)} · container"),
        subtitle="i inspect",
        body=Group(*parts),
        sections=sections,
    )


# --- Compose project card ----------------------------------------------------------

_PROJECT_HINTS: tuple[tuple[str, str], ...] = (
    ("s", "stop"),
    ("S", "start"),
    ("x", "restart"),
    ("^u", "up"),
    ("^k", "down"),
    ("l", "project logs"),
)


def project_card(project: ComposeProject, p: Palette) -> CardModel:
    members = sorted(project.containers, key=lambda c: (c.compose_service, c.name))
    up, _down, failed = project_state_counts(members)
    total = len(members)
    role = "success" if total and up == total else "warning" if up else "muted"
    head = pill(p, f"{up}/{total} UP", role)
    facts = [fg(p, "error", f"{failed} failed")] if failed else []

    services = "\n".join(
        f"{state_glyph(c, p)} {escape(c.compose_service or c.name)}  {state_cell(c, p)}"
        for c in members
    ) or fg(p, "faint", "none")
    configs = [escape(f.strip()) for f in project.config_files.split(",") if f.strip()]
    rows = [
        ("services", services),
        (
            "dir",
            escape(project.working_dir) if project.working_dir else fg(p, "faint", "—"),
        ),
        ("config", "\n".join(configs) if configs else fg(p, "faint", "—")),
    ]
    hints = "  ".join(
        f"{key_chip(p, key)} {fg(p, 'muted', label)}" for key, label in _PROJECT_HINTS
    )
    glyph = fg(p, role, "●")
    return CardModel(
        key=f"project:{project.name}",
        title=SafeMarkup(f"{glyph} {escape(project.name)} · project"),
        subtitle="",
        body=Group(
            _facts_line(p, head, facts),
            Text(""),
            _kv(p, rows),
            Text(""),
            section_rule("ACTIONS", p),
            Text.from_markup(hints),
        ),
    )


# --- Image card ------------------------------------------------------------------

_IMAGE_PILL_ROLE = {"in use": "success", "unused": "muted", "dangling": "warning"}


def _image_used_by_rows(containers: Sequence[Container], p: Palette) -> list[Text]:
    rows: list[Text] = []
    for index, c in enumerate(containers):
        where = (
            f"{escape(c.compose_project)} {fg(p, 'faint', '›')} "
            f"{escape(c.compose_service or c.name)}"
            f" {fg(p, 'faint', '·')} "
            if c.is_compose
            else ""
        )
        line = f"{state_glyph(c, p)} {escape(c.name)}  {where}{fg(p, 'muted', c.state)}"
        if index == 0:
            line += f"   {fg(p, 'muted', '⏎ jump')}"
        rows.append(Text.from_markup(line))
    return rows


def image_card(
    image: Image,
    platform: ImagePlatform | None,
    tags: Sequence[str],
    containers: Sequence[Container],
    p: Palette,
) -> CardModel:
    """The image detail card. `platform` is None until the lazy inspect lands;
    `tags` are every tag of this image id (see `image_tags`); `containers` are
    the ones running from it (see `related_containers`)."""
    status = image_status(image)
    chips = [
        pill(p, status.upper(), _IMAGE_PILL_ROLE[status]),
        key_chip(p, platform.architecture if platform else "…"),
        key_chip(p, platform.os if platform else "…"),
    ]
    facts = [
        fg(p, "muted", format_size(image.size_bytes)),
        fg(p, "muted", f"created {format_relative_time(image.created)}"),
    ]
    sha = image.id.removeprefix("sha256:")[:12]
    tag_text = ", ".join(escape(t) for t in tags) or fg(p, "faint", "none")
    rows = [
        ("id", f"sha256:{escape(sha)}   {fg(p, 'muted', 'i inspect')}"),
        ("tags", f"{tag_text}   {fg(p, 'muted', 'y tag')}"),
    ]
    used_by: list[RenderableType] = (
        list(_image_used_by_rows(containers, p))
        if containers
        else [Text.from_markup(fg(p, "faint", "none"))]
    )
    name = f"{image.repository}:{image.tag}"
    return CardModel(
        key=f"image:{image.id}|{name}",
        title=SafeMarkup(f"{status_glyph(image, p)} {escape(name)} · image"),
        subtitle="",
        body=Group(
            _facts_line(p, " ".join(chips), facts),
            Text(""),
            _kv(p, rows),
            Text(""),
            section_rule("USED BY", p),
            *used_by,
        ),
    )


# --- Volume card ---------------------------------------------------------------

_VOLUME_LABEL_WIDTH = 10
# Longest hash-name prefix/suffix shown in a volume card title.
_TITLE_HEAD = 8
_TITLE_TAIL = 6
_LABELS_SHOWN = 8


def _volume_title(v: Volume, p: Palette) -> SafeMarkup:
    if not is_anonymous(v):
        return SafeMarkup(f"{escape(v.name)} · volume")
    name = v.name
    if len(name) > _TITLE_HEAD + _TITLE_TAIL + 1:
        name = f"{name[:_TITLE_HEAD]}…{name[-_TITLE_TAIL:]}"
    return SafeMarkup(f"{fg(p, 'secondary', ANON_GLYPH)} {escape(name)} · volume")


def _volume_label_chips(v: Volume, p: Palette) -> str:
    if not v.labels:
        return fg(p, "faint", "none")
    shown = [
        key_chip(p, f"{k}={val}") for k, val in list(v.labels.items())[:_LABELS_SHOWN]
    ]
    hidden = len(v.labels) - len(shown)
    if hidden > 0:
        shown.append(fg(p, "muted", f"+{hidden} more   i inspect"))
    return " ".join(shown)


def volume_card(
    v: Volume, size: int | None, containers: Sequence[Container], p: Palette
) -> CardModel:
    """The volume detail card. `size` is None until `b` has computed it;
    `containers` are the ones mounting the volume (see `related_containers`)."""
    in_use = bool(v.used_by)
    chips = [
        pill(p, "IN USE" if in_use else "ORPHANED", "success" if in_use else "warning")
    ]
    if is_anonymous(v):
        chips.append(pill(p, "anonymous", "secondary"))
    facts = [
        fg(p, "muted", format_size(size) if size is not None else "· · ·"),
        fg(p, "muted", f"driver {v.driver}"),
    ]
    used_by: RenderableType
    if containers:
        used_by = Group(*_image_used_by_rows(containers, p))
    elif v.used_by:
        used_by = Text.from_markup(fg(p, "muted", ", ".join(v.used_by)))
    else:
        used_by = Text.from_markup(fg(p, "muted", "nothing"))
    rows: list[tuple[str, str | RenderableType]] = [
        ("name", f"{escape(v.name)}   {fg(p, 'muted', 'Y copy')}"),
        ("mountpoint", fg(p, "muted", escape(v.mountpoint))),
        ("labels", _volume_label_chips(v, p)),
        ("used by", used_by),
    ]
    return CardModel(
        key=f"volume:{v.name}",
        title=_volume_title(v, p),
        subtitle="",
        body=Group(
            _facts_line(p, " ".join(chips), facts),
            Text(""),
            _kv(p, rows, _VOLUME_LABEL_WIDTH),
        ),
    )
