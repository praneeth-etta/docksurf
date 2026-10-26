"""ChipBar — the status-filter chips in the list toolbar."""

from rich.text import Text
from textual.widgets import Static


class ChipBar(Static):
    """Shows the status chips for the active tab (filled in by a later phase;
    empty until then)."""

    def set_markup(self, markup: str) -> None:
        self.update(Text.from_markup(markup) if markup else "")
