"""SearchBar — the inline filter input in the list toolbar."""

from textual.message import Message
from textual.widgets import Input


class SearchBar(Input):
    """Filter input that hands focus back to the table on Escape.

    The query is kept (it stays visible in the toolbar and keeps filtering);
    clearing it is just editing the text.
    """

    BINDINGS = [("escape", "dismiss", "Back to list")]

    class Dismissed(Message):
        """The user pressed Escape in the filter; the app moves focus back."""

    def action_dismiss(self) -> None:
        self.post_message(self.Dismissed())
