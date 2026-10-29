"""JumpHandler — `enter` on an image, volume or network jumps to the
container(s) using it.

Which containers are related is core's `related_containers`; what has to
change for the row to be visible is `TablesState.reveal`. This mixin only
drives the UI: notify, pick among several, switch tab, select the row.
"""

from rich.markup import escape
from textual import work
from textual.widgets import Input, TabbedContent

from docksurf_py.core.models import Container
from docksurf_py.core.state.navigation import related_containers
from docksurf_py.core.state.tables import row_key
from docksurf_py.core.types import TabID
from docksurf_py.tui.actions.common import _Base, _display_name
from docksurf_py.tui.ids import SEARCH_BAR_ID
from docksurf_py.tui.widgets import ContainerPickerScreen

JUMP_TABS = (TabID.IMAGES, TabID.VOLUMES, TabID.NETWORKS)


def _picker_label(c: Container) -> str:
    where = (
        f"{c.compose_project} › {c.compose_service or c.name}" if c.is_compose else ""
    )
    return f"{c.name}   {where + ' · ' if where else ''}{c.state}"


class JumpHandler(_Base):
    """`enter` → select the related container on the Containers tab."""

    def action_jump_related(self) -> None:
        active = TabID(self.query_one(TabbedContent).active)
        if active not in JUMP_TABS:
            self.notify(
                "Jump works on the Images, Volumes and Networks tabs",
                severity="warning",
            )
            return
        item = self._get_focused_resource(active)
        if item is None:
            return
        targets = related_containers(item, self.snapshot)
        label = self._resource_registry[active].label
        if not targets:
            self.notify(f"Nothing to jump to: no container uses this {label}")
        elif len(targets) == 1:
            self._jump_to_container(targets[0])
        else:
            self._pick_container_to_jump(f"Jump to {_display_name(item)}", targets)

    @work
    async def _pick_container_to_jump(
        self, title: str, targets: list[Container]
    ) -> None:
        options = [(c.id, _picker_label(c)) for c in targets]
        chosen = await self.push_screen_wait(
            ContainerPickerScreen(escape(title), options)
        )
        target = next((c for c in targets if c.id == chosen), None)
        if target is not None:
            self._jump_to_container(target)

    def _jump_to_container(self, container: Container) -> None:
        """Show the Containers tab with `container`'s row selected.

        The cursor can't be moved yet: the tab isn't active and its table may
        need rebuilding. The row is recorded as pending focus instead, and
        `clear_on_tab_switch` selects it once the tab has settled.
        """
        search = self.query_one(f"#{SEARCH_BAR_ID}", Input)
        reveal = self._tables.reveal(TabID.CONTAINERS, container, search.value)
        if reveal.query_hides:
            search.value = ""
        self._tables.request_focus(row_key(container))
        tabbed = self.query_one(TabbedContent)
        if tabbed.active == TabID.CONTAINERS:  # not reachable from a jump tab
            self._select_pending_focus()
        else:
            tabbed.active = TabID.CONTAINERS

    def _select_pending_focus(self) -> None:
        """Select the row a jump asked for, if the Containers tab is showing."""
        key = self._tables.take_pending_focus()
        if key is not None and self.query_one(TabbedContent).active == TabID.CONTAINERS:
            self._restore_selection(TabID.CONTAINERS, key)
