"""DetailPane — key-value table plus collapsible extras for the side panel."""

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from docksurf_py.core.render.cards import CardModel, CardSection
from docksurf_py.core.types import SafeMarkup


class DetailPane(VerticalScroll):
    """The side column's detail panel: a body plus collapsible extras.

    Containers and Compose projects are drawn from a `CardModel`
    (`update_card`): a titled panel whose `▸` sections are `Collapsible`s that
    keep their open/closed state across re-renders of the same card. The other
    tabs still use `update_details` until they move to cards too.

    The `_topology_panel` region shows the network-topology diagram for the
    selected item and updates independently of the body, so it never resets
    the collapsibles. All renderables are built by the controller, keeping
    this widget display-only (no Docker imports).
    """

    _panel: Static
    _topology_panel: Static
    _env_collapsible: "Collapsible | None" = None
    _health_collapsible: "Collapsible | None" = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Card sections by key, and the card they belong to.
        self._card_key: str | None = None
        self._sections: dict[str, Collapsible] = {}
        self._section_bodies: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        self._panel = Static("Select an item to view details.", classes="muted")
        yield self._panel
        self._topology_panel = Static("")
        yield self._topology_panel

    def update_topology(self, content) -> None:
        self._topology_panel.update(content)

    def clear_topology(self) -> None:
        self._topology_panel.update("")

    # --- Cards ---------------------------------------------------------------

    def update_card(self, card: CardModel) -> None:
        """Paint a card. Sections of the same card (same `key`) are updated in
        place, so an open section stays open across refreshes; a different
        card starts with every section collapsed."""
        self._clear_legacy_sections()
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
            self.mount(existing, before=self._topology_panel)
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

    def _clear_legacy_sections(self) -> None:
        """Drop the env/health collapsibles `update_details` mounts."""
        for name in ("_env_collapsible", "_health_collapsible"):
            if (old := getattr(self, name)) is not None:
                old.remove()
                setattr(self, name, None)

    def update_details(
        self,
        title: str,
        sections: dict[str, dict],
        env_text: str | None = None,
        env_masked: bool = True,
        health_log: str | None = None,
    ) -> None:
        """Update the details panel and its optional collapsible sections.

        The main panel displays fields grouped into named sections. `sections`
        maps each section heading to a dictionary of field names and values.
        A falsy heading (``""``) suppresses the section header, which is useful
        for rendering an ungrouped set of fields.
        """
        safe_title = title if isinstance(title, SafeMarkup) else escape(title)
        self._leave_card()

        table = Table(show_header=False, expand=True, box=None, pad_edge=False)
        table.add_column("Property", style="cyan", justify="left")
        table.add_column("Value", ratio=1)
        for i, (heading, fields) in enumerate(sections.items()):
            if heading:
                if i:
                    table.add_row("", "")
                table.add_row(f"[b dim]{escape(heading.upper())}[/b dim]", "")
            for key, value in fields.items():
                safe_value = (
                    str(value) if isinstance(value, SafeMarkup) else escape(str(value))
                )
                table.add_row(f"[b]{key}[/b]", safe_value)

        self.border_title = safe_title
        self._panel.update(table)

        if self._env_collapsible is not None:
            self._env_collapsible.remove()
            self._env_collapsible = None
        if self._health_collapsible is not None:
            self._health_collapsible.remove()
            self._health_collapsible = None

        if env_text:
            env_static = Static(escape(env_text), id="env-content")
            env_static.styles.padding = (1, 2)
            suffix = "masked — R to reveal" if env_masked else "revealed — R to mask"
            self._env_collapsible = Collapsible(
                env_static,
                title=f"Environment Variables ({suffix})",
                collapsed=True,
            )
            self.mount(self._env_collapsible)

        if health_log:
            health_static = Static(escape(health_log))
            health_static.styles.padding = (1, 2)
            self._health_collapsible = Collapsible(
                health_static, title="Health checks (recent)", collapsed=True
            )
            self.mount(self._health_collapsible)

    def _leave_card(self) -> None:
        self._card_key = None
        self._remove_sections(set())
        self.border_subtitle = ""

    def clear_details(self) -> None:
        self.border_title = ""
        self._leave_card()
        self._panel.update("Select an item to view details.")
        self._topology_panel.update("")
        if self._env_collapsible is not None:
            self._env_collapsible.remove()
            self._env_collapsible = None
        if self._health_collapsible is not None:
            self._health_collapsible.remove()
            self._health_collapsible = None
