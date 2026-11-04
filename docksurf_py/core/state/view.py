"""View-level state: the active palette, layout mode and which panels are
shown. Decisions only — the tui reads these and paints."""

from dataclasses import dataclass
from enum import StrEnum

from docksurf_py.core.render.chrome import Connection
from docksurf_py.core.render.palette import TIDE_PALETTE, Palette

# Below either bound the side column no longer fits next to the list.
COMPACT_BELOW_WIDTH = 120
COMPACT_BELOW_HEIGHT = 30
# Leaving compact needs this much extra room, so dragging a window edge across
# a threshold doesn't flap between layouts.
HYSTERESIS = 2


class LayoutMode(StrEnum):
    WIDE = "wide"
    COMPACT = "compact"


@dataclass(slots=True)
class ViewState:
    palette: Palette = TIDE_PALETTE
    layout: LayoutMode = LayoutMode.WIDE
    side_hidden: bool = False  # `z` in wide mode: table takes the full width
    side_overlay: bool = False  # `z` in compact mode: side column over the list
    logs_full: bool = False  # `l`: full-view log
    connection: Connection = "connecting"
    connection_message: str = ""  # why we're offline; empty while live

    def layout_for(self, width: int, height: int) -> LayoutMode:
        """The layout a terminal of this size should use. Relative to the
        current mode: going wide needs `HYSTERESIS` more cells than staying
        wide does."""
        margin = HYSTERESIS if self.layout is LayoutMode.COMPACT else 0
        if (
            width < COMPACT_BELOW_WIDTH + margin
            or height < COMPACT_BELOW_HEIGHT + margin
        ):
            return LayoutMode.COMPACT
        return LayoutMode.WIDE

    def resize(self, width: int, height: int) -> bool:
        """Adopt the layout for a new terminal size; True if the mode changed.
        The compact overlay never survives a switch to wide (and vice versa
        for the wide hide flag) so `z` always starts from a clean state."""
        mode = self.layout_for(width, height)
        if mode is self.layout:
            return False
        self.layout = mode
        self.side_overlay = False
        self.side_hidden = False
        return True

    @property
    def side_visible(self) -> bool:
        """Whether the side column is on screen: shown by default in wide, and
        only as the `z` overlay in compact. Streams only it displays must stop
        while this is False."""
        if self.layout is LayoutMode.COMPACT:
            return self.side_overlay
        return not self.side_hidden

    def toggle_side(self) -> bool:
        """`z`: flip the flag for the current layout; returns its new value."""
        if self.layout is LayoutMode.COMPACT:
            self.side_overlay = not self.side_overlay
            return self.side_overlay
        self.side_hidden = not self.side_hidden
        return self.side_hidden

    def toggle_logs_full(self) -> bool:
        self.logs_full = not self.logs_full
        return self.logs_full
