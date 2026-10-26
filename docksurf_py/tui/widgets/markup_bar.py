"""MarkupBar — a one-line `Static` that repaints Rich markup at its own width."""

from collections.abc import Callable

from rich.text import Text
from textual.events import Resize
from textual.widgets import Static


class MarkupBar(Static):
    """Paints the Rich markup returned by a core builder.

    The builder takes the bar's current width so core can right-align and drop
    pieces that don't fit; the bar re-invokes it on resize. Display-only: what
    to show is decided by the caller.
    """

    _build: Callable[[int], str] | None = None

    def set_builder(self, build: Callable[[int], str]) -> None:
        self._build = build
        self._paint()

    def on_resize(self, event: Resize) -> None:
        self._paint()

    def _paint(self) -> None:
        width = self.size.width
        if self._build is None or width <= 0:
            return
        text = Text.from_markup(self._build(width))
        text.no_wrap = True
        text.overflow = "crop"
        self.update(text)
