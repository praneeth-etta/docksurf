"""
app.py — The Application shell.

Assembles the seven mixin classes into DockSurfApp, defines layout and
key bindings, and wires the on_mount / action_refresh entry points.
"""

import argparse
import logging
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import (
    DataTable,
    LoadingIndicator,
    Static,
    TabbedContent,
    TabPane,
)
from textual.worker import Worker

from docksurf_py.core.config import DEFAULT_CONFIG_PATH, Config, load_config
from docksurf_py.core.models import (
    CommandResult,
    Container,
    DockerSnapshot,
)
from docksurf_py.core.paths import DATA_DIR
from docksurf_py.core.render.containers import NAME_WIDTH, age_seconds
from docksurf_py.core.render.images import (
    CREATED_WIDTH,
    REPO_WIDTH,
    SIZE_WIDTH,
    STATUS_WIDTH,
    TAG_WIDTH,
    status_rank,
)
from docksurf_py.core.render.networks import (
    CONTAINERS_WIDTH,
    NETWORK_DRIVER_WIDTH,
    NETWORK_NAME_WIDTH,
    SCOPE_WIDTH,
    SUBNET_WIDTH,
)
from docksurf_py.core.render.volumes import (
    DRIVER_WIDTH,
    PROJECT_WIDTH,
    USED_BY_WIDTH,
    VOLUME_NAME_WIDTH,
    VOLUME_SIZE_WIDTH,
    volume_project,
)
from docksurf_py.core.service import DockerService
from docksurf_py.core.session import SessionState, load_session, save_session
from docksurf_py.core.state.details import DetailCache, LiveTargets
from docksurf_py.core.state.ops import (
    BulkJob,
    plan_container_delete,
    plan_image_delete,
    plan_network_delete,
    plan_volume_delete,
)
from docksurf_py.core.state.refresh import RefreshCoordinator
from docksurf_py.core.state.status import STATE_SORT_ORDER, classify
from docksurf_py.core.state.tables import (
    CONTAINER_CHIPS,
    IMAGE_CHIPS,
    NETWORK_GROUP_SPECS,
    ResourceKind,
    TablesState,
    matches_container,
    matches_image,
    matches_network,
    matches_volume,
    volume_group_specs,
)
from docksurf_py.core.state.view import ViewState
from docksurf_py.core.types import LogOptions, SafeMarkup, TabID
from docksurf_py.tui.actions import (
    ClipboardHandler,
    ComposeActionHandler,
    ContainerActionHandler,
    ContextActionHandler,
    ImageActionHandler,
    InspectHandler,
    JumpHandler,
    NetworkActionHandler,
    PruneHandler,
    ResourceDeletionHandler,
    SelectionHandler,
    VolumeActionHandler,
)
from docksurf_py.tui.actions.jump import JUMP_TABS
from docksurf_py.tui.chrome import ChromeController
from docksurf_py.tui.ids import (
    BOTTOM_BAR_ID,
    CHIP_BAR_ID,
    CONNECTION_BANNER_ID,
    DETAIL_PANE_ID,
    DISK_PANEL_ID,
    EMPTY_STATE_IDS,
    ENDPOINTS_PANEL_ID,
    FILTER_PLACEHOLDERS,
    LAYERS_PANEL_ID,
    LIST_PANEL_ID,
    LIST_TOOLBAR_ID,
    LIVE_PANEL_ID,
    LOG_PANE_ID,
    MAIN_CONTAINER_ID,
    REFRESH_LOADING_ID,
    SAFE_DELETE_PANEL_ID,
    SEARCH_BAR_ID,
    SIDE_COLUMN_ID,
    TOOLBAR_LINE_ID,
    TOP_BAR_ID,
    TOPOLOGY_PANEL_ID,
    TableID,
)
from docksurf_py.tui.observability import LiveStatsController
from docksurf_py.tui.renderer import (
    DetailPaneRenderer,
    ResourceFocusResolver,
    SnapshotManager,
    TableRenderer,
)
from docksurf_py.tui.search import ResourceSearchController
from docksurf_py.tui.themes import (
    CUSTOM_THEMES,
    DEFAULT_THEME_NAME,
    THEME_CYCLE_NAMES,
    palette_from_theme,
)
from docksurf_py.tui.widgets import (
    BottomBar,
    ChipBar,
    ContainerTable,
    DetailPane,
    DiskPanel,
    EndpointsPanel,
    HelpScreen,
    ImageTable,
    LayersPanel,
    LivePanel,
    LogPane,
    NetworkTable,
    SafeDeletePanel,
    SearchBar,
    ToolbarLine,
    TopBar,
    TopologyPanel,
    VolumeTable,
    WhaleScreen,
)

