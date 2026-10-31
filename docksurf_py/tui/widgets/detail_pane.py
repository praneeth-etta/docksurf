"""DetailPane — key-value table plus collapsible extras for the side panel."""

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from docksurf_py.core.render.cards import CardModel, CardSection


class DetailPane(VerticalScroll):
    """The side column's detail panel: a body plus collapsible extras.

    Containers and Compose projects are drawn from a `CardModel`
    (`update_card`): a titled panel whose `▸` sections are `Collapsible`s that
    keep their open/closed state across re-renders of the same card. Every tab
    draws a card. All renderables are built by the controller, keeping this
    widget display-only (no Docker imports).
    """

    _panel: Static

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Card sections by key, and the card they belong to.
        self._card_key: str | None = None
        self._sections: dict[str, Collapsible] = {}
        self._section_bodies: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        self._panel = Static("Select an item to view details.", classes="muted")
        yield self._panel

    # --- Cards ---------------------------------------------------------------

    def update_card(self, card: CardModel) -> None:
        """Paint a card. Sections of the same card (same `key`) are updated in
        place, so an open section stays open across refreshes; a different
        card starts with every section collapsed."""
        self.border_title = Text.from_markup(card.title)
        self.border_subtitle = card.subtitle
        self._panel.remove_class("muted")
        self._panel.update(card.body)
        if card.key != self._card_key:
            self._remove_sections(set())
            self._card_key = card.key
        self._remove_sections({s.key for s in card.sections})
        for section in card.sections:
            self._sync_section(section)

    def section(self, key: str) -> Collapsible | None:
        return self._sections.get(key)

    def section_body(self, key: str) -> Static | None:
        return self._section_bodies.get(key)

    def expand_section(self, key: str) -> None:
        if (section := self._sections.get(key)) is not None:
            section.collapsed = False

    def collapse_section(self, key: str) -> None:
        if (section := self._sections.get(key)) is not None:
            section.collapsed = True

    @staticmethod
    def _section_title(section: CardSection) -> str:
        return (
            f"{section.summary}   {section.hint}" if section.hint else section.summary
        )

    def _sync_section(self, section: CardSection) -> None:
        title = self._section_title(section)
        existing = self._sections.get(section.key)
        if existing is None:
            body = Static("")
            body.styles.padding = (0, 2)
            self._section_bodies[section.key] = body
            existing = Collapsible(body, title=title, collapsed=True)
            self._sections[section.key] = existing
            self.mount(existing)
        else:
            existing.title = title
        if section.content is not None:
            content = section.content
            self._section_bodies[section.key].update(
                escape(content) if isinstance(content, str) else content
            )

    def _remove_sections(self, keep: set[str]) -> None:
        for key in [k for k in self._sections if k not in keep]:
            self._sections.pop(key).remove()
            self._section_bodies.pop(key, None)

    def clear_details(self) -> None:
        self.border_title = ""
        self.border_subtitle = ""
        self._card_key = None
        self._remove_sections(set())
        self._panel.update("Select an item to view details.")
        self._panel.add_class("muted")
