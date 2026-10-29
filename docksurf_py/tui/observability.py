"""
observability.py — Live observability mixin.

LiveStatsController streams real-time resource usage for the currently-focused
running container into the detail pane, and (system df) is wired here too. It's
a mixin composed into DockSurfApp via MRO, mirroring search.py's structure.
"""

import logging
import threading
import time
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.timer import Timer
from textual.widgets import TabbedContent

from docksurf_py.core.docker import StatsStream
from docksurf_py.core.models import Container, ContainerStats, ContainerTop, SystemDf
from docksurf_py.core.render.observability import (
    render_df,
    render_live,
)
from docksurf_py.core.render.palette import fg
from docksurf_py.core.types import TabID
from docksurf_py.tui.ids import DETAIL_PANE_ID, LIVE_PANEL_ID
from docksurf_py.tui.widgets import DetailPane, LivePanel, SystemDfScreen

if TYPE_CHECKING:
    from docksurf_py.tui.app import AppContext

    _Base = AppContext
else:
    # Real runtime base is `object` — `AppContext` only exists for mypy to
    # check these mixins' bodies against; see app.py's `AppContext` docstring.
    _Base = object

logger = logging.getLogger(__name__)

# How long the cursor must rest before the side panels retarget to its row.
SIDE_RETARGET_DELAY = 0.3


