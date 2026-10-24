"""Row identity, Compose grouping, and search matching for the resource tables."""

from collections import defaultdict
from typing import Any, Callable

from docksurf_py.core.models import ComposeProject, Container, Image, Network, Volume


def row_key(item: Any) -> tuple[str, str] | None:
    """Stable identity for a row-backing object, for selection restore."""
    if isinstance(item, Container):
        return ("container", item.id)
    if isinstance(item, ComposeProject):
        return ("project", item.name)
    if isinstance(item, Image):
        # `get_images()` emits one row per tag, all sharing `item.id` —
        # the tag must be part of the identity, or marking/selection
        # restore/delete on one tag row would silently affect every tag
        # row of the same image (BF-3).
        return ("image", f"{item.id}|{item.repository}:{item.tag}")
    if isinstance(item, Volume):
        return ("volume", item.name)
    if isinstance(item, Network):
        return ("network", item.name)
    return None


def group_by_project(
    containers: list[Container],
    key: Callable[[Container], Any] | None = None,
    reverse: bool = False,
) -> tuple[list[ComposeProject], list[Container]]:
    """Split containers into Compose projects (sorted) and standalone ones.

    Project ordering is always alphabetical (a header isn't a sortable data
    row). Services within a project are sorted by `key`/`reverse` when a
    column sort is active, else by service name (the original default). A
    project's config-file/working-dir come from its first service (they're
    identical across a project's containers).
    """
    grouped: dict[str, list[Container]] = defaultdict(list)
    standalone: list[Container] = []
    for c in containers:
        if c.is_compose:
            grouped[c.compose_project].append(c)
        else:
            standalone.append(c)

    projects: list[ComposeProject] = []
    for name in sorted(grouped):
        if key is not None:
            members = sorted(grouped[name], key=key, reverse=reverse)
        else:
            members = sorted(grouped[name], key=lambda c: c.compose_service)
        first = members[0]
        projects.append(
            ComposeProject(
                name=name,
                containers=members,
                config_files=first.compose_config_files,
                working_dir=first.compose_working_dir,
            )
        )
    return projects, standalone


def matches_container(c: Container, q: str) -> bool:
    return (
        q in c.name.lower()
        or q in c.image_name.lower()
        or q in c.status.lower()
        or q in c.id.lower()
        or q in c.compose_project.lower()
        or q in c.compose_service.lower()
    )


def matches_image(i: Image, q: str) -> bool:
    return q in (i.repository or "").lower() or q in (i.tag or "").lower()


def matches_volume(v: Volume, q: str) -> bool:
    return q in v.name.lower() or q in v.driver.lower() or q in v.mountpoint.lower()


def matches_network(n: Network, q: str) -> bool:
    return q in n.name.lower() or q in n.driver.lower() or q in n.subnet.lower()
