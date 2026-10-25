"""TableRenderer — column setup and row-population for the four resource tables."""

import os
from typing import TYPE_CHECKING

from rich.markup import escape
from textual.widgets import DataTable, Input, Static, TabbedContent

from docksurf_py.core.docker import format_size
from docksurf_py.core.models import (
    ComposeProject,
    Container,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.render.markup import (
    image_status_markup,
    network_containers_markup,
    project_status_color,
    status_color,
    status_dot,
    truncate,
)
from docksurf_py.core.session import save_session
from docksurf_py.core.state.tables import TablesState
from docksurf_py.core.types import (
    SafeMarkup,
    TabID,
    markup_green,
    markup_yellow,
)
from docksurf_py.tui.ids import (
    EMPTY_STATE_HINTS,
    EMPTY_STATE_IDS,
    MARK_GLYPH,
    SEARCH_BAR_ID,
)
from docksurf_py.tui.renderer.common import _Base

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

    # Temporary read-through aliases onto `self._tables`, kept so existing
    # tests that inspect the old attributes keep working unchanged while the
    # UI separation is in progress (local/UI_SEPARATION_PLAN.md, Phase 4
    # removes them). Each returns the live collection, not a copy.
    @property
    def _current(self) -> dict[TabID, list]:
        return self._tables.rows

    @property
    def _dirty_tabs(self) -> set[TabID]:
        return self._tables.dirty

    @property
    def _collapsed_projects(self) -> set[str]:
        return self._tables.collapsed_projects

    @property
    def _marked(self) -> dict[TabID, set[tuple[str, str]]]:
        return self._tables.marked

    @property
    def _sort_state(self) -> dict[TabID, tuple[str, bool] | None]:
        return self._tables.sort_state

    @property
    def _volume_sizes(self) -> dict[str, int]:
        return self._details.volume_sizes

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
            table.add_column(label, width=entry.column_widths.get(col))

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
        project: ComposeProject | None = None
        for row in rows:
            if isinstance(row, ComposeProject):
                project = row
                collapsed = self._tables.is_collapsed(row.name)
                glyph = "▸" if collapsed else "▾"
                config = (
                    os.path.basename(row.config_files.split(",")[0].strip())
                    if row.config_files
                    else ""
                )
                dot = status_dot(project_status_color(row))
                _safe_row(
                    table,
                    "",  # project headers can't be marked
                    SafeMarkup(f"[b]{glyph}[/b] {dot} [b]{escape(row.name)}[/b]"),
                    config,
                )
                continue
            mark = self._mark_cell(TabID.CONTAINERS, row)
            dot = status_dot(status_color(row))
            if row.is_compose and project is not None:
                last = row is project.containers[-1]
                branch = "└" if last else "├"
                name = SafeMarkup(
                    f"  {branch} {dot} {escape(row.compose_service or row.name)}"
                )
            else:
                name = SafeMarkup(f"{dot} {escape(row.name)}")
            _safe_row(table, mark, name, row.image_name)

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
        self._apply_filter(search_bar.value if search_bar.display else "")
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
                self._mark_cell(TabID.IMAGES, i),
                truncate(i.repository, NAME_MAX_CHARS),
                truncate(i.tag, 10),
                format_size(i.size_bytes),
                image_status_markup(i.used_by),
            )

    def _populate_volume_table(self, table: DataTable, rows: list[Volume]) -> None:
        for v in rows:
            status = markup_green("In Use") if v.used_by else markup_yellow("Orphaned")
            raw = truncate(v.name, NAME_MAX_CHARS, middle=True)
            size_bytes = self._details.volume_size(v.name)
            # Computed on-demand only (`b`) — never on the snapshot path, it's
            # too slow to fetch on every refresh (see `DockerClient.volume_sizes`).
            size = (
                format_size(size_bytes)
                if size_bytes is not None
                else SafeMarkup("[dim]—[/]")
            )
            _safe_row(table, self._mark_cell(TabID.VOLUMES, v), raw, size, status)

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

    def _mark_cell(self, tab_id: TabID, item: object) -> SafeMarkup | str:
        """Leading-column cell for a row: the mark glyph if marked, else blank."""
        return MARK_GLYPH if self._tables.is_marked(tab_id, self._row_key(item)) else ""

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
        classified `ConnectionState` the status bar shows), or genuinely zero
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
        if query:
            message = f"No {entry.label}s match {query!r}"
        elif not self.docker.is_connected:
            state = self.docker.connection
            message = state.message
            if state.hint:
                message += f"\n{state.hint}"
        else:
            message = EMPTY_STATE_HINTS.get(tab_id, f"No {entry.label}s found")
        empty.update(escape(message))