class LiveStatsController(_Base):
    """Streams stats for the focused running container; owns the `w` df screen.

    Follows the `LogPane` streaming pattern — a `LiveTargets.generation` counter plus
    a daemon thread marshalling samples back via `call_from_thread` — but keyed
    to the current selection rather than a toggle. `_sync_stats()` is idempotent:
    it only (re)starts the stream when the focused target actually changes, so a
    periodic/event refresh that re-selects the same container doesn't restart it.
    """

    # Which container stats/top follow is core's `LiveTargets` (`self._live`,
    # created by `DockSurfApp.__init__`); the stream handle and threads are
    # this mixin's.
    _stats_stream: StatsStream | None = None

    # Pending debounce timer for `_schedule_side_retarget`.
    _side_retarget_timer: Timer | None = None

    def _schedule_side_retarget(self) -> None:
        """Retarget the side panels once the cursor has been still for
        `SIDE_RETARGET_DELAY`; every call restarts the wait."""
        if self._side_retarget_timer is not None:
            self._side_retarget_timer.stop()
        self._side_retarget_timer = self.set_timer(
            SIDE_RETARGET_DELAY, self._retarget_side
        )

    def _retarget_side(self) -> None:
        """Point everything that follows the cursor at the focused row now:
        the stats stream, the `top` list and the lazily fetched details."""
        timer, self._side_retarget_timer = self._side_retarget_timer, None
        if timer is not None:
            timer.stop()
        if not self.is_running:
            return
        self._sync_stats()
        self._sync_top()
        self._sync_image_platform()
        self._sync_image_layers()
        self._sync_container_detail()
        self._sync_side_logs()

    def _sync_stats(self) -> None:
        item = None
        # No stream while the side column is hidden (`z`): nothing shows it.
        if (
            self.query_one(TabbedContent).active == TabID.CONTAINERS
            and not self._view.side_hidden
        ):
            item = self._get_focused_resource(TabID.CONTAINERS)
        target = self._live.stats_target_for(item)
        if not self._live.retarget_stats(target):
            return

        self._stop_stats_stream()
        panel = self.query_one(f"#{LIVE_PANEL_ID}", LivePanel)
        if target is None:
            panel.clear_live()
            panel.display = False
            return
        panel.display = not self._view.logs_full
        panel.show_message(fg(self._view.palette, "muted", "collecting stats…"))
        assert isinstance(item, Container)
        self._start_stats_stream(target, item.name)

    def _start_stats_stream(self, container_id: str, name: str) -> None:
        generation = self._live.next_generation()
        stream = self.docker.stream_stats(container_id)
        self._stats_stream = stream
        threading.Thread(
            target=self._pump_stats, args=(generation, stream, name), daemon=True
        ).start()

    def _pump_stats(self, generation: int, stream: StatsStream, name: str) -> None:
        for stats in stream:
            if not self._live.is_current(generation):
                break
            try:
                self.call_from_thread(
                    self._render_stats_sample, generation, stats, name
                )
            except Exception:
                break

    def _render_stats_sample(
        self, generation: int, stats: ContainerStats, name: str
    ) -> None:
        # A late sample from a superseded stream must not overwrite the panel.
        if not self._live.is_current(generation):
            return
        history = self._live.history
        history.push(stats, time.monotonic())
        palette = self._view.palette
        self.query_one(f"#{LIVE_PANEL_ID}", LivePanel).show_live(
            lambda width: render_live(history, palette, width)
        )

    def _stop_stats_stream(self) -> None:
        self._live.next_generation()  # invalidate any in-flight pump thread
        if self._stats_stream is not None:
            self._stats_stream.stop()
            self._stats_stream = None

    def stop_stats(self) -> None:
        """Stop streaming entirely (app teardown)."""
        if self._side_retarget_timer is not None:
            self._side_retarget_timer.stop()
            self._side_retarget_timer = None
        self._live.retarget_stats(None)
        self._stop_stats_stream()
        self._live.clear_top()

    @work(thread=True)
    def action_system_df(self) -> None:
        self.call_from_thread(self.notify, "Computing disk usage…")
        df: SystemDf = self.docker.system_df()
        if not df.entries and not self.docker.is_connected:
            self.call_from_thread(
                self.notify, "Could not compute disk usage", severity="error"
            )
            return
        self.call_from_thread(self._apply_system_df, df)
        self.call_from_thread(self.push_screen, SystemDfScreen(render_df(df)))

    def _sync_system_df(self) -> None:
        """Fetch `docker system df` for the Images/Volumes disk figures if the
        cache wants it (first use, older than a minute, or flagged by events)."""
        if self._details.want_system_df(time.monotonic()):
            self._fetch_system_df()

    @work(thread=True)
    def _fetch_system_df(self) -> None:
        df: SystemDf = self.docker.system_df()
        self.call_from_thread(self._apply_system_df, df)

    def _apply_system_df(self, df: SystemDf | None) -> None:
        if self._details.store_system_df(df, time.monotonic()):
            self._repaint_toolbar_line()

    # --- `t`: on-demand running-process snapshot ---

    def _sync_top(self) -> None:
        """Invalidate a stale process list when the selection moves on.

        Unlike stats, `top` is on-demand (`t` toggles/fetches it), so this
        never re-fetches automatically — it only forgets the list when the
        focused container id no longer matches whatever it was fetched for.
        """
        item = None
        if self.query_one(TabbedContent).active == TabID.CONTAINERS:
            item = self._get_focused_resource(TabID.CONTAINERS)
        focused_id = item.id if isinstance(item, Container) else None

        # The card for the new selection is already drawn without the old
        # container's processes; dropping them is all that is left to do.
        self._live.drop_stale_top(focused_id)

    def action_container_top(self) -> None:
        if self.query_one(TabbedContent).active != TabID.CONTAINERS:
            self.notify(
                "Switch to the Containers tab and select a container",
                severity="warning",
            )
            return
        c = self._get_focused_container()
        if c is None:
            self.notify(
                "Switch to the Containers tab and select a container",
                severity="warning",
            )
            return
        if not c.running:
            self.notify(f"{escape(c.name)} is not running", severity="warning")
            return

        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        if not self._live.toggle_top(c.id):
            # Was already showing this container's processes — toggled off.
            self._refresh_detail_card()
            pane.collapse_section("top")
            return

        self._refresh_detail_card()
        pane.expand_section("top")
        self._fetch_top(c.id, c.name)

    @work(thread=True)
    def _fetch_top(self, container_id: str, name: str) -> None:
        top = self.docker.container_top(container_id)
        self.call_from_thread(self._render_top_result, container_id, name, top)

    def _render_top_result(
        self, container_id: str, name: str, top: ContainerTop | None
    ) -> None:
        # The selection may have moved (or `t` toggled off) since the fetch
        # started — a stale result must not overwrite what's shown now.
        if not self._live.accepts_top_result(container_id):
            return
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        if top is None:
            self._live.clear_top()
            self._refresh_detail_card()
            pane.collapse_section("top")
            self.notify(
                f"Could not fetch processes for {escape(name)}", severity="error"
            )
            return
        self._live.store_top(container_id, top)
        self._refresh_detail_card()
