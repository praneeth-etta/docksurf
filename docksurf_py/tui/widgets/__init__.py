"""widgets/ — The View Layer.

All components here are intentionally "dumb":
  - They handle user input and screen updates.
  - No subprocess calls; all Docker I/O lives in docker/.
  - All string IDs come from constants.py.

Facade package: re-exports everything the single-file `widgets.py` used to
expose, so callers keep using `from docksurf_py.tui.widgets import X` unchanged.
"""

from docksurf_py.tui.widgets.bottom_bar import BottomBar
from docksurf_py.tui.widgets.build_progress_screen import BuildProgressScreen
from docksurf_py.tui.widgets.chip_bar import ChipBar
from docksurf_py.tui.widgets.container_picker_screen import ContainerPickerScreen
from docksurf_py.tui.widgets.detail_pane import DetailPane
from docksurf_py.tui.widgets.dialogs import ConfirmDialog, PromptField, PromptScreen
from docksurf_py.tui.widgets.help_screen import HelpScreen
from docksurf_py.tui.widgets.inspect_screen import InspectScreen
from docksurf_py.tui.widgets.layer_history_screen import LayerHistoryScreen
from docksurf_py.tui.widgets.log_pane import (
    LogOptionsScreen,
    LogPane,
    LogSource,
    SelectableRichLog,
)
from docksurf_py.tui.widgets.prune_screen import PruneScreen
from docksurf_py.tui.widgets.pull_progress_screen import PullProgressScreen
from docksurf_py.tui.widgets.search_bar import SearchBar
from docksurf_py.tui.widgets.system_df_screen import SystemDfScreen
from docksurf_py.tui.widgets.tables import (
    ContainerTable,
    ImageTable,
    NetworkTable,
    VolumeTable,
)
from docksurf_py.tui.widgets.top_bar import TopBar
from docksurf_py.tui.widgets.whale_screen import WhaleScreen

__all__ = [
    "ContainerTable",
    "ImageTable",
    "VolumeTable",
    "NetworkTable",
    "DetailPane",
    "ConfirmDialog",
    "PromptField",
    "PromptScreen",
    "LogSource",
    "LogPane",
    "SelectableRichLog",
    "LogOptionsScreen",
    "HelpScreen",
    "SystemDfScreen",
    "WhaleScreen",
    "InspectScreen",
    "PruneScreen",
    "PullProgressScreen",
    "BuildProgressScreen",
    "LayerHistoryScreen",
    "ContainerPickerScreen",
    "SearchBar",
    "BottomBar",
    "ChipBar",
    "TopBar",
]
