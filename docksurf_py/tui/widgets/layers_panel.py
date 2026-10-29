"""LayersPanel — the side column's `layers · 14 · largest first` panel (Images)."""

from collections.abc import Callable

from rich.console import RenderableType
from rich.text import Text
from textual.events import Resize
from textual.widgets import Static


class LayersPanel(Static):
    """Shows an image's biggest layers, rebuilt at the panel's own size.
    Display-only: the content builder is core's `layers_summary`, handed in as
    a callable taking the inner width and height."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, **kwargs)
        self._builder: Callable[[int, int], RenderableType] | None = None

    def on_mount(self) -> None:
        self.display = False  # shown on the Images tab only

    def show_layers(
        self, title: str, builder: Callable[[int, int], RenderableType]
    ) -> None:
        self.border_title = title
        self.border_subtitle = "h full history"
        self._builder = builder
        self._paint()

    def show_message(self, title: str, message: RenderableType) -> None:
        """A placeholder (`…` while fetching, `unavailable`, no image focused)."""
        self.border_title = title
        self.border_subtitle = ""
        self._builder = None
        self.update(Text.from_markup(message) if isinstance(message, str) else message)

    def on_resize(self, event: Resize) -> None:
        self._paint()

    def _paint(self) -> None:
        if self._builder is not None:
            size = self.content_size
            content = self._builder(max(size.width, 1), max(size.height, 1))
            self.update(
                Text.from_markup(content) if isinstance(content, str) else content
            )
