"""
observability.py — Live observability mixin.

LiveStatsController streams real-time resource usage for the currently-focused
running container into the detail pane, and (system df) is wired here too. It's
a mixin composed into DockSurfApp via MRO, mirroring search.py's structure.
"""

import logging
import threading
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.widgets import TabbedContent

from docksurf_py.core.docker import StatsStream
from docksurf_py.core.models import Container, ContainerStats, ContainerTop, SystemDf
from docksurf_py.core.render.observability import (
    render_df,
    render_stats,
    render_top,
)
from docksurf_py.core.types import SafeMarkup, TabID
from docksurf_py.tui.ids import DETAIL_PANE_ID
from docksurf_py.tui.widgets import DetailPane, SystemDfScreen

if TYPE_CHECKING:
    from docksurf_py.tui.app import AppContext

    _Base = AppContext
else:
    # Real runtime base is `object` — `AppContext` only exists for mypy to
    # check these mixins' bodies against; see app.py's `AppContext` docstring.
    _Base = object

logger = logging.getLogger(__name__)


class LiveStatsController(_Base):
    """Streams stats for the focused running container; owns the `w` df screen.

    Follows the `LogPane` streaming pattern — a `_stats_generation` counter plus
    a daemon thread marshalling samples back via `call_from_thread` — but keyed
    to the current selection rather than a toggle. `_sync_stats()` is idempotent:
    it only (re)starts the stream when the focused target actually changes, so a
    periodic/event refresh that re-selects the same container doesn't restart it.
    """

    _stats_target: str | None = None
    _stats_generation: int = 0
    _stats_stream: StatsStream | None = None
    _top_target: str | None = None

    def _sync_stats(self) -> None:
        item = None
        if self.query_one(TabbedContent).active == TabID.CONTAINERS:
            item = self._get_focused_resource(TabID.CONTAINERS)
        target = item.id if isinstance(item, Container) and item.running else None

        if target == self._stats_target:
            return
        self._stats_target = target

        self._stop_stats_stream()
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        if target is None:
            pane.clear_live_stats()
            return
        pane.update_live_stats(SafeMarkup("[dim]collecting stats…[/]"))
        assert isinstance(item, Container)
        self._start_stats_stream(target, item.name)

    def _start_stats_stream(self, container_id: str, name: str) -> None:
        self._stats_generation += 1
        generation = self._stats_generation
        stream = self.docker.stream_stats(container_id)
        self._stats_stream = stream
        threading.Thread(
            target=self._pump_stats, args=(generation, stream, name), daemon=True
        ).start()

    def _pump_stats(self, generation: int, stream: StatsStream, name: str) -> None:
        for stats in stream:
            if self._stats_generation != generation:
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
        # A late sample from a superseded stream must not overwrite the pane.
        if self._stats_generation != generation:
            return
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        pane.update_live_stats(render_stats(stats, name))

    def _stop_stats_stream(self) -> None:
        self._stats_generation += 1  # invalidate any in-flight pump thread
        if self._stats_stream is not None:
            self._stats_stream.stop()
            self._stats_stream = None

    def stop_stats(self) -> None:
        """Stop streaming entirely (app teardown)."""
        self._stats_target = None
        self._stop_stats_stream()
        self._top_target = None

    @work(thread=True)
    def action_system_df(self) -> None:
        self.call_from_thread(self.notify, "Computing disk usage…")
        df: SystemDf = self.docker.system_df()
        if not df.entries and not self.docker.is_connected:
            self.call_from_thread(
                self.notify, "Could not compute disk usage", severity="error"
            )
            return
        self.call_from_thread(self.push_screen, SystemDfScreen(render_df(df)))

    # --- `t`: on-demand running-process snapshot ---

    def _sync_top(self) -> None:
        """Invalidate a stale process list when the selection moves on.

        Unlike stats, `top` is on-demand (`t` toggles/fetches it), so this
        never re-fetches automatically — it only clears the panel when the
        focused container id no longer matches whatever it was fetched for.
        """
        item = None
        if self.query_one(TabbedContent).active == TabID.CONTAINERS:
            item = self._get_focused_resource(TabID.CONTAINERS)
        focused_id = item.id if isinstance(item, Container) else None

        if self._top_target is not None and focused_id != self._top_target:
            self._top_target = None
            self.query_one(f"#{DETAIL_PANE_ID}", DetailPane).clear_processes()

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

        if self._top_target == c.id:
            # Already showing this container's processes — toggle off.
            self._top_target = None
            self.query_one(f"#{DETAIL_PANE_ID}", DetailPane).clear_processes()
            return

        self._top_target = c.id
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        pane.update_processes(SafeMarkup("[dim]fetching processes…[/]"))
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
        if self._top_target != container_id:
            return
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        if top is None:
            self._top_target = None
            pane.clear_processes()
            self.notify(
                f"Could not fetch processes for {escape(name)}", severity="error"
            )
            return
        pane.update_processes(render_top(top, name))
