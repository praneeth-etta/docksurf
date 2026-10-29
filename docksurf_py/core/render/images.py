"""Image table cells: pure builders for the Images tab (Tide)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.markup import escape

from docksurf_py.core.docker.format import _parse_docker_ts, format_size
from docksurf_py.core.models import Image, ImageLayer
from docksurf_py.core.render.markup import truncate
from docksurf_py.core.render.palette import Palette, fg, pill
from docksurf_py.core.types import SafeMarkup

# Content widths of the Images table columns (the REPOSITORY column can't be
# `1fr` in a DataTable, so it gets a fixed width like the container NAME).
REPO_WIDTH = 32
TAG_WIDTH = 12
SIZE_WIDTH = 12
CREATED_WIDTH = 9
STATUS_WIDTH = 11

# Longest registry pill label for an unrecognised host.
_PILL_HOST_MAX = 10

_DOCKER_HUB = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
_NONE = "<none>"


@dataclass(frozen=True)
class RepoParts:
    """A repository reference split for display.

    `registry` is the pill label (`ecr`, `ghcr`, ...) or None for Docker Hub;
    `namespace` is everything between the registry and the name (`/`-joined,
    empty if there is none); `name` is the last path segment.
    """

    registry: str | None
    namespace: str
    name: str


def _is_host(segment: str) -> bool:
    """Docker's own rule: the first path segment is a registry host if it
    contains `.` or `:` or is `localhost`."""
    return "." in segment or ":" in segment or segment == "localhost"


def _pill_label(host: str) -> str:
    if host.endswith(".amazonaws.com") and ".dkr.ecr" in host:
        return "ecr"
    if host == "ghcr.io":
        return "ghcr"
    if host == "quay.io":
        return "quay"
    if host.endswith(".gcr.io") or host == "gcr.io" or host.endswith("-docker.pkg.dev"):
        return "gcr"
    if len(host) > _PILL_HOST_MAX:
        return host[: _PILL_HOST_MAX - 1] + "…"
    return host


def split_repository(repo: str) -> RepoParts:
    """Split `repo` (no tag) into registry pill, namespace and name.

    `<acct>.dkr.ecr.<region>.amazonaws.com/<name>` keeps the account id as the
    namespace; `docker.io/library/x`, `library/x` and `x` all have no registry
    and no namespace; `docker.io/org/x` has namespace `org`.
    """
    if not repo or repo == _NONE:
        return RepoParts(None, "", _NONE)
    segments = repo.split("/")
    registry: str | None = None
    namespace: list[str] = []
    if len(segments) > 1 and _is_host(segments[0]):
        host = segments.pop(0)
        if host not in _DOCKER_HUB:
            registry = _pill_label(host)
            if registry == "ecr":
                namespace.append(host.split(".", 1)[0])  # the account id
    name = segments.pop()
    if registry is None and segments == ["library"]:
        segments = []
    return RepoParts(registry, "/".join([*namespace, *segments]), name)


def _middle(text: str, width: int) -> str:
    """`text` cut to `width` cells with a middle `…`, keeping both ends."""
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    keep = width - 1
    head = (keep + 1) // 2
    tail = keep - head
    return text[:head] + "…" + (text[len(text) - tail :] if tail else "")


def repository_cell(repo: str, p: Palette, max_width: int) -> SafeMarkup:
    """The REPOSITORY cell: `ecr  acct › name`, `org/name` or a muted
    `<none>`, at most `max_width` cells wide.

    Too long: the namespace is middle-truncated first (down to a lone `…`),
    then the name. If even a lone-`…` prefix doesn't fit with the name, the
    pill and namespace are dropped and the name is cut to the width.
    """
    parts = split_repository(repo)
    if parts.name == _NONE:
        return fg(p, "muted", _middle(_NONE, max_width))

    pill_width = len(parts.registry) + 2 if parts.registry else 0
    # Cells around the namespace: `<pill> ` before it, ` › ` or `/` after it.
    lead = pill_width + 1 if parts.registry else 0
    sep = " › " if parts.registry else "/"
    namespace = parts.namespace
    name = parts.name

    def fixed() -> int:
        return lead + (len(sep) if namespace else 0)

    if lead + len(sep) * bool(namespace) + len(namespace) + len(name) > max_width:
        if namespace:
            room = max_width - fixed() - len(name)
            namespace = _middle(namespace, max(room, 1))
        if fixed() + len(namespace) + len(name) > max_width:
            # Not even `…` fits next to the name: keep just the name.
            return fg(p, "strong", _middle(name, max_width))

    out = ""
    if parts.registry:
        out += pill(p, parts.registry, "secondary") + " "
    if namespace:
        out += fg(p, "muted", namespace + sep)
    return SafeMarkup(out + f"[{p.strong}]{escape(name)}[/]")


def image_tags(image: Image, images: Sequence[Image]) -> list[str]:
    """Every tag of `image`'s id, in list order: `latest` when it shares the
    image's repository, `repo:tag` otherwise. Untagged (`<none>`) is skipped."""
    tags: list[str] = []
    for other in images:
        if other.id != image.id or other.is_dangling:
            continue
        label = (
            other.tag
            if other.repository == image.repository
            else f"{other.repository}:{other.tag}"
        )
        if label not in tags:
            tags.append(label)
    return tags


