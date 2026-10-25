"""Refresh scheduling and the `docker events` watch loop.

`RefreshCoordinator` decides whether a refresh may start, whether one more is
owed after it finishes, and which connection changes deserve a notice.
`EventWatcher` runs the blocking `docker events` loop (including the
reconnect retry) and reports through callbacks. Neither starts threads or
timers — the UI layer owns those and calls in.
"""

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from docksurf_py.core.connection import ConnectionState, ConnectionStatus
from docksurf_py.core.docker import EventStream
from docksurf_py.core.service import DockerService

logger = logging.getLogger(__name__)

# Docker event prefixes that don't change the rendered state and would only
# trigger unnecessary refreshes. Exec events include a command after ':', so
# matching is done on the action prefix.
EVENT_NOISE = frozenset(
    {
        "exec_create",
        "exec_start",
        "exec_die",
        "exec_detach",
        "top",
        "health_status",
    }
)

# Pause between `docker events` reconnect attempts, and between stream
# restarts while connected.
RETRY_SECONDS = 2.0


def is_noise_event(action: str) -> bool:
    return action.split(":", 1)[0].strip() in EVENT_NOISE


@dataclass(frozen=True)
class ConnectionChange:
    """A connection status transition worth showing to the user."""

    state: ConnectionState
    connected: bool
    # True when coming back from a failure (not on the very first connect),
    # i.e. when a "Reconnected" notice is due.
    reconnected: bool


class RefreshCoordinator:
    """Refresh bookkeeping: at most one fetch at a time, one catch-up run for
    events that arrive mid-fetch, and connection-transition detection."""

    def __init__(self) -> None:
        self.in_progress = False
        self.pending = False
        # Last connection status reported, so notices fire on real changes
        # only — not on every reconnect retry.
        self.last_status = ConnectionStatus.NOT_CONNECTED

    def begin(self) -> bool:
        """Claim the refresh slot. False if a refresh is already running; the
        request is dropped, not queued (see `events_settled` for queuing)."""
        if self.in_progress:
            return False
        self.in_progress = True
        return True

    def finish(self) -> bool:
        """Release the slot. True if events arrived mid-refresh and one more
        refresh should run so the final state lands."""
        self.in_progress = False
        if self.pending:
            self.pending = False
            return True
        return False

    def events_settled(self) -> bool:
        """A debounced burst of Docker events ended. True if a refresh should
        start now; if one is already running, remember to run once more."""
        if self.in_progress:
            self.pending = True
            return False
        return True

    def connection_changed(self, state: ConnectionState) -> ConnectionChange | None:
        """None unless `state.status` differs from the last one reported."""
        previous = self.last_status
        if state.status == previous:
            return None
        was_down = previous not in (
            ConnectionStatus.NOT_CONNECTED,
            ConnectionStatus.CONNECTED,
        )
        self.last_status = state.status
        connected = state.status == ConnectionStatus.CONNECTED
        return ConnectionChange(
            state=state, connected=connected, reconnected=connected and was_down
        )


class EventWatcher:
    """Watches `docker events` and doubles as the reconnect loop.

    `run()` blocks until `stop()`; call it from a background thread. Callbacks
    run on that thread — the caller marshals them to the UI thread:
    `on_event` for each non-noise event, `on_connection` with the current
    connection state after every stream ends and every reconnect attempt,
    `on_reconnected` once a reconnect attempt succeeds.
    """

    def __init__(
        self,
        docker: DockerService,
        *,
        on_event: Callable[[], None],
        on_connection: Callable[[ConnectionState], None],
        on_reconnected: Callable[[], None],
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self._docker = docker
        self._on_event = on_event
        self._on_connection = on_connection
        self._on_reconnected = on_reconnected
        self._retry_seconds = retry_seconds
        self._stop = threading.Event()
        self._stream: EventStream | None = None

    def run(self) -> None:
        stop = self._stop
        while not stop.is_set():
            stream = self._docker.stream_events()
            self._stream = stream
            try:
                for event in stream:
                    if stop.is_set():
                        break
                    if is_noise_event(event.get("Action", "")):
                        continue
                    self._on_event()
            except Exception:
                # Usually a callback racing app shutdown, not a Docker failure.
                # `EventStream` records daemon errors in `.error` instead of
                # propagating them.
                logger.exception("Event listener error")

            if stream.error is not None:
                self._docker.mark_disconnected(stream.error)
            self._on_connection(self._docker.connection)

            if not self._docker.is_connected:
                stop.wait(self._retry_seconds)
                if stop.is_set():
                    break
                state = self._docker.ensure_connected()
                self._on_connection(state)
                if state.status == ConnectionStatus.CONNECTED:
                    self._on_reconnected()
                continue

            stop.wait(self._retry_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            self._stream.stop()
