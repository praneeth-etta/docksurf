"""SnapshotManager — background snapshot fetch and event-driven auto-refresh."""

import logging

from rich.markup import escape
from textual import work
from textual.timer import Timer
from textual.widgets import DataTable, Input, LoadingIndicator, Static, TabbedContent

from docksurf_py.core.connection import ConnectionState
from docksurf_py.core.models import (
    DockerSnapshot,
)
from docksurf_py.core.state.refresh import EventWatcher
from docksurf_py.core.state.tables import row_key
from docksurf_py.core.types import TabID
from docksurf_py.tui.ids import (
    CONNECTION_BANNER_ID,
    DETAIL_PANE_ID,
    REFRESH_LOADING_ID,
    SEARCH_BAR_ID,
)
from docksurf_py.tui.renderer.common import _Base
from docksurf_py.tui.widgets import DetailPane

logger = logging.getLogger(__name__)


class SnapshotManager(_Base):
    """Fetches Docker state in a background thread and commits it to the UI."""

    # Refresh bookkeeping (`self._refresh`, a core `RefreshCoordinator`) is
    # created by `DockSurfApp.__init__`; this mixin owns the threads/timers.
    _event_watcher: EventWatcher | None = None
    _refresh_debounce: Timer | None = None

    def start_refresh(self) -> None:
        if not self._refresh.begin():
            logger.debug("Refresh skipped — already in progress")
            return
        logger.info("Refresh started")
        self.query_one(f"#{REFRESH_LOADING_ID}", LoadingIndicator).display = True
        self.populate_tables()

    @work(thread=True)
    def populate_tables(self) -> None:
        try:
            snapshot = self.docker.fetch_snapshot()
        except Exception as exc:
            logger.exception("Snapshot fetch failed")
            self.call_from_thread(self._finish_refresh, None, str(exc))
        else:
            logger.info(
                "Snapshot fetched — containers=%d images=%d volumes=%d networks=%d",
                len(snapshot.containers),
                len(snapshot.images),
                len(snapshot.volumes),
                len(snapshot.networks),
            )
            self.call_from_thread(self._finish_refresh, snapshot, None)

    def _apply_snapshot(self, snapshot: DockerSnapshot) -> None:
        self.snapshot: DockerSnapshot | None = snapshot

        # A fetch worker can complete while the app is tearing down (e.g. the
        # user quit mid-refresh); the widgets are gone, so committing would
        # raise NoMatches. Skip — there's nothing left to paint.
        if not self.is_running:
            return

        if not self.docker.is_connected:
            state = self.docker.connection
            logger.error(
                "Docker unavailable — status=%s context=%s host=%s",
                state.status.value,
                state.context,
                state.host,
            )
        self._maybe_notify_connection_change(self.docker.connection)

        # Remember what the user had selected so a background refresh doesn't
        # yank the cursor back to the top (and needlessly restart the stats
        # stream) on every `docker events` tick.
        active = self.query_one(TabbedContent).active
        focus_key = self._focused_row_key(active)

        # Drop marks on resources that no longer exist, and flag the inactive
        # tabs dirty: only the active tab's table is visible, so only it is
        # repopulated now — the others are caught up lazily by `_populate_tab`
        # the moment they become active (`DockSurfApp.clear_on_tab_switch`).
        # Cuts DataTable rebuild work ~4x per event-driven refresh (P-5).
        self._tables.apply_snapshot(snapshot, active)

        search_bar = self.query_one(f"#{SEARCH_BAR_ID}", Input)
        filter_active = bool(search_bar.value)
        if filter_active:
            # _apply_filter (below) populates the active tab itself — doing
            # it here too would repaint it twice.
            self._tables.mark_clean(active)
        else:
            self._populate_tab(active)

        if filter_active:
            self._apply_filter(search_bar.value)

        # Restore selection last — after any filter re-populate — so it wins.
        self._restore_selection(active, focus_key)
        self._repaint_chrome()

    def _populate_tab(self, tab_id: TabID) -> None:
        """(Re)populate a tab's table from the current snapshot and clear its dirty
        flag.

        Used when refreshing the active tab and when a dirty tab becomes active.
        """
        if self.snapshot is None:
            return
        entry = self._resource_registry[tab_id]
        table = self.query_one(f"#{entry.table_id}", DataTable)
        table.clear(columns=False)
        rows = self._tables.build_rows(tab_id, self.snapshot)
        entry.populate(table, rows)
        self._update_empty_state(tab_id, entry, rows)
        self._tables.mark_clean(tab_id)
        self._repaint_list_panel()

    def _focused_row_key(self, tab_id: TabID) -> tuple[str, str] | None:
        item = self._get_focused_resource(tab_id)
        return row_key(item) if item is not None else None

    def _restore_selection(
        self, tab_id: TabID, focus_key: tuple[str, str] | None
    ) -> None:
        """Re-select the row matching `focus_key`, or fall back to the first row."""
        entry = self._resource_registry.get(tab_id)
        if focus_key is None or entry is None:
            self._auto_select_first()
            return
        idx = self._tables.index_of(tab_id, focus_key)
        if idx is None:
            # The previously-focused resource is gone — select the first row.
            self._auto_select_first()
            return
        table = self.query_one(f"#{entry.table_id}", DataTable)
        # `RowHighlighted` only fires when the cursor's coordinate actually
        # changes, so only drive the detail pane / live panes here when the
        # cursor is not about to move, otherwise the event handler
        # (`DockSurfApp.update_details`) does it, and doing both
        # renders everything twice.
        cursor_will_move = table.cursor_row != idx
        table.move_cursor(row=idx)
        if not cursor_will_move:
            pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
            try:
                entry.show_details(pane, idx)
            except IndexError:
                pane.clear_details()
            self._retarget_side()

    def _maybe_notify_connection_change(self, state: ConnectionState) -> None:
        """UI-thread only — call via `call_from_thread` from a worker.

        No-ops unless `state.status` actually changed since the last call, so
        a poll loop retrying every couple seconds doesn't spam a toast on
        every attempt. Feeds the top and bottom bars' connection state either way
        the first time, then only a real transition after that.
        """
        change = self._refresh.connection_changed(state)
        if change is None:
            return
        connected = change.connected
        self._view.connection = "live" if connected else "offline"
        self._view.connection_message = "" if connected else state.message
        self._repaint_top_bar()
        self._repaint_bottom_bar()
        banner = self.query_one(f"#{CONNECTION_BANNER_ID}", Static)
        if connected:
            banner.display = False
            if change.reconnected:
                self.notify("Reconnected to Docker")
        else:
            banner_text = state.message
            if state.hint:
                banner_text += f"  —  {state.hint}"
            banner.update(escape(banner_text))
            banner.display = True
            self.notify(f"{state.message}\n{state.hint}", severity="error", timeout=12)

    def _finish_refresh(
        self, snapshot: DockerSnapshot | None, error: str | None
    ) -> None:
        try:
            if snapshot is not None:
                self._apply_snapshot(snapshot)
                logger.info("Refresh complete")
                failed = self.docker.last_fetch_errors
                if failed:
                    logger.warning("Partial fetch failure: %s", ", ".join(failed))
                    self.notify(
                        f"Could not refresh {', '.join(failed)} — "
                        "showing last known state",
                        severity="warning",
                    )
            elif error:
                logger.warning("Refresh failed: %s", error)
                self.notify(f"Refresh failed: {error}", severity="error")
        finally:
            rerun = self._refresh.finish()
            self.query_one(f"#{REFRESH_LOADING_ID}", LoadingIndicator).display = False
            # An event arrived mid-refresh — run once more so the final state lands.
            if rerun:
                self.start_refresh()

    # --- Event-driven auto-refresh ---

    @work(thread=True)
    def start_event_listener(self) -> None:
        """Watch `docker events` and keep the UI in sync.

        Runs core's `EventWatcher` on this background thread; it also acts as
        the reconnect loop, retrying until the connection is restored and
        triggering an immediate refresh when it succeeds. Its callbacks hop
        to the UI thread here.
        """
        watcher = EventWatcher(
            self.docker,
            on_event=lambda: self.call_from_thread(self._on_docker_event),
            on_connection=lambda state: self.call_from_thread(
                self._maybe_notify_connection_change, state
            ),
            on_reconnected=lambda: self.call_from_thread(self.start_refresh),
        )
        self._event_watcher = watcher
        watcher.run()

    def _on_docker_event(self) -> None:
        # Cheap flag only: the df is refetched on the next Images/Volumes entry.
        self._details.mark_system_df_stale()
        # Coalesce bursts (e.g. `compose up` emits many events) into one refresh.
        if self._refresh_debounce is not None:
            self._refresh_debounce.stop()
        self._refresh_debounce = self.set_timer(0.4, self._debounced_refresh)

    def _debounced_refresh(self) -> None:
        self._refresh_debounce = None
        if self._refresh.events_settled():
            self.start_refresh()

    def stop_event_listener(self) -> None:
        if self._event_watcher is not None:
            self._event_watcher.stop()