logger = logging.getLogger(__name__)


def _container_only_actions() -> frozenset[str]:
    """Action names implemented by ContainerActionHandler.

    Used to scope the help screen to whatever that mixin actually owns, so
    the scope column can't drift out of sync the way a hand-copied set would.
    """
    return frozenset(
        name.removeprefix("action_")
        for name, member in vars(ContainerActionHandler).items()
        if name.startswith("action_") and callable(member)
    )


def _compose_actions() -> frozenset[str]:
    """Action names implemented by ComposeActionHandler.

    Lets the help screen tag project-wide actions with their own scope instead
    of mislabeling them Global — derived from the mixin so it can't drift.
    """
    return frozenset(
        name.removeprefix("action_")
        for name, member in vars(ComposeActionHandler).items()
        if name.startswith("action_") and callable(member)
    )


def _tab_actions(mixin: type) -> frozenset[str]:
    """Action names implemented directly by a tab-scoped handler mixin.

    Used to give the help screen a per-tab scope column for Image/Volume/
    Network actions — reflected from the mixin so it can't drift from what's
    actually bound (same trick as `_container_only_actions`).
    """
    return frozenset(
        name.removeprefix("action_")
        for name, member in vars(mixin).items()
        if name.startswith("action_") and callable(member)
    )


@dataclass(frozen=True)
class ResourceEntry:
    """Everything the app needs to treat one resource type generically.

    One instance per tab, keyed by `TabID` in `DockSurfApp._resource_registry`.
    Replaces the five places (auto-select, row-highlight, search filter,
    delete dispatch, plus the focus-resolver quartet) that used to hand-list
    each of the four resource types separately.
    """

    kind: ResourceKind  # the core half: label, items, matcher, sort keys, delete plan
    table_id: TableID
    columns: tuple[str, ...]
    populate: Callable[[DataTable, list], None]  # (table, rows) -> cells
    show_details: Callable[[DetailPane, int], None]
    column_widths: dict[str, int] = field(default_factory=dict)
    cursor_gutter: bool = False  # draw `▌` in the leading column under the cursor
    # Bottom line of the list panel, built from the tab's marked items
    # (None: no footer on this tab).
    footer: Callable[[list], SafeMarkup] | None = None
    # Columns whose header is right-aligned over right-aligned cells.
    right_columns: frozenset[str] = frozenset()
    # One-line summary above the filter row, built for a width (None: no line).
    toolbar_line: Callable[[int], SafeMarkup] | None = None

    @property
    def label(self) -> str:
        return self.kind.label


