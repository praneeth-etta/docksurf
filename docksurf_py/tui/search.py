"""
search.py — Live filter bar mixin.

ResourceSearchController is a mixin class that composes into DockSurfApp.
"""

from typing import TYPE_CHECKING

from textual.timer import Timer
from textual.widgets import DataTable, Input, TabbedContent

from docksurf_py.core.types import TabID
from docksurf_py.tui.ids import SEARCH_BAR_ID

if TYPE_CHECKING:
    from docksurf_py.tui.app import AppContext

    _Base = AppContext
else:
    # Real runtime base is `object` — `AppContext` only exists for mypy to
    # check these mixins' bodies against; see app.py's `AppContext` docstring.
    _Base = object


class ResourceSearchController(_Base):
    """Focuses, leaves, and applies the inline filter across all resource tabs.

    `on_search_changed`/`on_search_escape` are plain methods, not `@on`-
    decorated, even though they look like Textual message handlers: this
    class runs with a plain `object` base at runtime, and `@on` only wires
    into Textual's dispatch table for methods defined on a real message-pump
    class. The real dispatch entry points are
    `DockSurfApp._on_search_input_changed`/`_on_search_input_submitted` in
    app.py, which just forward here.
    """

    _search_filter_timer: Timer | None = None

    def action_open_search(self) -> None:
        self.query_one(f"#{SEARCH_BAR_ID}", Input).focus()

    def action_cycle_chip(self) -> None:
        """`F`: next status chip on the active tab (wraps back to `all`)."""
        active = TabID(self.query_one(TabbedContent).active)
        if not self._resource_registry[active].kind.chips:
            return
        self._tables.cycle_chip(active)
        self._rerender_active_table()

    def action_set_chip(self, key: str) -> None:
        """Click on a chip in the chip bar."""
        active = TabID(self.query_one(TabbedContent).active)
        if self._tables.set_chip(active, key):
            self._rerender_active_table()

    def on_search_changed(self, event: Input.Changed) -> None:
        # Debounced like LogPane's filter (log_pane.py): otherwise each keystroke
        # re-populates the active tab's table.
        query = event.value
        if self._search_filter_timer is not None:
            self._search_filter_timer.stop()

        def apply() -> None:
            self._search_filter_timer = None
            self._apply_filter(query)

        self._search_filter_timer = self.set_timer(0.2, apply)

    def on_search_escape(self) -> None:
        """Enter/Escape in the filter: hand focus back to the table. The query
        stays — it keeps filtering and stays visible in the toolbar."""
        self._flush_pending_filter()
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is not None:
            self.query_one(f"#{entry.table_id}", DataTable).focus()

    def _flush_pending_filter(self) -> None:
        """Apply the query now if a debounced filter is still pending."""
        if self._search_filter_timer is None:
            return
        self._search_filter_timer.stop()
        self._search_filter_timer = None
        self._apply_filter(self.query_one(f"#{SEARCH_BAR_ID}", Input).value)

    def _apply_filter(self, query: str) -> None:
        if not self.snapshot:
            return

        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None:
            return

        rows = self._tables.build_rows(active, self.snapshot, query)
        table = self.query_one(f"#{entry.table_id}", DataTable)
        table.clear(columns=False)
        entry.populate(table, rows)
        self._update_empty_state(active, entry, rows, query)
        self._repaint_list_panel()
