"""actions/ — Container management, Compose, and resource-deletion mixins.

Facade package: re-exports everything the single-file `actions.py` used to
expose, so callers keep using `from docksurf_py.tui.actions import X` unchanged.
"""

from docksurf_py.tui.actions.clipboard import ClipboardHandler
from docksurf_py.tui.actions.compose import ComposeActionHandler
from docksurf_py.tui.actions.container import ContainerActionHandler, _open_in_browser
from docksurf_py.tui.actions.context import ContextActionHandler
from docksurf_py.tui.actions.deletion import ResourceDeletionHandler
from docksurf_py.tui.actions.images import ImageActionHandler
from docksurf_py.tui.actions.inspect import InspectHandler
from docksurf_py.tui.actions.networks import NetworkActionHandler
from docksurf_py.tui.actions.prune import PruneHandler
from docksurf_py.tui.actions.selection import SelectionHandler
from docksurf_py.tui.actions.volumes import VolumeActionHandler

__all__ = [
    "ClipboardHandler",
    "ComposeActionHandler",
    "ContainerActionHandler",
    "ContextActionHandler",
    "ImageActionHandler",
    "InspectHandler",
    "NetworkActionHandler",
    "PruneHandler",
    "ResourceDeletionHandler",
    "SelectionHandler",
    "VolumeActionHandler",
    "_open_in_browser",
]