class AppContext(Protocol):
    """Structural contract each mixin's `self` is checked against by mypy.

    `TableRenderer`, `ContainerActionHandler`, etc. only ever run composed
    into `DockSurfApp` (which really does provide all of this), but mypy
    analyses each mixin class in isolation otherwise — reaching for
    `self.docker` in a bare `class ContainerActionHandler:` is an unchecked
    attribute error waiting to happen. `tui/renderer/`, `tui/actions/`,
    `tui/search.py` and `tui/observability.py` give each mixin
    `self: AppContext` via `_Base = AppContext if TYPE_CHECKING else object`,
    so mypy checks method bodies against this instead. Never instantiated or
    inherited at runtime. Per-concern app state lives in the five core state
    objects (`_tables`, `_refresh`, `_details`, `_live`, `_view`), not loose attributes.
    """

    snapshot: DockerSnapshot | None
    docker: DockerService
    config: Config
    _session: SessionState
    _persist_session: bool
    _resource_registry: dict[TabID, ResourceEntry]
    _tables: TablesState
    _refresh: RefreshCoordinator
    _details: DetailCache
    _live: LiveTargets
    _view: ViewState
    is_running: bool  # really a textual.app.App property
    theme: str  # really a textual.app.App reactive

    def start_refresh(self) -> None: ...
    def _repaint_chrome(self) -> None: ...
    def _repaint_top_bar(self) -> None: ...
    def _repaint_bottom_bar(self) -> None: ...
    def _repaint_list_panel(self) -> None: ...
    def _auto_select_first(self) -> None: ...
    def _apply_filter(self, query: str) -> None: ...
    def _rerender_containers(self) -> None: ...
    def _rerender_active_table(self) -> None: ...
    def _update_empty_state(
        self, tab_id: TabID, entry: ResourceEntry, items: list, query: str = ""
    ) -> None: ...
    def _sync_stats(self) -> None: ...
    def _sync_top(self) -> None: ...
    def _sync_image_platform(self) -> None: ...
    def _sync_image_layers(self) -> None: ...
    def _paint_image_layers(self, image: Any) -> None: ...
    def _sync_system_df(self) -> None: ...
    def _apply_system_df(self, df: Any) -> None: ...
    def _repaint_toolbar_line(self) -> None: ...
    def _repaint_footer(self) -> None: ...
    def _select_pending_focus(self) -> None: ...
    def _images_footer(self, marked: list) -> SafeMarkup: ...
    def _images_toolbar_line(self, width: int) -> SafeMarkup: ...
    def _volumes_footer(self, marked: list) -> SafeMarkup: ...
    def _networks_footer(self, marked: list) -> SafeMarkup: ...
    def _volumes_toolbar_line(self, width: int) -> SafeMarkup: ...
    def _repaint_disk_panel(self) -> None: ...
    def _sync_side_logs(self) -> None: ...
    def _sync_log_layout(self) -> None: ...
    def _exit_logs_full(self) -> None: ...
    def _retarget_side(self) -> None: ...
    def _schedule_side_retarget(self) -> None: ...
    def _sync_container_detail(self) -> None: ...
    def _refresh_detail_card(self) -> None: ...
    def _get_focused_container(self) -> Container | None: ...
    def _get_focused_resource(self, tab_id: TabID) -> Any: ...
    def _get_focused_project(self) -> Any: ...
    def _focused_is_project_header(self) -> bool: ...
    def _focused_row_key(self, tab_id: TabID) -> tuple[str, str] | None: ...
    def _restore_selection(
        self, tab_id: TabID, focus_key: tuple[str, str] | None
    ) -> None: ...
    def _gutter_cell(self, tab_id: TabID, item: Any, is_cursor: bool) -> Any: ...
    def _paint_gutter(
        self, table: Any, tab_id: TabID, old: int | None, new: int
    ) -> None: ...
    def _retint_row(self, table: Any, tab_id: TabID, row: int) -> None: ...
    def _marked_items(self, tab_id: TabID) -> list[Any]: ...
    def _run_bulk(
        self,
        tab_id: TabID,
        verb: str,
        jobs: list[BulkJob],
    ) -> None: ...
    def action_compose_stop(self) -> None: ...
    def action_compose_start(self) -> None: ...
    def action_compose_restart(self) -> None: ...
    def action_compose_down(self) -> Worker[None]: ...
    def action_toggle_group(self) -> None: ...
    def _open_container_logs(self, key: str, name: str) -> None: ...
    def _handle_write_result(self, result: CommandResult) -> None: ...

    # These are really `textual.app.App` methods. DockSurfApp's real
    # MRO includes both `App` and (fictitiously, TYPE_CHECKING-only) this
    # protocol, so mypy requires these stubs to be valid *overrides* of
    # App's real signatures — hence maximally loose rather than precise.
    def notify(self, *args: Any, **kwargs: Any) -> Any: ...
    def query_one(self, *args: Any, **kwargs: Any) -> Any: ...
    def call_from_thread(self, *args: Any, **kwargs: Any) -> Any: ...
    def push_screen_wait(self, *args: Any, **kwargs: Any) -> Any: ...
    def push_screen(self, *args: Any, **kwargs: Any) -> Any: ...
    def suspend(self, *args: Any, **kwargs: Any) -> Any: ...
    def set_timer(self, *args: Any, **kwargs: Any) -> Any: ...
    def copy_to_clipboard(self, *args: Any, **kwargs: Any) -> Any: ...