def image_status(image: Image) -> str:
    """`in use`, `dangling` or `unused` (a dangling image a container still
    uses counts as in use, matching the chips)."""
    if image.used_by:
        return "in use"
    return "dangling" if image.is_dangling else "unused"


# STATUS sort order: in use first, then unused, then dangling.
_STATUS_ORDER = {"in use": 0, "unused": 1, "dangling": 2}


def status_rank(image: Image) -> int:
    return _STATUS_ORDER[image_status(image)]


_STATUS_STYLE = {
    "in use": ("●", "success"),
    "unused": ("○", "muted"),
    "dangling": ("✕", "warning"),
}


def status_glyph(image: Image, p: Palette) -> SafeMarkup:
    """`●` (in use), `○` (unused) or `✕` (dangling), in the status colour."""
    glyph, role = _STATUS_STYLE[image_status(image)]
    return fg(p, role, glyph)


def status_cell(image: Image, p: Palette) -> SafeMarkup:
    """`● in use` (success), `○ unused` (muted), `✕ dangling` (warning)."""
    glyph, role = _STATUS_STYLE[image_status(image)]
    return fg(p, role, f"{glyph} {image_status(image)}")


def compact_relative(created: str, now: datetime | None = None) -> str:
    """`3d`, `2w`, `2mo`: the age of an RFC3339 timestamp in one unit,
    `now` under a minute, `—` when unknown."""
    dt = _parse_docker_ts(created) if created else None
    if dt is None:
        return "—"
    seconds = int(((now or datetime.now(timezone.utc)) - dt).total_seconds())
    minute, hour, day = 60, 3600, 86400
    if seconds < minute:
        return "now"
    if seconds < hour:
        return f"{seconds // minute}m"
    if seconds < day:
        return f"{seconds // hour}h"
    if seconds < 7 * day:
        return f"{seconds // day}d"
    if seconds < 30 * day:
        return f"{seconds // (7 * day)}w"
    if seconds < 365 * day:
        return f"{seconds // (30 * day)}mo"
    return f"{seconds // (365 * day)}y"


def image_row_cells(
    image: Image, p: Palette, now: datetime | None = None
) -> tuple[SafeMarkup, ...]:
    """The five non-gutter cells: repository, tag, size (right-aligned),
    created, status."""
    tag = truncate(image.tag, TAG_WIDTH)
    return (
        repository_cell(image.repository, p, REPO_WIDTH),
        fg(p, "muted" if image.tag == _NONE else "foreground", tag),
        fg(p, "foreground", format_size(image.size_bytes).rjust(SIZE_WIDTH)),
        fg(p, "muted", compact_relative(image.created, now)),
        status_cell(image, p),
    )


def marked_size(items: Sequence[Image]) -> int:
    """Total size of the marked images, counting each image id once: several
    tag rows of one image share its layers."""
    return sum({i.id: i.size_bytes for i in items}.values())


def marked_summary(items: Sequence[Image], p: Palette) -> SafeMarkup:
    """`✓ 3 marked · 7.17GB`: the number of marked rows and the disk they
    would free (see `marked_size`)."""
    return SafeMarkup(
        f"[bold {p.primary}]✓ {len(items)} marked[/]"
        + fg(p, "muted", f" · {format_size(marked_size(items))}")
    )


