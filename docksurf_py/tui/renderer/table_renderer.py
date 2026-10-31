"""TableRenderer — column setup and row-population for the four resource tables."""

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.style import Style
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Static, TabbedContent

from docksurf_py.core.models import (
    ComposeProject,
    Container,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.render.containers import (
    chevron,
    container_row_cells,
    project_row_cells,
)
from docksurf_py.core.render.images import image_row_cells
from docksurf_py.core.render.markup import (
    network_containers_markup,
)
from docksurf_py.core.render.palette import fg
from docksurf_py.core.render.volumes import volume_group_cells, volume_row_cells
from docksurf_py.core.session import save_session
from docksurf_py.core.state.tables import ALL_CHIP, RowGroup, TablesState, row_key
from docksurf_py.core.types import (
    SafeMarkup,
    TabID,
)
from docksurf_py.tui.ids import (
    EMPTY_STATE_HINTS,
    EMPTY_STATE_IDS,
    MARK_GLYPH,
    SEARCH_BAR_ID,
)
from docksurf_py.tui.renderer.common import _Base
from docksurf_py.tui.widgets.tables import ResourceTable

if TYPE_CHECKING:
    from docksurf_py.tui.app import ResourceEntry

# Max chars shown in an image repository / volume name cell.
NAME_MAX_CHARS = 30


def _safe_row(table: DataTable, *values) -> None:
    """Add a row, escaping plain strings and passing SafeMarkup through unchanged."""
    table.add_row(*(v if isinstance(v, SafeMarkup) else escape(str(v)) for v in values))


class TableRenderer(_Base):
    """Knows how to initialise columns and populate rows for every resource table.

    Column layout lives on each tab's `ResourceEntry` in `self._resource_registry`
    (built by `DockSurfApp`), not here — this class just drives it.
    """

    def setup_tables(self) -> None:
        # Rows, marks, sort, collapse and dirty flags for every tab live in
        # core; this mixin only turns rows into cells. Sort is restored from
        # the previous session; unknown columns are ignored.
        self._tables = TablesState(
            {tab_id: entry.kind for tab_id, entry in self._resource_registry.items()},
            sort_state={
                tab_id: self._session.sort_state.get(tab_id.value)
                for tab_id in self._resource_registry
            },
        )

        for tab_id, entry in self._resource_registry.items():
            table = self.query_one(f"#{entry.table_id}", DataTable)
            self._add_table_columns(table, tab_id, entry)
            table.cursor_type = "row"

    def _add_table_columns(
        self, table: DataTable, tab_id: TabID, entry: "ResourceEntry"
    ) -> None:
        """(Re)build a table's columns, marking the active sort column.

        Called at initial setup and again whenever the sort column/direction
        changes (`_on_header_selected`), since Textual has no API to relabel
        an existing column in place.
        """
        # Leading mark column, added separately (not part of ResourceEntry.
        # columns) so every populate method just prepends one cell per row.
        table.add_column("", width=2)
        sort_state = self._tables.sort(tab_id)
        for col in entry.columns:
            label = col
            if sort_state is not None and sort_state[0] == col:
                label = f"{col} {'▼' if sort_state[1] else '▲'}"
            width = entry.column_widths.get(col)
            if col in entry.right_columns and width:
                label = label.rjust(width)
            table.add_column(label, width=width)

    def _on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Handle a header click and toggle that column's sort.

        Not `@on`-decorated here: this class is a plain mixin (`_Base` is
        `object` at runtime — see the module docstring), so Textual's
        `_MessagePumpMeta` never processes it and any `@on` decorator on a
        mixin method is silently inert. The real dispatch entry point is
        `DockSurfApp._on_data_table_header_selected` in app.py, which is a
        real method on the App class and just forwards here.
        """
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None or event.data_table.id != entry.table_id:
            return
        # Column 0 is the leading mark-glyph column — not sortable.
        if event.column_index == 0:
            return
        col_name = entry.columns[event.column_index - 1]
        new_sort = self._tables.cycle_sort(active, col_name)
        if new_sort is None:
            return
        if self._persist_session:
            self._session.sort_state[active.value] = new_sort
            save_session(self._session)

        table = event.data_table
        table.clear(columns=True)
        self._add_table_columns(table, active, entry)
        self._rerender_active_table()

    def _populate_container_table(
        self, table: DataTable, rows: list[ComposeProject | Container]
    ) -> None:
        """Write container rows: a Compose project header, its service rows
        (unless collapsed), and standalone containers, in `rows` order."""
        p = self._view.palette
        project: ComposeProject | None = None
        for row in rows:
            if isinstance(row, ComposeProject):
                project = row
                cells = project_row_cells(row, p)
            else:
                branch = None
                if row.is_compose and project is not None:
                    branch = "└─" if row is project.containers[-1] else "├─"
                cells = container_row_cells(row, branch=branch, p=p)
            _safe_row(table, self._gutter_cell(TabID.CONTAINERS, row, False), *cells)
        self._finish_populate(TabID.CONTAINERS, table, rows)

    def _rerender_active_table(self) -> None:
        """Re-populate the active tab's table honouring any active search filter.

        `_apply_filter` (with the current query, empty when the bar is closed)
        clears and repopulates whichever tab is active — used after a mark-clear
        or a bulk action, where several rows' mark cells changed at once.

        `DataTable.clear()` resets cursor to row 0, focused row's identity is
        captured first and restored afterwards.
        """
        active = self.query_one(TabbedContent).active
        focus_key = self._focused_row_key(active)
        search_bar = self.query_one(f"#{SEARCH_BAR_ID}", Input)
        self._apply_filter(search_bar.value)
        self._repaint_chrome()
        self._restore_selection(active, focus_key)

    def _rerender_containers(self) -> None:
        """Re-run the container populate honouring any active search filter.

        Used after a group is collapsed/expanded.
        """
        self._rerender_active_table()

    def _populate_image_table(self, table: DataTable, rows: list[Image]) -> None:
        for i in rows:
            _safe_row(
                table,
                self._gutter_cell(TabID.IMAGES, i, False),
                *image_row_cells(i, self._view.palette),
            )
        self._finish_populate(TabID.IMAGES, table, rows)

    def _populate_volume_table(
        self, table: DataTable, rows: list[RowGroup | Volume]
    ) -> None:
        """Write volume rows under their `IN USE` / `ORPHANED` group headers.
        Sizes are computed on demand only (`b`), never on the snapshot path:
        too slow to fetch on every refresh (see `DockerClient.volume_sizes`)."""
        p = self._view.palette
        for row in rows:
            if isinstance(row, RowGroup):
                cells = volume_group_cells(row, self._details.volume_sizes, p)
            else:
                cells = volume_row_cells(row, self._details.volume_size(row.name), p)
            _safe_row(table, self._gutter_cell(TabID.VOLUMES, row, False), *cells)
        self._finish_populate(TabID.VOLUMES, table, rows)

    def _populate_network_table(self, table: DataTable, rows: list[Network]) -> None:
        containers = self.snapshot.containers if self.snapshot else []
        for n in rows:
            _safe_row(
                table,
                self._mark_cell(TabID.NETWORKS, n),
                n.name,
                n.driver,
                n.scope,
                network_containers_markup(n, containers),
            )
        self._finish_populate(TabID.NETWORKS, table, rows)

    def _mark_cell(self, tab_id: TabID, item: object) -> SafeMarkup | str:
        """Leading-column cell for a row: the mark glyph if marked, else blank."""
        return MARK_GLYPH if self._tables.is_marked(tab_id, row_key(item)) else ""

    def _gutter_cell(
        self, tab_id: TabID, item: object, is_cursor: bool
    ) -> SafeMarkup | str:
        """Leading 2-column cell: a group header's chevron, `▌` under the
        cursor, `✓` when the cursor row is marked, else the mark glyph. Only
        tabs whose entry sets `cursor_gutter` draw the cursor."""
        p = self._view.palette
        if isinstance(item, ComposeProject):
            return chevron(self._tables.is_collapsed(item.name), p)
        if isinstance(item, RowGroup):
            collapsed = self._tables.is_group_collapsed(item.tab, item.key)
            return chevron(collapsed, p, item.role)
        marked = self._tables.is_marked(tab_id, row_key(item))
        if is_cursor and self._resource_registry[tab_id].cursor_gutter:
            if marked:
                return SafeMarkup(f"[bold {p.primary}]✓[/]")
            return fg(p, "primary", "▌")
        return MARK_GLYPH if marked else ""

    def _row_tint(self, tab_id: TabID, item: object) -> Style | None:
        """Background for a header (`surface`) or marked (`marked_bg`) row;
        None on terminal-native themes, which have no colours to tint with."""
        p = self._view.palette
        if isinstance(item, (ComposeProject, RowGroup)):
            colour = p.surface
        elif self._tables.is_marked(tab_id, row_key(item)):
            colour = p.marked_bg
        else:
            return None
        return None if colour == "default" else Style(bgcolor=colour)

    def _finish_populate(self, tab_id: TabID, table: DataTable, rows: list) -> None:
        """After a populate: row tints, and the cursor gutter on the row the
        (freshly cleared) table's cursor sits on."""
        if not isinstance(table, ResourceTable):
            return
        for index, item in enumerate(rows):
            tint = self._row_tint(tab_id, item)
            if tint is not None:
                table.row_tints[index] = tint
        if rows and self._resource_registry[tab_id].cursor_gutter:
            self._paint_gutter(table, tab_id, None, table.cursor_row)

    def _paint_gutter(
        self, table: DataTable, tab_id: TabID, old: int | None, new: int
    ) -> None:
        """Move the `▌` from `old` to `new` with two cell updates (no repopulate)."""
        rows = self._tables.rows.get(tab_id, [])
        if not isinstance(table, ResourceTable):
            return
        if not self._resource_registry[tab_id].cursor_gutter:
            return
        if old is None:
            old = table.gutter_row
        if old is not None and old != new and old < min(len(rows), table.row_count):
            table.update_cell_at(
                Coordinate(old, 0), self._gutter_cell(tab_id, rows[old], False)
            )
        if new < min(len(rows), table.row_count):
            table.update_cell_at(
                Coordinate(new, 0), self._gutter_cell(tab_id, rows[new], True)
            )
            table.gutter_row = new

    def _retint_row(self, table: DataTable, tab_id: TabID, row: int) -> None:
        rows = self._tables.rows.get(tab_id, [])
        if isinstance(table, ResourceTable) and row < len(rows):
            table.set_row_tint(row, self._row_tint(tab_id, rows[row]))

    def _update_empty_state(
        self,
        tab_id: TabID,
        entry: "ResourceEntry",
        items: list,
        query: str = "",
    ) -> None:
        """Swap the table for a placeholder message when `items` is empty.

        Distinguishes three causes so the message actually helps: an active
        search with no matches, a disconnected daemon (reusing the same
        classified `ConnectionState` the bottom bar shows), or genuinely zero
        resources of that type.
        """
        table = self.query_one(f"#{entry.table_id}", DataTable)
        empty = self.query_one(f"#{EMPTY_STATE_IDS[tab_id]}", Static)
        if items:
            table.display = True
            empty.display = False
            return
        table.display = False
        empty.display = True
        chip = self._tables.active_chip[tab_id]
        if query:
            message = f"No {entry.label}s match {query!r}"
            if chip != ALL_CHIP:
                message += f" ({self._tables.chip_label(tab_id)})"
        elif chip != ALL_CHIP:
            message = f"No {self._tables.chip_label(tab_id)} {entry.label}s"
        elif not self.docker.is_connected:
            state = self.docker.connection
            message = state.message
            if state.hint:
                message += f"\n{state.hint}"
        else:
            message = EMPTY_STATE_HINTS.get(tab_id, f"No {entry.label}s found")
        empty.update(escape(message))
