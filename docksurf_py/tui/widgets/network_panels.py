"""TopologyPanel and EndpointsPanel — the Networks tab's side column."""

from collections.abc import Callable

from rich.console import RenderableType
from rich.text import Text
from textual.events import Resize
from textual.widgets import Static

Builder = Callable[[int, int], RenderableType]  # (inner width, inner height)


class _BuilderPanel(Static):
    """A panel redrawn at its own size. Display-only: the content builder is
    core's, handed in as a callable taking the inner width and height."""

    subtitle = ""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__("", *args, **kwargs)
        self._builder: Builder | None = None

    def on_mount(self) -> None:
        self.display = False  # shown on the Networks tab only

    def show_content(self, title: str, builder: Builder) -> None:
        self.border_title = title
        self.border_subtitle = self.subtitle
        self._builder = builder
        self._paint()

    def show_message(self, title: str, message: str) -> None:
        """A placeholder (`select a network`)."""
        self.border_title = title
        self.border_subtitle = ""
        self._builder = None
        self.update(Text.from_markup(message))

    def on_resize(self, event: Resize) -> None:
        self._paint()

    def _paint(self) -> None:
        if self._builder is not None:
            size = self.content_size
            self.update(self._builder(max(size.width, 1), max(size.height, 1)))


class TopologyPanel(_BuilderPanel):
    """`topology · 7 endpoints` — the bus diagram."""

    # Phase 07 adds `· ←→↑↓ move between nodes` once the arrows work.
    subtitle = "bus layout scales to any count"


class EndpointsPanel(_BuilderPanel):
    """`endpoints` — container, IPv4 and MAC of what is attached."""

    subtitle = "v connect · m disconnect"