class DockSurfApp(
    TableRenderer,
    SnapshotManager,
    ResourceFocusResolver,
    DetailPaneRenderer,
    ContainerActionHandler,
    ComposeActionHandler,
    ResourceDeletionHandler,
    ClipboardHandler,
    SelectionHandler,
    InspectHandler,
    JumpHandler,
    PruneHandler,
    ContextActionHandler,
    ImageActionHandler,
    VolumeActionHandler,
    NetworkActionHandler,
    ResourceSearchController,
    LiveStatsController,
    ChromeController,
    App,
):
    TITLE = "DockSurf"

    snapshot: DockerSnapshot | None = None

    docker: DockerService
    config: Config

    _resource_registry: dict[TabID, ResourceEntry]

    BINDINGS = [
        ("?", "help", "Help"),
        ("r", "refresh", "Refresh"),
        ("/", "open_search", "Search"),
        ("q", "quit", "Quit"),
        # Container/log-viewer actions below are declared here so the Help screen
        # (`action_help`) and command palette (`get_system_commands`) both of which only
        # read `self.BINDINGS` still surface them everywhere. `show=False` keeps them
        # out of any key listing; `ContainerTable.BINDINGS` (widgets/tables.py)
        # re-declares the same actions so they fire while that table has focus.
        Binding("s", "stop_container", "Stop", show=False),
        Binding("S", "start_container", "Start", show=False),
        Binding("x", "restart_container", "Restart", show=False),
        Binding("p", "pause_container", "Pause/Unpause", show=False),
        Binding("K", "kill_container", "Kill", show=False),
        Binding("e", "exec_container", "Exec", show=False),
        Binding("E", "exec_custom", "Exec (custom)", show=False),
        ("i", "inspect", "Inspect"),
        ("d", "delete", "Delete"),
        Binding("l", "view_logs", "Full-view logs", show=False),
        Binding("f", "follow_logs", "Pause/Resume", show=False),
        Binding("c", "clear_logs", "Clear", show=False),
        Binding("C", "copy_files", "Copy files", show=False),
        Binding("z", "toggle_side", "Toggle side panels", show=False),
        Binding("o", "log_options", "Log options", show=False),
        Binding("T", "toggle_timestamps", "Toggle timestamps", show=False),
        Binding("W", "toggle_log_wrap", "Toggle wrap", show=False),
        Binding("n", "next_match", "Next log match", show=False),
        Binding("N", "prev_match", "Prev log match", show=False),
        Binding("g", "log_top", "Jump to log top", show=False),
        Binding("G", "log_bottom", "Jump to log bottom", show=False),
        Binding("X", "export_logs", "Export logs", show=False),
        Binding("ctrl+u", "compose_up", "Compose Up", show=False),
        Binding("ctrl+k", "compose_down", "Compose Down", show=False),
        Binding("B", "rebuild_service", "Rebuild service", show=False),
        Binding("t", "container_top", "Top", show=False),
        ("space", "toggle_mark", "Mark / Collapse"),
        ("w", "system_df", "Disk usage"),
        ("P", "prune", "Prune"),
        Binding("D", "switch_context", "Docker context", show=False),
        Binding("escape", "clear_marks", "Clear marks", show=False),
        Binding("Y", "yank", "Copy to clipboard", show=False),
        Binding("O", "open_port", "Open port in browser", show=False),
        Binding("R", "toggle_secrets", "Reveal/mask secret env vars", show=False),
        Binding("1", "switch_tab_1", "Containers tab", show=False),
        Binding("2", "switch_tab_2", "Images tab", show=False),
        Binding("3", "switch_tab_3", "Volumes tab", show=False),
        Binding("4", "switch_tab_4", "Networks tab", show=False),
        Binding("[", "prev_tab", "Previous tab", show=False),
        Binding("]", "next_tab", "Next tab", show=False),
        # Image / Volume / Network tab actions (Roadmap §5). Tab-scoped: each
        # action guards its tab and notifies a hint elsewhere. show=False keeps
        # the footer uncluttered; all are documented in the `?` help screen.
        Binding("plus", "new_resource", "New / Pull", show=False),
        Binding("h", "image_history", "Image layer history", show=False),
        Binding("y", "tag_image", "Tag image", show=False),
        Binding("a", "mark_all_dangling", "Mark dangling images", show=False),
        Binding("b", "volume_size", "Volume size on disk", show=False),
        Binding("v", "network_connect", "Connect to network", show=False),
        Binding("m", "network_disconnect", "Disconnect from network", show=False),
        Binding("F", "cycle_chip", "Cycle status filter", show=False),
        Binding("M", "cycle_theme", "Theme", show=True),
        Binding("~", "show_whale", "Whale", show=True),
    ]
    CSS_PATH = "app.tcss"
    # Focus the first table at boot, not the first focusable widget (the filter
    # input, which would swallow every key until the user leaves it).
    AUTO_FOCUS = "DataTable"

    def __init__(
        self,
        docker: DockerService,
        config: Config | None = None,
        session: SessionState | None = None,
        persist_session: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._injected_docker = docker
        self.config = config or Config()
        self._session = session or SessionState()
        self._persist_session = persist_session
        self._refresh = RefreshCoordinator()
        self._details = DetailCache()
        self._live = LiveTargets()
        self._view = ViewState()
        self._resource_registry = {
            TabID.CONTAINERS: ResourceEntry(
                kind=ResourceKind(
                    label="container",
                    plan_delete=plan_container_delete,
                    snapshot_items=lambda snap: snap.containers,
                    matches=matches_container,
                    sort_keys={
                        "Name": lambda c: c.name.lower(),
                        "State": lambda c: STATE_SORT_ORDER[classify(c)],
                        "Uptime": age_seconds,
                        "Image": lambda c: c.image_name.lower(),
                    },
                    groups_by_project=True,
                    chips=CONTAINER_CHIPS,
                ),
                table_id=TableID.CONTAINERS,
                columns=("Name", "State", "Uptime", "Ports", "Image"),
                populate=self._populate_container_table,
                show_details=self._show_container_details,
                column_widths={
                    "Name": NAME_WIDTH,
                    "State": 16,
                    "Uptime": 9,
                    "Ports": 17,
                },
                cursor_gutter=True,
            ),
            TabID.IMAGES: ResourceEntry(
                kind=ResourceKind(
                    label="image",
                    plan_delete=plan_image_delete,
                    snapshot_items=lambda snap: snap.images,
                    matches=matches_image,
                    sort_keys={
                        "Repository": lambda i: i.repository.lower(),
                        "Tag": lambda i: i.tag.lower(),
                        "Size": lambda i: i.size_bytes,
                        "Created": lambda i: i.created,
                        "Status": status_rank,
                    },
                    chips=IMAGE_CHIPS,
                ),
                table_id=TableID.IMAGES,
                columns=("Repository", "Tag", "Size", "Created", "Status"),
                populate=self._populate_image_table,
                show_details=self._show_image_details,
                column_widths={
                    "Repository": REPO_WIDTH,
                    "Tag": TAG_WIDTH,
                    "Size": SIZE_WIDTH,
                    "Created": CREATED_WIDTH,
                    "Status": STATUS_WIDTH,
                },
                right_columns=frozenset({"Size"}),
                footer=self._images_footer,
                cursor_gutter=True,
                toolbar_line=self._images_toolbar_line,
            ),
            TabID.VOLUMES: ResourceEntry(
                kind=ResourceKind(
                    label="volume",
                    plan_delete=plan_volume_delete,
                    snapshot_items=lambda snap: snap.volumes,
                    matches=matches_volume,
                    sort_keys={
                        "Name": lambda v: v.name.lower(),
                        "Project": lambda v: volume_project(v).lower(),
                        "Driver": lambda v: v.driver.lower(),
                        "Size": lambda v: self._details.volume_sizes.get(v.name, -1),
                        "Used by": lambda v: len(v.used_by),
                    },
                    groups=volume_group_specs(lambda: self._details.volume_sizes),
                    grouped_by="usage",
                ),
                table_id=TableID.VOLUMES,
                columns=("Name", "Project", "Driver", "Size", "Used by"),
                populate=self._populate_volume_table,
                show_details=self._show_volume_details,
                column_widths={
                    "Name": VOLUME_NAME_WIDTH,
                    "Project": PROJECT_WIDTH,
                    "Driver": DRIVER_WIDTH,
                    "Size": VOLUME_SIZE_WIDTH,
                    "Used by": USED_BY_WIDTH,
                },
                right_columns=frozenset({"Size"}),
                footer=self._volumes_footer,
                cursor_gutter=True,
                toolbar_line=self._volumes_toolbar_line,
            ),
            TabID.NETWORKS: ResourceEntry(
                kind=ResourceKind(
                    label="network",
                    plan_delete=plan_network_delete,
                    snapshot_items=lambda snap: snap.networks,
                    matches=matches_network,
                    sort_keys={
                        "Name": lambda n: n.name.lower(),
                        "Driver": lambda n: n.driver.lower(),
                        "Scope": lambda n: n.scope.lower(),
                    },
                    groups=NETWORK_GROUP_SPECS,
                    grouped_by="owner",
                ),
                table_id=TableID.NETWORKS,
                columns=("Name", "Driver", "Scope", "Subnet", "Containers"),
                populate=self._populate_network_table,
                show_details=self._show_network_details,
                column_widths={
                    "Name": NETWORK_NAME_WIDTH,
                    "Driver": NETWORK_DRIVER_WIDTH,
                    "Scope": SCOPE_WIDTH,
                    "Subnet": SUBNET_WIDTH,
                    "Containers": CONTAINERS_WIDTH,
                },
                footer=self._networks_footer,
                cursor_gutter=True,
            ),
        }

    def compose(self) -> ComposeResult:
        yield TopBar(id=TOP_BAR_ID)
        yield Static("", id=CONNECTION_BANNER_ID)
        with Horizontal(id=MAIN_CONTAINER_ID):
            with Vertical(id=LIST_PANEL_ID, classes="panel"):
                yield ToolbarLine(id=TOOLBAR_LINE_ID)
                with Horizontal(id=LIST_TOOLBAR_ID):
                    yield SearchBar(
                        placeholder=FILTER_PLACEHOLDERS[TabID.CONTAINERS],
                        id=SEARCH_BAR_ID,
                    )
                    yield ChipBar(id=CHIP_BAR_ID)
                with TabbedContent():
                    with TabPane("Containers", id=TabID.CONTAINERS):
                        yield ContainerTable(id=TableID.CONTAINERS)
                        yield Static(
                            "",
                            id=EMPTY_STATE_IDS[TabID.CONTAINERS],
                            classes="empty-state",
                        )
                    with TabPane("Images", id=TabID.IMAGES):
                        yield ImageTable(id=TableID.IMAGES)
                        yield Static(
                            "", id=EMPTY_STATE_IDS[TabID.IMAGES], classes="empty-state"
                        )
                    with TabPane("Volumes", id=TabID.VOLUMES):
                        yield VolumeTable(id=TableID.VOLUMES)
                        yield Static(
                            "", id=EMPTY_STATE_IDS[TabID.VOLUMES], classes="empty-state"
                        )
                    with TabPane("Networks", id=TabID.NETWORKS):
                        yield NetworkTable(id=TableID.NETWORKS)
                        yield Static(
                            "",
                            id=EMPTY_STATE_IDS[TabID.NETWORKS],
                            classes="empty-state",
                        )
            with Vertical(id=SIDE_COLUMN_ID):
                yield DetailPane(id=DETAIL_PANE_ID, classes="panel")
                yield LivePanel(id=LIVE_PANEL_ID, classes="panel")
                yield LayersPanel(id=LAYERS_PANEL_ID, classes="panel")
                yield SafeDeletePanel(id=SAFE_DELETE_PANEL_ID, classes="panel -warning")
                yield DiskPanel(id=DISK_PANEL_ID, classes="panel")
                yield TopologyPanel(id=TOPOLOGY_PANEL_ID, classes="panel")
                yield EndpointsPanel(id=ENDPOINTS_PANEL_ID, classes="panel")
                yield LogPane(
                    id=LOG_PANE_ID,
                    classes="panel",
                    default_options=LogOptions(
                        tail=self.config.default_log_tail,
                        since_seconds=self.config.default_log_since_seconds,
                    ),
                )
        yield LoadingIndicator(id=REFRESH_LOADING_ID)
        yield BottomBar(id=BOTTOM_BAR_ID)

    def on_mount(self) -> None:
        self.docker = self._injected_docker
        for theme in CUSTOM_THEMES:
            self.register_theme(theme)
        self.theme = (
            self._session.theme
            if self._session.theme in self.available_themes
            else DEFAULT_THEME_NAME
        )
        self._view.palette = palette_from_theme(self.available_themes[self.theme])
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        if self._session.active_tab:
            try:
                self.query_one(TabbedContent).active = TabID(self._session.active_tab)
            except ValueError:
                pass
        # DockerClient connects lazily on the first fetch_snapshot() call
        # (inside start_refresh()'s background worker), not here — so the
        # UI never blocks on the daemon round-trip before its first paint.
        # Any connection failure is surfaced from SnapshotManager once that
        # first fetch comes back.
        self.setup_tables()
        self._sync_log_layout()
        self._repaint_chrome()
        self.start_refresh()
        # Live: react to `docker events` so the tables stay current without `r`.
        self.start_event_listener()

    def on_unmount(self) -> None:
        self.stop_event_listener()
        self.stop_stats()
        if self._search_filter_timer is not None:
            self._search_filter_timer.stop()
            self._search_filter_timer = None

    def action_refresh(self) -> None:
        self.start_refresh()

    def _switch_tab(self, tab_id: TabID) -> None:
        self.query_one(TabbedContent).active = tab_id

    def action_switch_tab_1(self) -> None:
        self._switch_tab(TabID.CONTAINERS)

    def action_switch_tab_2(self) -> None:
        self._switch_tab(TabID.IMAGES)

    def action_switch_tab_3(self) -> None:
        self._switch_tab(TabID.VOLUMES)

    def action_switch_tab_4(self) -> None:
        self._switch_tab(TabID.NETWORKS)

    def action_next_tab(self) -> None:
        tabs = list(TabID)
        idx = tabs.index(TabID(self.query_one(TabbedContent).active))
        self._switch_tab(tabs[(idx + 1) % len(tabs)])

    def action_prev_tab(self) -> None:
        tabs = list(TabID)
        idx = tabs.index(TabID(self.query_one(TabbedContent).active))
        self._switch_tab(tabs[(idx - 1) % len(tabs)])

    def _auto_select_first(self) -> None:
        if not self.snapshot:
            return
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None:
            pane.clear_details()
            self._retarget_side()
            return
        table = self.query_one(f"#{entry.table_id}", DataTable)
        # Explicitly follow focus to the active tab's table (rather than
        # relying on Textual's implicit initial-focus behavior) so each table's
        # BINDINGS scoping is deterministic — unless the user is typing a filter.
        if not self.query_one(f"#{SEARCH_BAR_ID}", SearchBar).has_focus:
            table.focus()
        current = self._tables.rows.get(active, [])
        if not current:
            pane.clear_details()
            self._retarget_side()
            return
        table.move_cursor(row=0)
        try:
            entry.show_details(pane, 0)
        except IndexError:
            pane.clear_details()
        self._retarget_side()

    @on(DataTable.RowHighlighted)
    def update_details(self, event: DataTable.RowHighlighted) -> None:
        # A queued RowHighlighted can dispatch during teardown (a write action's
        # refresh repopulates a table just as the app unmounts) — the widgets
        # are gone, so bail rather than raise NoMatches, matching the
        # is_running guard in SnapshotManager._apply_snapshot.
        if not self.is_running or not self.snapshot:
            return
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None or event.control.id != entry.table_id:
            return
        self._paint_gutter(event.control, active, None, event.cursor_row)
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        try:
            entry.show_details(pane, event.cursor_row)
        except IndexError:
            pane.clear_details()
        # The card is cheap and drawn at once; the streams and fetches follow
        # the cursor only once it settles (holding `↓` starts one, not fifty).
        self._schedule_side_retarget()
        self._repaint_bottom_bar()

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        """`enter` (or a second click) on a row: jump to the related container
        on the tabs that have one. Handled here, not in a mixin, where `@on`
        would be silently dead."""
        if not self.is_running:
            return
        active = self.query_one(TabbedContent).active
        entry = self._resource_registry.get(active)
        if entry is None or event.control.id != entry.table_id:
            return
        if active in JUMP_TABS:
            self.action_jump_related()

    @on(TabbedContent.TabActivated)
    def clear_on_tab_switch(self) -> None:
        if not self.is_running:
            return  # a queued activation dispatched during teardown
        active = self.query_one(TabbedContent).active
        if self._tables.is_dirty(active):
            self._populate_tab(active)
        self._sync_log_layout()
        self._auto_select_first()
        if active == TabID.CONTAINERS:
            self._select_pending_focus()  # a jump from Images/Volumes/Networks
        else:
            self._tables.take_pending_focus()  # a jump that didn't land: drop it
        self._repaint_chrome()
        if active in (TabID.IMAGES, TabID.VOLUMES):
            self._sync_system_df()
        if self._persist_session:
            self._session.active_tab = active
            save_session(self._session)

    @on(DataTable.HeaderSelected)
    def _on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        # Must live here, not on the TableRenderer mixin: `@on` only wires up
        # through Textual's metaclass for methods on a real message-pump
        # class, and the mixins run with a plain `object` base at runtime.
        self._on_header_selected(event)

    @on(SearchBar.Changed, f"#{SEARCH_BAR_ID}")
    def _on_search_input_changed(self, event: SearchBar.Changed) -> None:
        # Same reason as _on_data_table_header_selected above — the real
        # logic lives on ResourceSearchController (search.py), a mixin.
        self.on_search_changed(event)

    @on(SearchBar.Submitted, f"#{SEARCH_BAR_ID}")
    def _on_search_input_submitted(self, event: SearchBar.Submitted) -> None:
        self.on_search_escape()

    @on(events.DescendantFocus)
    @on(events.DescendantBlur)
    def _on_focus_moved(self) -> None:
        # The bottom bar shows filter hints while the filter input has focus.
        self._repaint_bottom_bar()

    @on(SearchBar.Dismissed)
    def _on_search_dismissed(self) -> None:
        self.on_search_escape()

    def action_new_resource(self) -> None:
        """`+`: create/pull on whichever non-container tab is active."""
        active = self.query_one(TabbedContent).active
        if active == TabID.IMAGES:
            self.action_pull_image()
        elif active == TabID.VOLUMES:
            self.action_create_volume()
        elif active == TabID.NETWORKS:
            self.action_create_network()
        else:
            self.notify("Nothing to create on this tab", severity="information")

    def _on_theme_changed(self, theme: Theme) -> None:
        """Rebuild the palette and repaint: the active table now, the others
        lazily when they next become active."""
        self._view.palette = palette_from_theme(theme)
        self._repaint_chrome()
        if not self.is_running or not self.snapshot:
            return
        active = self.query_one(TabbedContent).active
        self._tables.dirty |= {tab for tab in TabID if tab != active}
        self._rerender_active_table()

    def action_cycle_theme(self) -> None:
        """`M`: cycle through the curated DockSurf themes, plus the
        terminal-native ansi-dark/ansi-light options at the end."""
        names = THEME_CYCLE_NAMES
        idx = names.index(self.theme) if self.theme in names else -1
        next_name = names[(idx + 1) % len(names)]
        self.theme = next_name
        self._session.theme = next_name
        if self._persist_session:
            save_session(self._session)
        self.notify(f"Theme: {next_name}")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Surface every bound action in the command palette (`ctrl+p`).

        Textual's default only offers Theme/Quit/Keys/Screenshot/Maximize.
        Reflecting over `BINDINGS` — the same source `?`'s help screen reads
        from — makes every DockSurf action discoverable, including the
        `show=False` ones hidden from the footer (that's the point: hidden
        from the footer, not from the palette).
        """
        yield from super().get_system_commands(screen)
        seen_actions: set[str] = set()
        for binding in self.BINDINGS:
            if isinstance(binding, Binding):
                key, action, description = (
                    binding.key,
                    binding.action,
                    binding.description,
                )
            elif len(binding) == 3:
                key, action, description = binding
            else:
                key, action = binding
                description = ""
            if not description or action in seen_actions:
                continue
            seen_actions.add(action)
            method = getattr(self, f"action_{action}", None)
            if method is None:
                continue
            yield SystemCommand(description, f"Key: {key}", method)

    def action_help(self) -> None:
        self.push_screen(
            HelpScreen(
                self.BINDINGS,
                _container_only_actions(),
                _compose_actions(),
                tab_actions={
                    "Images tab": _tab_actions(ImageActionHandler),
                    "Volumes tab": _tab_actions(VolumeActionHandler),
                    "Networks tab": _tab_actions(NetworkActionHandler),
                },
            )
        )

    def action_show_whale(self) -> None:
        self.push_screen(WhaleScreen())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="docksurf", description="Terminal UI for Docker resources"
    )
    parser.add_argument(
        "--host", help="Docker daemon endpoint to connect to (e.g. tcp://host:2375)"
    )
    parser.add_argument(
        "--context", help="Docker context to connect through (this run only)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=f"Path to config.toml (default: {DEFAULT_CONFIG_PATH})",
    )
    return parser.parse_args(argv)


def main():
    from docksurf_py.core.docker import DockerClient

    args = _parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DATA_DIR / "docksurf.log"

    # Rotating FileHandlers, capped at 5MB per file, keeping
    # 3 rotated backups (~20MB total)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-30s  %(levelname)-8s  %(message)s",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        ],
    )
    logger.info("DockSurf starting — log file: %s", log_file)
    DockSurfApp(
        docker=DockerClient(context_override=args.context, host_override=args.host),
        config=load_config(args.config),
        session=load_session(),
        persist_session=True,
    ).run()
    logger.info("DockSurf exiting")


if __name__ == "__main__":
    main()
