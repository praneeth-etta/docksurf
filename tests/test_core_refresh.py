"""Unit tests for core/state/refresh — refresh bookkeeping, connection
transitions, noise filtering, and the `docker events` watch loop. Plain
pytest, no Textual app."""

import threading
import unittest

from docksurf_py.core.connection import ConnectionState, ConnectionStatus
from docksurf_py.core.state.refresh import (
    EventWatcher,
    RefreshCoordinator,
    is_noise_event,
)


def _state(status: ConnectionStatus) -> ConnectionState:
    return ConnectionState(
        status=status, message=status.value, hint="", context="default", host=""
    )


class RefreshCoordinatorTests(unittest.TestCase):
    def test_second_begin_is_refused_until_finish(self) -> None:
        refresh = RefreshCoordinator()
        self.assertTrue(refresh.begin())
        self.assertFalse(refresh.begin())
        self.assertFalse(refresh.finish())
        self.assertTrue(refresh.begin())

    def test_refused_manual_refresh_is_not_queued(self) -> None:
        refresh = RefreshCoordinator()
        refresh.begin()
        refresh.begin()
        self.assertFalse(refresh.finish())

    def test_events_while_idle_start_a_refresh(self) -> None:
        self.assertTrue(RefreshCoordinator().events_settled())

    def test_events_mid_refresh_queue_exactly_one_rerun(self) -> None:
        refresh = RefreshCoordinator()
        refresh.begin()
        self.assertFalse(refresh.events_settled())
        self.assertFalse(refresh.events_settled())
        self.assertTrue(refresh.finish())
        refresh.begin()
        self.assertFalse(refresh.finish())


class ConnectionChangedTests(unittest.TestCase):
    def test_first_connect_is_a_change_but_not_a_reconnect(self) -> None:
        change = RefreshCoordinator().connection_changed(
            _state(ConnectionStatus.CONNECTED)
        )
        assert change is not None
        self.assertTrue(change.connected)
        self.assertFalse(change.reconnected)

    def test_repeated_status_is_not_a_change(self) -> None:
        refresh = RefreshCoordinator()
        down = _state(ConnectionStatus.DAEMON_UNAVAILABLE)
        self.assertIsNotNone(refresh.connection_changed(down))
        self.assertIsNone(refresh.connection_changed(down))

    def test_recovery_from_failure_is_a_reconnect(self) -> None:
        refresh = RefreshCoordinator()
        refresh.connection_changed(_state(ConnectionStatus.CONNECTED))
        drop = refresh.connection_changed(_state(ConnectionStatus.DAEMON_UNAVAILABLE))
        assert drop is not None
        self.assertFalse(drop.connected)
        back = refresh.connection_changed(_state(ConnectionStatus.CONNECTED))
        assert back is not None
        self.assertTrue(back.reconnected)


class NoiseEventTests(unittest.TestCase):
    def test_prefix_match_ignores_command_suffix(self) -> None:
        self.assertTrue(is_noise_event("exec_start: /bin/sh -c echo"))
        self.assertTrue(is_noise_event("health_status: unhealthy"))
        self.assertFalse(is_noise_event("start"))
        self.assertFalse(is_noise_event(""))


class _Stream:
    def __init__(self, events: list[dict], error: Exception | None = None) -> None:
        self._events = events
        self.error = error
        self.stopped = False

    def __iter__(self):
        return iter(self._events)

    def stop(self) -> None:
        self.stopped = True


class _FakeDocker:
    """Minimal `DockerService` surface `EventWatcher` touches."""

    def __init__(self, streams: list[_Stream], reconnect_after: int = 0) -> None:
        self._streams = streams
        self._reconnect_after = reconnect_after
        self._attempts = 0
        self.is_connected = True
        self.connection = _state(ConnectionStatus.CONNECTED)
        self.calls: list[str] = []

    def stream_events(self) -> _Stream:
        return self._streams.pop(0) if self._streams else _Stream([])

    def mark_disconnected(self, exc: Exception) -> None:
        self.calls.append("mark_disconnected")
        self.is_connected = False
        self.connection = _state(ConnectionStatus.DAEMON_UNAVAILABLE)

    def ensure_connected(self) -> ConnectionState:
        self._attempts += 1
        self.calls.append("ensure_connected")
        if self._attempts > self._reconnect_after:
            self.is_connected = True
            self.connection = _state(ConnectionStatus.CONNECTED)
        return self.connection


def _run(watcher: EventWatcher, until: threading.Event) -> None:
    thread = threading.Thread(target=watcher.run, daemon=True)
    thread.start()
    until.wait(timeout=2)
    watcher.stop()
    thread.join(timeout=2)
    assert not thread.is_alive(), "watcher did not stop"


class EventWatcherTests(unittest.TestCase):
    def test_reports_real_events_and_skips_noise(self) -> None:
        docker = _FakeDocker(
            [_Stream([{"Action": "start"}, {"Action": "exec_die"}, {"Action": "die"}])]
        )
        events: list[int] = []
        done = threading.Event()

        def on_event() -> None:
            events.append(1)
            if len(events) == 2:
                done.set()

        watcher = EventWatcher(
            docker,  # type: ignore[arg-type]
            on_event=on_event,
            on_connection=lambda s: None,
            on_reconnected=lambda: None,
            retry_seconds=0.01,
        )
        _run(watcher, done)
        self.assertEqual(len(events), 2)

    def test_stream_error_marks_disconnected_then_reconnects(self) -> None:
        docker = _FakeDocker(
            [_Stream([], error=ConnectionError("gone"))], reconnect_after=1
        )
        statuses: list[ConnectionStatus] = []
        reconnected = threading.Event()
        watcher = EventWatcher(
            docker,  # type: ignore[arg-type]
            on_event=lambda: None,
            on_connection=lambda s: statuses.append(s.status),
            on_reconnected=reconnected.set,
            retry_seconds=0.01,
        )
        _run(watcher, reconnected)
        self.assertTrue(reconnected.is_set())
        self.assertEqual(
            docker.calls[:3], ["mark_disconnected"] + ["ensure_connected"] * 2
        )
        self.assertEqual(statuses[0], ConnectionStatus.DAEMON_UNAVAILABLE)
        self.assertIn(ConnectionStatus.CONNECTED, statuses)

    def test_callback_error_in_event_loop_is_contained(self) -> None:
        docker = _FakeDocker([_Stream([{"Action": "start"}])])
        reported = threading.Event()

        def boom() -> None:
            raise RuntimeError("UI gone")

        watcher = EventWatcher(
            docker,  # type: ignore[arg-type]
            on_event=boom,
            on_connection=lambda s: reported.set(),
            on_reconnected=lambda: None,
            retry_seconds=0.01,
        )
        with self.assertLogs("docksurf_py.core.state.refresh", level="ERROR"):
            _run(watcher, reported)
        self.assertTrue(reported.is_set())

    def test_stop_stops_current_stream(self) -> None:
        stream = _Stream([])
        docker = _FakeDocker([stream])
        started = threading.Event()
        watcher = EventWatcher(
            docker,  # type: ignore[arg-type]
            on_event=lambda: None,
            on_connection=lambda s: started.set(),
            on_reconnected=lambda: None,
            retry_seconds=5.0,
        )
        thread = threading.Thread(target=watcher.run, daemon=True)
        thread.start()
        started.wait(timeout=2)
        watcher.stop()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(stream.stopped)
