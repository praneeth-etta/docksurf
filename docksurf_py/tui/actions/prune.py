"""PruneHandler — `docker system prune`-family cleanup."""

import logging
from typing import Callable

from textual import work
from textual.widgets import TabbedContent

from docksurf_py.core.models import CommandResult, Volume
from docksurf_py.core.render.volumes import ORPHANED_KEY
from docksurf_py.core.state.ops import Refusal, plan_orphan_prune
from docksurf_py.core.state.tables import RowGroup
from docksurf_py.core.types import TabID
from docksurf_py.tui.actions.common import _Base
from docksurf_py.tui.widgets import ConfirmDialog, PruneScreen

logger = logging.getLogger(__name__)

# target -> (confirm message, DockerClient method name)
_PRUNE_SPECS: dict[str, tuple[str, str]] = {
    "containers": ("Remove all STOPPED containers?", "prune_containers"),
    "images": ("Remove all dangling (untagged) images?", "prune_images"),
    "volumes": (
        "Remove all unused anonymous volumes? Named volumes are kept: to remove "
        "the orphaned named ones, press P on the ORPHANED row of the Volumes tab.",
        "prune_volumes",
    ),
    "networks": ("Remove all unused networks?", "prune_networks"),
    "system": (
        "System prune: removes stopped containers, unused networks, "
        "dangling images, and build cache where supported. Continue?",
        "prune_system",
    ),
}


class PruneHandler(_Base):
    """`docker system prune`-family cleanup: a target picker, then a confirm
    dialog, before anything is actually removed — see `PruneScreen`."""

    def _orphan_group_in_focus(self) -> RowGroup | None:
        """The ORPHANED group when `P` means "prune the orphans": the cursor is
        on its header or on one of its volumes (the `safe to delete` card)."""
        if self.query_one(TabbedContent).active != TabID.VOLUMES:
            return None
        item = self._get_focused_resource(TabID.VOLUMES)
        if isinstance(item, RowGroup):
            return item if item.key == ORPHANED_KEY else None
        if isinstance(item, Volume) and not item.used_by:
            return next(
                (
                    row
                    for row in self._tables.rows.get(TabID.VOLUMES, [])
                    if isinstance(row, RowGroup) and row.key == ORPHANED_KEY
                ),
                None,
            )
        return None

    async def _prune_orphans(self, group: RowGroup) -> None:
        """Delete exactly the volumes the group lists, one by one (not
        `volumes.prune()`, which skips named volumes): confirm with the count
        and size first, unless `confirm_prune` is off."""
        plan = plan_orphan_prune(
            self.docker, list(group.items), self._details.volume_sizes
        )
        if isinstance(plan, Refusal):
            self.notify(plan.message, severity="warning")
            return
        if self.config.confirm_prune:
            confirmed = await self.push_screen_wait(ConfirmDialog(plan.confirm_message))
            if not confirmed:
                logger.debug("Orphan prune cancelled by user")
                return
        self._run_bulk(TabID.VOLUMES, "Pruned", plan.jobs)

    @work
    async def action_prune(self) -> None:
        orphans = self._orphan_group_in_focus()
        if orphans is not None:
            await self._prune_orphans(orphans)
            return
        target = await self.push_screen_wait(PruneScreen())
        if target is None:
            logger.debug("Prune cancelled — no target chosen")
            return

        confirm_message, method_name = _PRUNE_SPECS[target]
        if self.config.confirm_prune:
            confirmed = await self.push_screen_wait(ConfirmDialog(confirm_message))
            if not confirmed:
                logger.debug("Prune cancelled by user")
                return

        self.notify("Pruning…")
        self._execute_prune(method_name)

    @work(thread=True)
    def _execute_prune(self, method_name: str) -> None:
        method: Callable[[], CommandResult] = getattr(self.docker, method_name)
        result = method()
        self.call_from_thread(self._handle_prune_result, result)

    def _handle_prune_result(self, result: CommandResult) -> None:
        if result.ok:
            logger.info("%s", result.message)
            self.notify(result.message)
            self.start_refresh()
        else:
            logger.warning("Prune failed: %s", result.message)
            self.notify(f"Error: {result.message}", severity="error")
