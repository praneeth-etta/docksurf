"""TopBar — logo, clickable tabs with counts, context and connection state."""

from docksurf_py.tui.widgets.markup_bar import MarkupBar


class TopBar(MarkupBar):
    """Top line of the app; content comes from `core.render.chrome.top_bar`."""
