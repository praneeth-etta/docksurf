"""SafeDeletePanel — the `safe to delete` card shown for an orphaned volume."""

from rich.text import Text
from textual.widgets import Static


class SafeDeletePanel(Static):
    """Display-only: the text is built by core's `safe_delete_body`. Hidden
    unless the focused volume is mounted by no container."""

    def on_mount(self) -> None:
        self.border_title = "safe to delete"
        self.display = False

    def show_body(self, markup: str) -> None:
        self.update(Text.from_markup(markup))
        self.display = True

    def hide(self) -> None:
        self.display = False
