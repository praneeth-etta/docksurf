"""
chrome.py — repaints the app frame (top bar, bottom bar, list panel title,
filter placeholder) from current state. ChromeController is a mixin composed
into DockSurfApp; the text itself comes from `core.render.chrome`.
"""

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import TabbedContent

from docksurf_py.core.models import ComposeProject, Container, Image, Network, Volume
from docksurf_py.core.render.chrome import (
    GLOBAL_HINTS,
    HintContext,
    RowKind,
    bottom_bar,
    chip_bar,
    global_counts,
    key_hints,
    list_title,
    tab_summaries,
    top_bar,
)
from docksurf_py.core.render.images import images_footer
from docksurf_py.core.render.observability import images_disk_line
from docksurf_py.core.state.status import RunState, classify
from docksurf_py.core.state.view import LayoutMode
from docksurf_py.core.types import SafeMarkup, TabID
from docksurf_py.tui.ids import (
    BOTTOM_BAR_ID,
    CHIP_BAR_ID,
    FILTER_PLACEHOLDERS,
    LIST_PANEL_ID,
    SEARCH_BAR_ID,
    TOOLBAR_LINE_ID,
    TOP_BAR_ID,
)
from docksurf_py.tui.widgets import BottomBar, ChipBar, SearchBar, ToolbarLine, TopBar

if TYPE_CHECKING:
    from docksurf_py.tui.app import AppContext

    _Base = AppContext
else:
    # Real runtime base is `object` — see app.py's `AppContext` docstring.
    _Base = object


class ChromeController(_Base):
    """Keeps the frame in step with the snapshot, cursor, tab, marks,
    connection and theme. Every method is cheap and safe to call redundantly."""

    def _repaint_chrome(self) -> None:
        """Repaint everything the frame shows. No-ops while the app is not
        fully mounted or is shutting down (a queued event can outlive the
        widgets)."""
        if not self.is_running:
            return
        self._repaint_top_bar()
        self._repaint_bottom_bar()
        self._repaint_list_panel()

    def action_toggle_side(self) -> None:
        """`z`: hide or show the side column, so the list takes the full width.
        The streams that only the side column shows stop while it is hidden."""
        if self._view.logs_full:
            return  # full-view logs fill the screen; `l` or Esc leaves them
        self._view.toggle_side()
        self._sync_log_layout()
        self._retarget_side()
        self._repaint_chrome()

    def _repaint_top_bar(self) -> None:
        if not self.is_running:
            return
        active = TabID(self.query_one(TabbedContent).active)
        tabs = tab_summaries(self.snapshot)
        view = self._view
        theme_label = self.theme.removeprefix("docksurf-")
        context = self.docker.connection.context if self.docker else ""
        compact = view.layout is LayoutMode.COMPACT
        palette = view.palette
        self.query_one(f"#{TOP_BAR_ID}", TopBar).set_builder(
            lambda width: top_bar(
                tabs,
                active,
                context,
                view.connection,
                theme_label,
                palette,
                compact=compact,
                width=width,
            )
        )

    def _repaint_bottom_bar(self) -> None:
        if not self.is_running:
            return
        ctx = self._hint_context()
        hints = key_hints(ctx)
        counts = global_counts(self.snapshot)
        view = self._view
        message = view.connection_message if view.connection == "offline" else ""
        palette = view.palette
        self.query_one(f"#{BOTTOM_BAR_ID}", BottomBar).set_builder(
            lambda width: bottom_bar(
                hints,
                counts,
                palette,
                width=width,
                message=message,
                global_hints=() if ctx.filtering else GLOBAL_HINTS,
            )
        )

    def _repaint_list_panel(self) -> None:
        """Panel title (`containers · 15 · grouped by project`) and the filter
        placeholder for the active tab."""
        if not self.is_running:
            return
        active = TabID(self.query_one(TabbedContent).active)
        entry = self._resource_registry[active]
        panel = self.query_one(f"#{LIST_PANEL_ID}")
        rows = self._tables.rows.get(active, [])
        shown = sum(1 for row in rows if not isinstance(row, ComposeProject))
        total = (
            len(entry.kind.snapshot_items(self.snapshot))
            if self.snapshot is not None
            else shown
        )
        panel.border_title = list_title(
            active,
            shown,
            self._tables.sort(active),
            grouped=entry.kind.groups_by_project,
            total=total,
        )
        search_bar = self.query_one(f"#{SEARCH_BAR_ID}", SearchBar)
        search_bar.placeholder = FILTER_PLACEHOLDERS[active]
        self._repaint_chip_bar(active, search_bar.value)
        self._repaint_toolbar_line()
        self._repaint_footer()

    def _repaint_footer(self) -> None:
        """The list panel's bottom line (marked summary, notes), if the active
        tab has one."""
        if not self.is_running:
            return
        active = TabID(self.query_one(TabbedContent).active)
        build = self._resource_registry[active].footer
        panel = self.query_one(f"#{LIST_PANEL_ID}")
        if build is None:
            panel.border_subtitle = ""
            return
        marked = self._marked_items(active)
        panel.border_subtitle = Text.from_markup(build(marked))

    def _images_footer(self, marked: list[Image]) -> SafeMarkup:
        return images_footer(marked, self._view.palette)

    def _repaint_toolbar_line(self) -> None:
        """The active tab's summary line above the filter, hidden if it has none."""
        if not self.is_running:
            return
        active = TabID(self.query_one(TabbedContent).active)
        build = self._resource_registry[active].toolbar_line
        line = self.query_one(f"#{TOOLBAR_LINE_ID}", ToolbarLine)
        line.display = build is not None and self.snapshot is not None
        if build is not None:
            line.set_builder(build)

    def _images_toolbar_line(self, width: int) -> SafeMarkup:
        images = self.snapshot.images if self.snapshot is not None else []
        return images_disk_line(
            self._details.system_df, images, width, self._view.palette
        )

    def _repaint_chip_bar(self, active: TabID, query: str) -> None:
        chips = self._resource_registry[active].kind.chips
        markup = ""
        if chips and self.snapshot is not None:
            counts = self._tables.chip_counts(active, self.snapshot, query)
            markup = chip_bar(
                chips, counts, self._tables.active_chip[active], self._view.palette
            )
        self.query_one(f"#{CHIP_BAR_ID}", ChipBar).set_markup(markup)

    def _hint_context(self) -> HintContext:
        active = TabID(self.query_one(TabbedContent).active)
        item = self._get_focused_resource(active)
        row: RowKind = "none"
        compose = False
        if isinstance(item, ComposeProject):
            row = "project"
        elif isinstance(item, Container):
            running = classify(item) not in (RunState.STOPPED, RunState.FAILED)
            row = "container_running" if running else "container_stopped"
            compose = item.is_compose
        elif isinstance(item, Image):
            row = "image"
        elif isinstance(item, Volume):
            row = "volume"
        elif isinstance(item, Network):
            row = "network"
        return HintContext(
            tab=active,
            row=row,
            logs_full=self._view.logs_full,
            has_marks=self._tables.has_marks(active),
            compose=compose,
            filtering=self.query_one(f"#{SEARCH_BAR_ID}", SearchBar).has_focus,
        )