def images_footer(marked: Sequence[Image], p: Palette) -> SafeMarkup:
    """The Images panel's bottom line: the marked summary with its actions,
    or a note about the shortened registry names when nothing is marked."""
    if not marked:
        return fg(p, "muted", "registry prefixes shortened · full name in detail")
    keys = SafeMarkup(
        "   ".join(
            fg(p, "strong", key) + fg(p, "muted", f" {action}")
            for key, action in (
                ("d", "delete marked"),
                ("a", "mark dangling"),
                ("esc", "clear"),
            )
        )
    )
    return SafeMarkup(f"{marked_summary(marked, p)}{fg(p, 'muted', ' · ')}{keys}")


# --- Layers panel ------------------------------------------------------------

LAYER_SIZE_WIDTH = 10

_NOP_PREFIX = re.compile(r"^/bin/sh -c #\(nop\)\s*")
# What the oldest layer of a base image typically looks like in `docker history`.
_BASE_LAYER = re.compile(r"^(ADD|COPY)\s+(file|rootfs)\b|^# debian\.sh|^FROM\b")


def clean_layer_command(created_by: str) -> str:
    """A layer's build instruction as one tidy line: `/bin/sh -c #(nop) `
    stripped (`CMD ["bash"]`), a shell command shown as `RUN ...`, a trailing
    `# buildkit` dropped and whitespace collapsed."""
    command = created_by.strip()
    if _NOP_PREFIX.match(command):
        command = _NOP_PREFIX.sub("", command)
    elif command.startswith("/bin/sh -c "):
        command = "RUN " + command.removeprefix("/bin/sh -c ")
    command = re.sub(r"\s*#\s*buildkit\s*$", "", command)
    return " ".join(command.split()) or "—"


def layers_title(layers: Sequence[ImageLayer]) -> str:
    """`layers · 14 · largest first`."""
    return f"layers · {len(layers)} · largest first"


def _base_layer(layers: Sequence[ImageLayer]) -> ImageLayer | None:
    """Best-effort: the oldest layer that has content, if it looks like a base
    image's root filesystem. `docker history` lists newest first."""
    for layer in reversed(layers):
        if layer.size_bytes > 0:
            if _BASE_LAYER.match(clean_layer_command(layer.created_by)):
                return layer
            return None
    return None


def layers_summary(
    layers: Sequence[ImageLayer], p: Palette, max_rows: int, width: int
) -> SafeMarkup:
    """The inline layers list: the biggest layers first, size right-aligned,
    the build command ellipsized to `width`, then a line counting what was
    left out. The base image's layer is muted and marked `(base)`.

    Metadata-only layers (size 0) are never listed, only counted. At most
    `max_rows` lines are returned, the last being the "left out" line when
    there is one (with a single row, the largest layer alone).
    """
    real = sorted(
        (layer for layer in layers if layer.size_bytes > 0),
        key=lambda layer: -layer.size_bytes,
    )
    empty = len(layers) - len(real)
    # Reserve a line for the summary when anything will be left out.
    if max_rows <= 0:
        return SafeMarkup("")
    room = max_rows
    # One row is better spent on the largest layer than on the summary.
    if max_rows > 1 and (empty or len(real) > room):
        room -= 1
    shown, hidden = real[:room], max(len(real) - room, 0)
    base = _base_layer(layers)
    command_width = max(width - LAYER_SIZE_WIDTH - 2, 1)
    lines: list[str] = []
    for layer in shown:
        is_base = layer is base
        command = clean_layer_command(layer.created_by)
        suffix = " (base)" if is_base else ""
        text = truncate(command, max(command_width - len(suffix), 1)) + suffix
        size = format_size(layer.size_bytes).rjust(LAYER_SIZE_WIDTH)
        role = "muted" if is_base else "foreground"
        lines.append(fg(p, role, f"{size}  {text}"))
    left_out: list[str] = []
    if hidden:
        left_out.append(f"{hidden} more")
    if empty:
        left_out.append(f"{empty} empty metadata layer{'s' if empty != 1 else ''}")
    if left_out and max_rows > 1:
        lines.append(fg(p, "faint", "… " + " · ".join(left_out)))
    return SafeMarkup("\n".join(lines))
