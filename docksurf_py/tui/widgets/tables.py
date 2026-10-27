"""DataTable subclasses carrying per-tab key bindings.

A widget's own `BINDINGS` apply only while it has focus, so declaring a tab's
actions here (rather than only on `DockSurfApp.BINDINGS`) scopes `Stop`/
`History`/`Size` etc. to the tab they apply to. The bottom bar's per-row hints
(`core.render.chrome.key_hints`) mirror these. The matching App-level entries
stay `show=False` and exist purely so the `?` help screen and command palette
(both of which only read `App.BINDINGS`) still surface every action.
"""

from rich.style import Style
from textual.binding import Binding
from textual.widgets import DataTable


class ResourceTable(DataTable):
    """Base for the four resource tables: whole-row background tints and the
    cursor-gutter bookkeeping. Display state only; which rows are headers or
    marked is decided by the caller (`TableRenderer`) from core state."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Row index -> extra style (group header surface, marked background).
        self.row_tints: dict[int, Style] = {}
        # Row currently showing the `▌` cursor gutter, if any.
        self.gutter_row: int | None = None

    def clear(self, columns: bool = False):
        self.row_tints = {}
        self.gutter_row = None
        return super().clear(columns=columns)

    def set_row_tint(self, row: int, style: Style | None) -> None:
        if style is None:
            self.row_tints.pop(row, None)
        else:
            self.row_tints[row] = style
        self.refresh()

    # Private Textual hook (8.2.7): tints every segment of a row, padding
    # between cells included. `tests/test_redesign_containers.py` fails if a
    # Textual upgrade removes or renames it.
    def _get_row_style(self, row_index: int, base_style: Style) -> Style:
        style = super()._get_row_style(row_index, base_style)
        tint = self.row_tints.get(row_index)
        return style + tint if tint is not None else style


class ContainerTable(ResourceTable):
    """A Table specifically for Containers with context-aware bindings."""

    def __init__(self, *args, **kwargs) -> None:
        # DataTable's default ("css") makes the cursor row's CSS foreground
        # color override every cell's own color, including the status dot
        # prefixed to the Name cell, which would otherwise go flat/unreadable
        # on exactly the row a user is currently looking at.
        kwargs.setdefault("cursor_foreground_priority", "renderable")
        super().__init__(*args, **kwargs)

    BINDINGS = [
        Binding("s", "stop_container", "Stop"),
        Binding("S", "start_container", "Start"),
        Binding("x", "restart_container", "Restart"),
        Binding("p", "pause_container", "Pause/Unpause"),
        Binding("K", "kill_container", "Kill"),
        Binding("e", "exec_container", "Exec"),
        Binding("E", "exec_custom", "Exec (custom)", show=False),
        Binding("C", "copy_files", "Copy files", show=False),
        Binding("l", "view_logs", "Logs"),
        Binding("f", "follow_logs", "Follow"),
        Binding("c", "clear_logs", "Clear", show=False),
        Binding("z", "toggle_log_expand", "Expand Logs", show=False),
        # Log-viewer controls, active while the pane is open (the handlers
        # no-op when it isn't). Mirrored here — like l/f/z above — so they fire
        # while the container table keeps focus; bare action names resolve up to
        # the app's handlers.
        Binding("o", "log_options", "Log options", show=False),
        Binding("T", "toggle_timestamps", "Timestamps", show=False),
        Binding("W", "toggle_log_wrap", "Wrap", show=False),
        Binding("n", "next_match", "Next match", show=False),
        Binding("N", "prev_match", "Prev match", show=False),
        Binding("g", "log_top", "Log top", show=False),
        Binding("G", "log_bottom", "Log bottom", show=False),
        Binding("X", "export_logs", "Export logs", show=False),
        Binding("d", "delete", "Delete"),
        Binding("ctrl+u", "compose_up", "Compose Up"),
        Binding("ctrl+k", "compose_down", "Compose Down"),
        Binding("B", "rebuild_service", "Rebuild", show=False),
        Binding("t", "container_top", "Top"),
        Binding("F", "cycle_chip", "Status filter", show=False),
        Binding("space", "toggle_mark", "Mark / Collapse", show=False),
    ]


class ImageTable(ResourceTable):
    """A Table specifically for Images with context-aware bindings."""

    BINDINGS = [
        Binding("h", "image_history", "History"),
        Binding("y", "tag_image", "Tag"),
        Binding("a", "mark_all_dangling", "Mark dangling"),
    ]


class VolumeTable(ResourceTable):
    """A Table specifically for Volumes with context-aware bindings."""

    BINDINGS = [
        Binding("b", "volume_size", "Size"),
    ]


class NetworkTable(ResourceTable):
    """A Table specifically for Networks with context-aware bindings."""

    BINDINGS = [
        Binding("v", "network_connect", "Connect"),
        Binding("m", "network_disconnect", "Disconnect"),
    ]
