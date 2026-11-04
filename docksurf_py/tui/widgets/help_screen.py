"""HelpScreen — the `?` keybindings cheat sheet, grouped by scope."""

from collections.abc import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from docksurf_py.core.render.help import HelpRow, group_rows, help_markup
from docksurf_py.core.render.palette import Palette


class HelpScreen(ModalScreen):
    """Keybindings cheat sheet. The rows are reflected from the live `BINDINGS`
    by the app (so the sheet can't drift from what is bound); grouping and
    markup come from `core.render.help`. This widget only shows them."""

    def __init__(self, rows: Sequence[HelpRow], palette: Palette) -> None:
        super().__init__()
        self._rows = rows
        self._palette = palette

    def on_key(self, event) -> None:
        if event.key in ("escape", "question_mark"):
            # Stop the event — otherwise it bubbles to the app's global "?"
            # binding after dismiss() and immediately reopens the screen.
            event.stop()
            self.dismiss()

    def compose(self) -> ComposeResult:
        body = Static(
            Text.from_markup(help_markup(group_rows(self._rows), self._palette)),
            id="help-body",
        )
        scroll = VerticalScroll(body, id="help-scroll", classes="panel")
        scroll.border_title = "keys"
        scroll.border_subtitle = "esc or ? to close"
        yield scroll
