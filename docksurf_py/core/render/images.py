"""Image table cells: pure builders for the Images tab (Tide)."""

from dataclasses import dataclass
from datetime import datetime, timezone

from rich.markup import escape

from docksurf_py.core.docker.format import _parse_docker_ts, format_size
from docksurf_py.core.models import Image
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


def status_cell(image: Image, p: Palette) -> SafeMarkup:
    """`● in use` (success), `○ unused` (muted), `✕ dangling` (warning)."""
    status = image_status(image)
    glyph, role = {
        "in use": ("●", "success"),
        "unused": ("○", "muted"),
        "dangling": ("✕", "warning"),
    }[status]
    return fg(p, role, f"{glyph} {status}")


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
