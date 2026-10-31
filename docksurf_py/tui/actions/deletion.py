"""ResourceDeletionHandler — confirmation dialogs and dispatched remove calls."""

import logging
from typing import Any, Callable

from rich.markup import escape
from textual import work
from textual.widgets import TabbedContent

from docksurf_py.core.models import (
    CommandErrorKind,
    CommandResult,
    ComposeProject,
)
from docksurf_py.core.state.ops import (
    BulkJob,
    DeletePlan,
    Refusal,
    bind_delete_command,
)
from docksurf_py.core.state.tables import RowGroup, row_key
from docksurf_py.core.types import TabID
from docksurf_py.tui.actions.common import _Base, _display_name
from docksurf_py.tui.widgets import ConfirmDialog

logger = logging.getLogger(__name__)


class ResourceDeletionHandler(_Base):
    """Confirmation dialogs and dispatched remove calls for all resource types.

    Per-resource behavior (the confirm message, force-flag logic, and any
    pre-condition guard) is decided in core (`core/state/ops.py`'s
    `plan_*_delete`, wired into each tab's `ResourceKind`) — this mixin only
    shows the dialog or warning and runs the result.
    """

    def _apply_if_confirmed(
        self,
        confirmed: bool,
        force: bool,
        command_fn: Callable[[bool], CommandResult],
        success_msg: str,
    ) -> None:
        if not confirmed:
            logger.debug("Deletion cancelled by user")
            return
        result = command_fn(force)
        if result.ok:
            logger.info("%s", success_msg)
            self.notify(success_msg)
            self.start_refresh()
        else:
            logger.warning("Delete failed: %s", result.message)
            self.notify(f"Error: {result.message}", severity="error")
            if result.kind is CommandErrorKind.NOT_FOUND:
                # Our snapshot is stale — the resource is already gone.
                self.start_refresh()

    def _plan_delete(self, tab_id: TabID, item: Any) -> DeletePlan | None:
        """Core's delete plan for `item`, or None — after warning with the
        reason when core refuses (in-use volume, built-in network)."""
        planner = self._resource_registry[tab_id].kind.plan_delete
        if planner is None:
            return None
        plan = planner(self.docker, item)
        if isinstance(plan, Refusal):
            self.notify(plan.message, severity="warning")
            return None
        return plan

    @work
    async def action_delete(self) -> None:
        if not self.snapshot:
            return
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None:
            return

        if self._tables.has_marks(active):
            await self._bulk_delete(active)
            return

        item = self._get_focused_resource(active)
        if item is None:
            self.notify(f"No {entry.label} selected", severity="warning")
            return

        if isinstance(item, ComposeProject):
            # `d` on a Compose project header has no single container to
            # delete, route to the project-wide teardown instead (already
            # confirms via ConfirmDialog when Config.confirm_compose_down).
            self.action_compose_down()
            return

        if isinstance(item, RowGroup):
            self.notify(f"Select a {entry.label}, or mark several", severity="warning")
            return

        plan = self._plan_delete(active, item)
        if plan is None:
            return

        if not self.config.confirm_delete:
            confirmed = True
            force = plan.force_default if plan.force_default is not None else False
        elif plan.force_default is None:
            confirmed = await self.push_screen_wait(ConfirmDialog(plan.confirm_message))
            force = False
        else:
            confirmed, force = await self.push_screen_wait(
                ConfirmDialog(plan.confirm_message, force_default=plan.force_default)
            )
        self._apply_if_confirmed(confirmed, force, plan.command, plan.success_message)

    async def _bulk_delete(self, tab_id: TabID) -> None:
        """Delete every marked resource on `tab_id` behind one confirm dialog.

        Reuses each item's existing `plan_delete` for its confirm wording/
        force-flag logic; items whose plan is `None` (in-use volume, built-in
        network) keep their guard-notify from `plan_delete` and are silently
        excluded from the batch.
        """
        entry = self._resource_registry[tab_id]
        jobs: list[BulkJob] = []
        names: list[str] = []
        for item in self._marked_items(tab_id):
            key = row_key(item)
            plan = self._plan_delete(tab_id, item)
            if key is None or plan is None:
                continue
            name = _display_name(item)
            force = plan.force_default if plan.force_default is not None else False
            jobs.append((key, name, bind_delete_command(plan, force)))
            names.append(name)

        if not jobs:
            self.notify(
                f"No marked {entry.label}s eligible to delete", severity="warning"
            )
            self._tables.clear_marks(tab_id)
            self._rerender_active_table()
            return

        if self.config.confirm_delete:
            preview = ", ".join(escape(n) for n in names[:8])
            if len(names) > 8:
                preview += f", and {len(names) - 8} more"
            confirmed = await self.push_screen_wait(
                ConfirmDialog(f"Delete {len(names)} {entry.label}(s)? {preview}")
            )
            if not confirmed:
                logger.debug("Bulk delete cancelled by user")
                return
        self._run_bulk(tab_id, "Deleted", jobs)
