from dataclasses import dataclass
from enum import StrEnum


# Tab IDs — also the resource-kind keys for session state and per-tab registries
class TabID(StrEnum):
    CONTAINERS = "tab-containers"
    IMAGES = "tab-images"
    VOLUMES = "tab-volumes"
    NETWORKS = "tab-networks"


# Prune target keys shared between PruneScreen (widgets.py) and PruneHandler
# (actions.py) — dismissing PruneScreen with one of these selects the
# matching `DockerClient.prune_*` method. Also doubles as each target
# button's ID suffix ("prune-<target>").
PRUNE_TARGETS: tuple[str, ...] = (
    "containers",
    "images",
    "volumes",
    "networks",
    "system",
)


# Palette cycled per service in the aggregated (merged) project log view, so
# each service's lines are colour-coded like `docker compose logs -f`.
LOG_SERVICE_COLORS = (
    "cyan",
    "magenta",
    "green",
    "yellow",
    "blue",
    "bright_red",
    "bright_cyan",
    "bright_magenta",
)


@dataclass(frozen=True, slots=True)
class LogOptions:
    """Stream-shaping options for the log viewer.

    Only covers what changes *what the daemon sends* (so a change requires
    re-subscribing the stream). Display-only toggles (timestamps, wrap) live on
    the `LogPane` widget, since they never re-fetch. `tail=None` means "all";
    `since_seconds=0` means no `--since` window.
    """

    tail: int | None = 500
    since_seconds: int = 0


@dataclass(frozen=True, slots=True)
class LogLine:
    """One rendered log line, carrying everything the view needs to style it.

    Replaces the old flat `list[str]` buffer so timestamps and per-service
    (merged-project) labels survive from the stream layer to the renderer
    without pre-formatted-markup special cases. `text` is the message only
    (timestamp split out into `ts`). `stream` is `"stdout"` for container
    output (docker-py can't demux logs, so real lines are always stdout) and
    `"stderr"` for our own stream-error messages, which the view styles
    dim-red. `service`/`color` are set only for merged project streams.
    """

    text: str
    ts: str = ""
    stream: str = "stdout"
    service: str = ""
    color: str = ""


class SafeMarkup(str):
    """A str subclass that marks text as already-constructed Rich markup.

    Render helpers (DetailPane, _safe_row) will pass SafeMarkup through
    unchanged and escape every plain str they receive.
    """
