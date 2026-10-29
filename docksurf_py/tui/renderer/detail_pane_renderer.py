"""DetailPaneRenderer — formats and pushes resource details into the side pane."""

from rich.markup import escape as _escape
from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.docker import (
    format_labels,
    format_size,
)
from docksurf_py.core.models import ComposeProject
from docksurf_py.core.render.cards import container_card, image_card, project_card
from docksurf_py.core.render.images import image_tags
from docksurf_py.core.render.topology import network_topology
from docksurf_py.core.state.navigation import related_containers
from docksurf_py.core.types import (
    SafeMarkup,
    TabID,
    markup_yellow,
)
from docksurf_py.tui.ids import DETAIL_PANE_ID
from docksurf_py.tui.renderer.common import _Base
from docksurf_py.tui.widgets import DetailPane


def _dim(value: object) -> SafeMarkup:
    """Mute a low-signal value (a raw ID/SHA) so it doesn't compete for
    attention with the fields a user actually scans for."""
    return SafeMarkup(f"[dim]{_escape(str(value))}[/]")


class DetailPaneRenderer(_Base):
    """Formats and pushes resource details into the side pane on row highlight."""

    def _show_container_details(self, pane: DetailPane, row: int) -> None:
        items = self._tables.rows.get(TabID.CONTAINERS, [])
        if row >= len(items):
            return
        item = items[row]
        p = self._view.palette
        if isinstance(item, ComposeProject):
            pane.update_card(project_card(item, p))
            pane.clear_topology()
            return
        c = item
        # env/health-log/started-at/restart-count/IPs aren't in the list summary.
        # They're fetched lazily on selection, cached by container id, and remain
        # `None` until that fetch succeeds (or fails).
        detail = self._details.container_detail(c.id)
        pane.update_card(
            container_card(
                c,
                detail,
                p,
                self._details.reveal_secrets,
                top=self._live.top_for(c.id),
                top_loading=self._live.top_target == c.id,
            )
        )
        pane.clear_topology()

    def _refresh_detail_card(self) -> None:
        """Re-render the focused row's card (after env reveal, `t`, a detail
        fetch landing, ...)."""
        if not self.is_running:
            return
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None:
            return
        table = self.query_one(f"#{entry.table_id}", DataTable)
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        try:
            entry.show_details(pane, table.cursor_row)
        except IndexError:
            pane.clear_details()

    def _show_image_details(self, pane: DetailPane, row: int) -> None:
        images = self._tables.rows.get(TabID.IMAGES, [])
        if row >= len(images):
            return
        image = images[row]
        snapshot = self.snapshot
        # Architecture/Os are fetched lazily on row-select and cached by
        # ImageActionHandler._sync_image_platform; pending until it resolves.
        pane.update_card(
            image_card(
                image,
                self._details.platform(image.id),
                image_tags(image, snapshot.images if snapshot else images),
                related_containers(image, snapshot),
                self._view.palette,
            )
        )
        pane.clear_topology()
        self._paint_image_layers(image)

    def _show_volume_details(self, pane: DetailPane, row: int) -> None:
        volumes = self._tables.rows.get(TabID.VOLUMES, [])
        if row >= len(volumes):
            return
        volume = volumes[row]

        identity = {
            "Mountpoint": volume.mountpoint,
            "Driver": volume.driver,
            "Labels": format_labels(volume.labels) if volume.labels else "None",
        }
        usage = {
            "Used By": (
                "\n".join(volume.used_by)
                if volume.used_by
                else markup_yellow("Orphaned (safe to delete)")
            ),
        }
        # Size is fetched on-demand (`b`); once known it's cached and shown here.
        size = self._details.volume_size(volume.name)
        if size is not None:
            usage["Size on disk"] = format_size(size)
        pane.update_details(
            f"Volume: {volume.name}", {"Identity": identity, "Usage": usage}
        )
        pane.clear_topology()

    def _show_network_details(self, pane: DetailPane, row: int) -> None:
        networks = self._tables.rows.get(TabID.NETWORKS, [])
        if row >= len(networks):
            return
        network = networks[row]

        identity = {
            "ID": _dim(network.id),
            "Driver": network.driver,
            "Scope": network.scope,
        }
        addressing = {
            "Subnet": network.subnet,
            "Gateway": network.gateway,
        }

        pane.update_details(
            f"Network: {network.name}",
            {"Identity": identity, "Addressing": addressing},
        )
        # The attached-containers list is drawn as the topology diagram below
        # the panel (endpoints joined with snapshot containers), replacing the
        # old plaintext "Attached" section.
        containers = self.snapshot.containers if self.snapshot else []
        pane.update_topology(network_topology(network, containers))
