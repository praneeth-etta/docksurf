"""Characterization tests for refresh scheduling and connection notices.

Pins how `docker events`, manual refreshes and connection changes turn into
snapshot fetches and toasts today, so moving that logic out of the Textual
mixin (local/UI_SEPARATION_PLAN.md, Phase 3b) can be checked against it.
Events are fed through a queue-backed stand-in for `EventStream`, so they
travel the real listener path (noise filter, debounce, coalescing).
"""

import asyncio
import queue
import threading
import unittest
from unittest.mock import patch

from docksurf_py.core.connection import ConnectionState, ConnectionStatus
from docksurf_py.core.models import DockerSnapshot
from docksurf_py.tui.app import DockSurfApp
from tests.test_app import _CONNECTED_STATE, MockDockerService, wait_until

EMPTY = DockerSnapshot([], [], [], [])
_STOP = object()

# Longer than the 0.4s event debounce, so a quiet period really is quiet.
SETTLE = 0.8

_DOWN_STATE = ConnectionState(
    status=ConnectionStatus.DAEMON_UNAVAILABLE,
    message="Docker daemon is not running",
    hint="Start Docker Desktop, or run: sudo systemctl start docker",
    context="default",
    host="unix:///var/run/docker.sock",
)


class QueueEventStream:
    """`EventStream`-shaped: yields events pushed by the test until stopped."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self._queue: queue.Queue = queue.Queue()

    def push(self, *actions: str) -> None:
        for action in actions:
            self._queue.put({"Action": action})

    def __iter__(self):
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            yield item

    def stop(self) -> None:
        self._queue.put(_STOP)


class GatedFetch:
    """A fetch function that counts calls and can be held open by the test."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._gate = threading.Event()
        self._gate.set()

    def __call__(self) -> DockerSnapshot:
        with self._lock:
            self.calls += 1
        self._gate.wait(timeout=5)
        return EMPTY

    def hold(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    @property
    def count(self) -> int:
        with self._lock:
            return self.calls


def _app_with_events() -> tuple[
    DockSurfApp, MockDockerService, GatedFetch, QueueEventStream
]:
    fetch = GatedFetch()
    svc = MockDockerService(fetch)
    stream = QueueEventStream()
    svc.stream_events = lambda: stream  # type: ignore[method-assign]
    return DockSurfApp(docker=svc), svc, fetch, stream


async def _idle(app: DockSurfApp, pilot) -> None:
    await wait_until(lambda: not app._refresh_in_progress, timeout=3.0)
    await pilot.pause()


class EventDrivenRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_event_triggers_one_refresh(self) -> None:
        app, _, fetch, stream = _app_with_events()
        async with app.run_test() as pilot:
            await wait_until(lambda: fetch.count == 1)
            await _idle(app, pilot)

            stream.push("start")
            await wait_until(lambda: fetch.count == 2, timeout=3.0)
            await _idle(app, pilot)
            await asyncio.sleep(SETTLE)
            self.assertEqual(fetch.count, 2)

    async def test_burst_of_events_coalesces_into_one_refresh(self) -> None:
        app, _, fetch, stream = _app_with_events()
        async with app.run_test() as pilot:
            await wait_until(lambda: fetch.count == 1)
            await _idle(app, pilot)

            stream.push("create", "start", "attach", "start", "die")
            await wait_until(lambda: fetch.count == 2, timeout=3.0)
            await _idle(app, pilot)
            await asyncio.sleep(SETTLE)
            self.assertEqual(fetch.count, 2)

    async def test_noise_events_do_not_refresh(self) -> None:
        app, _, fetch, stream = _app_with_events()
        async with app.run_test() as pilot:
            await wait_until(lambda: fetch.count == 1)
            await _idle(app, pilot)

            stream.push(
                "exec_create: /bin/sh -c true",
                "exec_start: /bin/sh -c true",
                "exec_die",
                "health_status: healthy",
                "top",
            )
            await asyncio.sleep(SETTLE)
            self.assertEqual(fetch.count, 1)

    async def test_events_during_refresh_cause_exactly_one_rerun(self) -> None:
        app, _, fetch, stream = _app_with_events()
        async with app.run_test() as pilot:
            await wait_until(lambda: fetch.count == 1)
            await _idle(app, pilot)

            fetch.hold()
            stream.push("start")
            await wait_until(lambda: fetch.count == 2, timeout=3.0)
            self.assertTrue(app._refresh_in_progress)

            # Two separate debounced bursts land while the fetch is held.
            stream.push("die")
            await asyncio.sleep(SETTLE)
            stream.push("destroy")
            await asyncio.sleep(SETTLE)
            self.assertEqual(fetch.count, 2)

            fetch.release()
            await wait_until(lambda: fetch.count == 3, timeout=3.0)
            await _idle(app, pilot)
            await asyncio.sleep(SETTLE)
            self.assertEqual(fetch.count, 3)

    async def test_manual_refresh_during_refresh_is_dropped_not_queued(self) -> None:
        app, _, fetch, _ = _app_with_events()
        async with app.run_test() as pilot:
            await wait_until(lambda: fetch.count == 1)
            await _idle(app, pilot)

            fetch.hold()
            app.action_refresh()
            await wait_until(lambda: fetch.count == 2)
            app.action_refresh()
            await pilot.pause()
            fetch.release()
            await _idle(app, pilot)
            await asyncio.sleep(SETTLE)
            self.assertEqual(fetch.count, 2)


class ConnectionNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_connect_shows_no_reconnected_toast(self) -> None:
        app = DockSurfApp(docker=MockDockerService(lambda: EMPTY))
        with patch.object(app, "notify") as notify:
            async with app.run_test() as pilot:
                await _idle(app, pilot)
                await asyncio.sleep(SETTLE)
                messages = [c.args[0] for c in notify.call_args_list]
                self.assertNotIn("Reconnected to Docker", messages)

    async def test_drop_toasts_once_while_retrying_then_reconnect_toasts_once(
        self,
    ) -> None:
        svc = MockDockerService(lambda: EMPTY)
        up = threading.Event()

        def stream_events():
            stream = QueueEventStream()
            if not up.is_set():
                stream.error = ConnectionError("daemon down")
                stream.stop()  # ends at once, as a dropped stream does
            return stream

        def ensure_connected() -> ConnectionState:
            svc.calls.append(("ensure_connected",))
            if up.is_set():
                svc._connected = True
                svc.connection = _CONNECTED_STATE
            else:
                svc._connected = False
                svc.connection = _DOWN_STATE
            return svc.connection

        svc.stream_events = stream_events  # type: ignore[method-assign]
        svc.ensure_connected = ensure_connected  # type: ignore[method-assign]

        app = DockSurfApp(docker=svc)
        with patch.object(app, "notify") as notify:
            async with app.run_test() as pilot:
                # Two failed retry cycles (2s apart) while the daemon stays down.
                await wait_until(
                    lambda: svc.calls.count(("ensure_connected",)) >= 2, timeout=6.0
                )
                await pilot.pause()

                def errors() -> list[str]:
                    return [
                        c.args[0]
                        for c in notify.call_args_list
                        if c.kwargs.get("severity") == "error"
                    ]

                self.assertEqual(len(errors()), 1)
                self.assertIn("Docker daemon is not running", errors()[0])

                up.set()
                await wait_until(
                    lambda: any(
                        c.args and c.args[0] == "Reconnected to Docker"
                        for c in notify.call_args_list
                    ),
                    timeout=4.0,
                )
                await asyncio.sleep(SETTLE)
                reconnects = [
                    c
                    for c in notify.call_args_list
                    if c.args and c.args[0] == "Reconnected to Docker"
                ]
                self.assertEqual(len(reconnects), 1)
                self.assertEqual(len(errors()), 1)
