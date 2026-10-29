"""Detail-pane state: lazily fetched per-resource details and which container
the live stats stream / process list currently follow.

`DetailCache` holds on-demand data the snapshot doesn't carry (volume sizes,
image architectures, container env/health detail) and de-duplicates fetches
already in flight. `LiveTargets` decides when the stats stream must restart
and when a `top` result is stale. Both only answer questions — the UI layer
does the fetching, streaming and rendering.
"""

from typing import Any

from docksurf_py.core.models import (
    ComposeProject,
    Container,
    ContainerDetail,
    ContainerTop,
    ImagePlatform,
    SystemDf,
)
from docksurf_py.core.state.stats import StatsHistory

# `docker system df` can take seconds, so it is cached and refetched sparingly.
SYSTEM_DF_MAX_AGE = 60.0
# A stale flag (an image/volume event) never forces a refetch sooner than this.
SYSTEM_DF_MIN_AGE = 5.0


class DetailCache:
    def __init__(self) -> None:
        # On-demand per-volume disk sizes (the `b` action), keyed by name.
        self.volume_sizes: dict[str, int] = {}
        # Per-image Architecture/Os, keyed by image id; fetched once per image.
        self.image_platforms: dict[str, ImagePlatform] = {}
        # Container env/health/start/restart detail, keyed by container id.
        # Refetched on selection, but kept so the pane shows last-known values.
        self.container_details: dict[str, ContainerDetail] = {}
        # Whether secret-looking env values are shown unmasked (`R`).
        self.reveal_secrets = False
        # Last `docker system df` result and the monotonic time it was stored.
        self.system_df: SystemDf | None = None
        self.system_df_at: float = 0.0
        self._system_df_stale = False
        self._system_df_in_flight = False
        self._detail_in_flight: str | None = None
        self._platform_in_flight: str | None = None

    # --- Volume sizes --------------------------------------------------------

    def set_volume_sizes(self, sizes: dict[str, int]) -> None:
        self.volume_sizes = sizes

    def volume_size(self, name: str) -> int | None:
        return self.volume_sizes.get(name)

    # --- System df -----------------------------------------------------------

    def want_system_df(self, now: float, max_age: float = SYSTEM_DF_MAX_AGE) -> bool:
        """True (and marks it in flight) if `docker system df` should be fetched
        now: never fetched, older than `max_age`, or flagged stale by an event
        and at least `SYSTEM_DF_MIN_AGE` old. Never while one is in flight."""
        if self._system_df_in_flight:
            return False
        age = now - self.system_df_at
        if (
            self.system_df is None
            or age >= max_age
            or (self._system_df_stale and age >= SYSTEM_DF_MIN_AGE)
        ):
            self._system_df_in_flight = True
            return True
        return False

    def mark_system_df_stale(self) -> None:
        """An image/volume-changing event arrived; refetch on next tab entry."""
        self._system_df_stale = True

    def store_system_df(self, df: SystemDf | None, now: float) -> bool:
        """Cache a fetched result; True if it differs from what was cached.

        A failed fetch (`None`) keeps the last known value and clears the
        in-flight flag. An empty result is treated as a failure too, since
        `DockerClient.system_df` returns empty entries when it can't compute.
        """
        self._system_df_in_flight = False
        if df is None or not df.entries:
            return False
        changed = df != self.system_df
        self.system_df = df
        self.system_df_at = now
        self._system_df_stale = False
        return changed

    # --- Image platform ------------------------------------------------------

    def want_platform(self, image_id: str | None) -> bool:
        """True (and marks it in flight) if `image_id`'s platform should be
        fetched now: not cached and not already being fetched."""
        if image_id is None or image_id in self.image_platforms:
            return False
        if image_id == self._platform_in_flight:
            return False
        self._platform_in_flight = image_id
        return True

    def store_platform(self, image_id: str, platform: ImagePlatform | None) -> None:
        """Cache a fetched platform; a failed fetch (`None`) is cached as
        `unknown` so it isn't retried on every highlight."""
        self.image_platforms[image_id] = platform or ImagePlatform("unknown", "unknown")
        if self._platform_in_flight == image_id:
            self._platform_in_flight = None

    def platform(self, image_id: str) -> ImagePlatform | None:
        """Cached platform, or None while it's still unknown."""
        return self.image_platforms.get(image_id)

    # --- Container detail ----------------------------------------------------

    def want_container_detail(self, container_id: str | None) -> bool:
        """True (and marks it in flight) unless no container is focused or
        this one's detail is already being fetched."""
        if container_id is None or container_id == self._detail_in_flight:
            return False
        self._detail_in_flight = container_id
        return True

    def store_container_detail(
        self, container_id: str, detail: ContainerDetail | None
    ) -> bool:
        """Cache a fetched detail; True if it differs from what was cached
        (the card needs repainting). A failed fetch (`None`) keeps the last
        known value and changes nothing."""
        changed = (
            detail is not None and self.container_details.get(container_id) != detail
        )
        if detail is not None:
            self.container_details[container_id] = detail
        if self._detail_in_flight == container_id:
            self._detail_in_flight = None
        return changed

    def container_detail(self, container_id: str) -> ContainerDetail | None:
        return self.container_details.get(container_id)

    # --- Secrets -------------------------------------------------------------

    def toggle_reveal_secrets(self) -> bool:
        self.reveal_secrets = not self.reveal_secrets
        return self.reveal_secrets


