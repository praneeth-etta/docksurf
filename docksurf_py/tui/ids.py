from enum import StrEnum

from docksurf_py.core.types import SafeMarkup, TabID


# Table IDs
class TableID(StrEnum):
    CONTAINERS = "table-containers"
    IMAGES = "table-images"
    VOLUMES = "table-volumes"
    NETWORKS = "table-networks"


# Generic widget IDs
LOG_PANE_ID = "log-pane"
LOG_PANE_VIEW_ID = "log-pane-view"
LOG_PANE_HEADER_ID = "log-pane-header"
LOG_PANE_TOOLBAR_ID = "log-pane-toolbar"
LOG_PANE_SEARCH_ID = "log-pane-search"
BTN_EXPAND_ID = "expand-btn"

# LogOptionsScreen widget IDs
LOG_OPTIONS_TAIL_ID = "log-options-tail"
LOG_OPTIONS_SINCE_ID = "log-options-since"
BTN_LOG_OPTIONS_OK_ID = "log-options-ok"
BTN_LOG_OPTIONS_CANCEL_ID = "log-options-cancel"

# InspectScreen widget IDs
INSPECT_VIEW_ID = "inspect-view"
INSPECT_SEARCH_ID = "inspect-search"
BTN_INSPECT_CLOSE_ID = "inspect-close"

# PullProgressScreen widget IDs
PULL_PROGRESS_VIEW_ID = "pull-progress-view"
BTN_PULL_PROGRESS_CLOSE_ID = "pull-progress-close"

# BuildProgressScreen widget IDs
BUILD_PROGRESS_VIEW_ID = "build-progress-view"
BTN_BUILD_PROGRESS_CLOSE_ID = "build-progress-close"

# LayerHistoryScreen widget IDs
BTN_LAYER_HISTORY_CLOSE_ID = "layer-history-close"

# ContainerPickerScreen widget IDs
PICKER_LIST_ID = "picker-list"
BTN_PICKER_CANCEL_ID = "picker-cancel"

# PruneScreen cancel button (target buttons use `PRUNE_TARGETS` in core.types)
BTN_PRUNE_CANCEL_ID = "prune-cancel"

DETAIL_PANE_ID = "detail-pane"
SEARCH_BAR_ID = "search-bar"
STATUS_BAR_ID = "status-bar"
MAIN_CONTAINER_ID = "main-container"
REFRESH_LOADING_ID = "refresh-loading"

# Confirm-dialog button IDs
BTN_CONFIRM_ID = "confirm"
BTN_CANCEL_ID = "cancel"
CONFIRM_FORCE_CHECKBOX_ID = "confirm-force"

# Empty-state placeholder IDs — one per resource table, shown instead of the
# table itself whenever it has zero rows (see TableRenderer._update_empty_state).
EMPTY_STATE_IDS: dict[TabID, str] = {
    TabID.CONTAINERS: "empty-containers",
    TabID.IMAGES: "empty-images",
    TabID.VOLUMES: "empty-volumes",
    TabID.NETWORKS: "empty-networks",
}

# Shown when a tab's table is genuinely empty (no active search, Docker
# connected) — a nudge toward the command that would populate it.
EMPTY_STATE_HINTS: dict[TabID, str] = {
    TabID.CONTAINERS: "No containers — try `docker run hello-world`",
    TabID.IMAGES: "No images — try `docker pull nginx`",
    TabID.VOLUMES: "No volumes — try `docker volume create my-volume`",
    TabID.NETWORKS: "No networks — try `docker network create my-network`",
}

# Persistent "Docker unreachable" banner, docked under the Header for as long
# as the connection stays down (the StatusBar segment + toast already exist
# but the toast expires after 12s — see
# SnapshotManager._maybe_notify_connection_change).
CONNECTION_BANNER_ID = "connection-banner"

# Small, always visible connected/disconnected readout docked under the Header
CONNECTION_INDICATOR_ID = "connection-indicator"

# PromptScreen button IDs
BTN_PROMPT_OK_ID = "prompt-ok"
BTN_PROMPT_CANCEL_ID = "prompt-cancel"


# Multi-select mark glyph — the leading-column cell for a marked row. Shared
# between renderer.py (row population) and actions.py (single-cell update on
# toggle) so both draw the same glyph without one importing the other.
MARK_GLYPH = SafeMarkup("[bold cyan]●[/]")
