"""DiskPanel — the side column's `disk usage · docker system df` panel (Volumes)."""

from collections.abc import Callable

from rich.console import RenderableType
from rich.text import Text
from textual.events import Resize
from textual.widgets import Static


class DiskPanel(Static):
    """Shows the disk usage bars, rebuilt at the panel's own width.
    Display-only: the content builder is core's `render_df_bars`, handed in as
    a callable taking the inner width."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, **kwargs)
        self._builder: Callable[[int], RenderableType] | None = None

    def on_mount(self) -> None:
        self.border_title = "disk usage · docker system df"
        self.border_subtitle = "w full report"
        self.display = False  # shown on the Volumes tab only

    def show_disk(self, builder: Callable[[int], RenderableType]) -> None:
        self._builder = builder
        self._paint()

    def show_message(self, message: str) -> None:
        """A placeholder while `docker system df` is still being computed."""
        self._builder = None
        self.update(Text.from_markup(message))

    def on_resize(self, event: Resize) -> None:
        self._paint()

    def _paint(self) -> None:
        if self._builder is not None:
            self.update(self._builder(max(self.content_size.width, 1)))
