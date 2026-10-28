"""ToolbarLine — an optional one-line summary above the filter row (Images'
disk usage); content comes from a per-tab core builder."""

from docksurf_py.tui.widgets.markup_bar import MarkupBar


class ToolbarLine(MarkupBar):
    """Shown only for tabs that define a `ResourceEntry.toolbar_line`."""