class LiveTargets:
    """Which container the stats stream, the `top` panel and the side logs follow.

    `generation` invalidates superseded stats streams: every (re)start or stop
    bumps it, and a sample is only shown if it carries the current one.
    """

    def __init__(self) -> None:
        self.stats_target: str | None = None
        # Samples for `stats_target`; reset whenever the target changes.
        self.history = StatsHistory()
        # What the side log panel shows: a container id, `project:<name>` for a
        # Compose header row, or None. Reloaded only when this changes.
        self.log_target: str | None = None
        self.generation = 0
        self.top_target: str | None = None
        # The last process snapshot fetched for `top_target`, so the detail
        # card can show it on every re-render until the selection moves on.
        self.top_result: ContainerTop | None = None

    # --- Stats stream --------------------------------------------------------

    @staticmethod
    def stats_target_for(item: Any) -> str | None:
        """Stats follow the focused container only while it is running."""
        return item.id if isinstance(item, Container) and item.running else None

    def retarget_stats(self, target: str | None) -> bool:
        """Point stats at `target`; False if it already was (no restart)."""
        if target == self.stats_target:
            return False
        self.stats_target = target
        self.history.reset()
        return True

    def next_generation(self) -> int:
        self.generation += 1
        return self.generation

    def is_current(self, generation: int) -> bool:
        return generation == self.generation

    # --- Side logs -----------------------------------------------------------

    @staticmethod
    def log_target_for(item: Any) -> str | None:
        """Logs follow any focused container (a stopped one just loads its last
        lines) and a Compose header row's whole project."""
        if isinstance(item, Container):
            return item.id
        if isinstance(item, ComposeProject):
            return f"project:{item.name}"
        return None

    def retarget_logs(self, target: str | None) -> bool:
        """Point the side logs at `target`; False if they already were (no reload)."""
        if target == self.log_target:
            return False
        self.log_target = target
        return True

    # --- Process list (`t`) --------------------------------------------------

    def drop_stale_top(self, focused_id: str | None) -> bool:
        """Forget the process list if the focus moved off its container.
        True if it was dropped (the panel should be cleared)."""
        if self.top_target is not None and focused_id != self.top_target:
            self.clear_top()
            return True
        return False

    def toggle_top(self, container_id: str) -> bool:
        """`t` on `container_id`: True to fetch and show, False to hide."""
        if self.top_target == container_id:
            self.clear_top()
            return False
        self.top_result = None
        self.top_target = container_id
        return True

    def accepts_top_result(self, container_id: str) -> bool:
        """Whether a finished fetch for `container_id` is still wanted."""
        return self.top_target == container_id

    def store_top(self, container_id: str, top: ContainerTop) -> bool:
        """Keep a fetched process list; False (dropped) if it is no longer wanted."""
        if not self.accepts_top_result(container_id):
            return False
        self.top_result = top
        return True

    def top_for(self, container_id: str) -> ContainerTop | None:
        """The stored process list if it belongs to `container_id`."""
        return self.top_result if self.top_target == container_id else None

    def clear_top(self) -> None:
        self.top_target = None
        self.top_result = None
