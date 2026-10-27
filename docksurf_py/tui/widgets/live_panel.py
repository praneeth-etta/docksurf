"""LivePanel — the side column's `● live · 1s` panel (sparklines and gauge)."""

from collections.abc import Callable

from rich.console import RenderableType
from textual.events import Resize
from textual.widgets import Static


class LivePanel(Static):
    """Shows whatever the controller builds for it, rebuilt at the panel's own
    width. Display-only: the content builder is core's `render_live`, handed in
    as a callable that takes the inner width."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, **kwargs)
        self._builder: Callable[[int], RenderableType] | None = None

    def on_mount(self) -> None:
        self.border_title = "● live · 1s"
        self.display = False  # shown once a running container is focused

    def show_live(self, builder: Callable[[int], RenderableType]) -> None:
        self._builder = builder
        self._paint()

    def show_message(self, message: RenderableType) -> None:
        self._builder = None
        self.update(message)

    def clear_live(self) -> None:
        self._builder = None
        self.update("")

    def on_resize(self, event: Resize) -> None:
        self._paint()

    def _paint(self) -> None:
        if self._builder is not None:
            self.update(self._builder(max(self.content_size.width, 1)))
