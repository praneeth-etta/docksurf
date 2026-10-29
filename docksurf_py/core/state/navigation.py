"""Navigation targets: which containers a resource is related to, for the
"jump to related" action (`enter` on an image, volume or network row).

Pure lookups over a snapshot — the tui decides how to present the result
(jump straight to one container, or let the user pick among several).
"""

from docksurf_py.core.models import (
    Container,
    DockerSnapshot,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.render.topology import network_members


def related_containers(
    item: object, snapshot: DockerSnapshot | None
) -> list[Container]:
    """The containers `item` is used by, sorted by name.

    - Image: containers created from it (matched by image id, or by name
      through the snapshot's `used_by` correlation).
    - Volume: containers that mount it.
    - Network: containers attached to it (the same endpoint join the topology
      diagram uses; an endpoint whose container isn't listed can't be jumped to).

    Anything else (containers, Compose projects) has no related containers.
    """
    if snapshot is None:
        return []
    containers = snapshot.containers
    if isinstance(item, Image):
        found = [
            c for c in containers if c.image_id == item.id or c.name in item.used_by
        ]
    elif isinstance(item, Volume):
        found = [
            c for c in containers if item.name in c.mounts or c.name in item.used_by
        ]
    elif isinstance(item, Network):
        attached = {m.name for m in network_members(item, containers)}
        found = [c for c in containers if c.name in attached]
    else:
        return []
    return sorted(found, key=lambda c: c.name)
