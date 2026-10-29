"""SelectionHandler — multi-select marking and the shared bulk-execution machinery."""

import logging
from typing import Any

from textual import work
from textual.coordinate import Coordinate
from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.state.ops import BulkJob, BulkResult, run_bulk
from docksurf_py.core.state.tables import row_key
from docksurf_py.core.types import TabID
from docksurf_py.tui.actions.common import _Base

logger = logging.getLogger(__name__)


class SelectionHandler(_Base):
    """Multi-select marking and the shared bulk-execution machinery.

    Marks live in core (`TablesState.marked`, keyed by `row_key` tuples) so
    they survive refresh/filter/collapse — `TablesState.apply_snapshot` prunes
    vanished keys and every populate method re-renders the mark glyph on each
    repaint. `ContainerActionHandler`/`ResourceDeletionHandler` build the
    per-domain job lists (what to run, and its guard/plan logic); this mixin
    only knows how to toggle a mark and run a batch of jobs sequentially.
    """

    def action_toggle_mark(self) -> None:
        active = self.query_one(TabbedContent).active
        if self._resource_registry.get(active) is None:
            return
        # A project header has no mark of its own — space still collapses it.
        if self._focused_is_project_header():
            self.action_toggle_group()
            return
        item = self._get_focused_resource(active)
        if item is None:
            return
        key = row_key(item)
        if key is None:
            return

        table_id = self._resource_registry[active].table_id
        table = self.query_one(f"#{table_id}", DataTable)
        row = table.cursor_row
        if row is None:
            return
        self._tables.toggle_mark(active, key)
        table.update_cell_at(Coordinate(row, 0), self._gutter_cell(active, item, True))
        self._retint_row(table, active, row)
        self._repaint_bottom_bar()
        self._repaint_footer()

        # Advance the cursor — mark-and-move, k9s-style rapid selection.
        if row + 1 < table.row_count:
            table.move_cursor(row=row + 1)

    def action_clear_marks(self) -> None:
        # In full-view logs, Esc leaves the view before it clears any marks.
        if self._view.logs_full:
            self._exit_logs_full()
            return
        active = self.query_one(TabbedContent).active
        if not self._tables.clear_marks(active):
            return
        self._rerender_active_table()

    def _marked_items(self, tab_id: TabID) -> list[Any]:
        """Resolve a tab's marked keys back to live objects from the snapshot."""
        return self._tables.marked_items(tab_id, self.snapshot)

    def _run_bulk(
        self,
        tab_id: TabID,
        verb: str,
        jobs: list[BulkJob],
    ) -> None:
        """Protocol-facing entry point for `ContainerActionHandler`/
        `ResourceDeletionHandler` — dispatches to the threaded worker below.

        Kept separate from `_execute_bulk` because `@work` gives a decorated
        method a Textual-generated wrapper signature at runtime, which mypy
        rejects as an incompatible override of the same name declared in
        `AppContext`. This thin, undecorated method is what the Protocol
        declares instead.
        """
        self._execute_bulk(tab_id, verb, jobs)

    @work(thread=True)
    def _execute_bulk(
        self,
        tab_id: TabID,
        verb: str,
        jobs: list[BulkJob],
    ) -> None:
        """Run the jobs off the UI thread (core's `run_bulk`), then report."""
        result = run_bulk(jobs)
        self.call_from_thread(self._handle_bulk_result, tab_id, verb, result)

    def _handle_bulk_result(self, tab_id: TabID, verb: str, result: BulkResult) -> None:
        message, is_error = result.summary(verb)
        if is_error:
            logger.warning(
                "Bulk %s: %d/%d failed — %s",
                verb,
                len(result.failures),
                result.total,
                message,
            )
            self.notify(message, severity="error")
        else:
            logger.info("Bulk %s: %d/%d succeeded", verb, result.ok_count, result.total)
            self.notify(message)
        self._tables.unmark(tab_id, result.executed_keys)
        self.start_refresh()
