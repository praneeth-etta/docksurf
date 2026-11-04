"""App smoke tests: the few things only the real, running app can get wrong —
key bindings firing, modals and focus, resize, stream start/stop. Everything
else is covered by the core unit tests. Real key presses and clicks, not direct
action calls, so a dead `@on` decorator or a wrong binding fails here.
Assertions run inside the `run_test()` block (teardown resets live state)."""

import unittest
from dataclasses import replace

from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.config import Config
from docksurf_py.core.connection import ConnectionState
from docksurf_py.core.models import (
    DockerSnapshot,
    Image,
    Network,
    NetworkEndpoint,
    Volume,
)
from docksurf_py.core.state.view import LayoutMode
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    BOTTOM_BAR_ID,
    BTN_CANCEL_ID,
    BTN_CONFIRM_ID,
    LIST_PANEL_ID,
    SEARCH_BAR_ID,
    SIDE_COLUMN_ID,
    TOP_BAR_ID,
    TableID,
)
from docksurf_py.tui.widgets import (
    ConfirmDialog,
    HelpScreen,
    InspectScreen,
    PruneScreen,
)
from tests.helpers import (
    _CONNECTED_STATE,
    EMPTY_SNAPSHOT,
    FakeEventStream,
    MockDockerService,
    wait_until,
)
from tests.test_compose import make_container


def _snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[
            replace(make_container("web"), image_id="sha256:web"),
            replace(
                make_container("db", running=False),
                state="exited",
                status="Exited (0) 14 minutes ago",
                mounts=["pgdata"],
                networks=["backend"],
            ),
            make_container("solo"),
        ],
        images=[
            Image("sha256:web", "shop-web", "latest", 500, False, ["web"], ""),
            Image("sha256:idle", "idle", "1", 300, False, [], ""),
        ],
        volumes=[
            Volume("pgdata", "local", "/m", ["db"], {}),
            Volume("orphan", "local", "/m", [], {}),
        ],
        networks=[
            Network(
                "n1",
                "backend",
                "bridge",
                "",
                "",
                "local",
                [],
                endpoints=[NetworkEndpoint("db")],
            )
        ],
    )


def _app(**kw) -> tuple[DockSurfApp, MockDockerService]:
    svc = MockDockerService(_snapshot)
    return DockSurfApp(docker=svc, **kw), svc


async def _ready(app: DockSurfApp, pilot) -> DataTable:
    """Wait for the first snapshot and focus the Containers table."""
    await pilot.pause()
    await wait_until(lambda: bool(app._tables.rows.get(TabID.CONTAINERS)), 3.0)
    await pilot.pause()
    table = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
    table.focus()
    return table


def _move_to(app: DockSurfApp, table: DataTable, name: str) -> None:
    rows = app._tables.rows[TabID.CONTAINERS]
    table.move_cursor(row=next(i for i, r in enumerate(rows) if r.name == name))


def _has(app: DockSurfApp, screen_type: type) -> bool:
    return any(isinstance(s, screen_type) for s in app.screen_stack)


class BootTests(unittest.IsolatedAsyncioTestCase):
    async def test_boots_wide(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            self.assertEqual(app._view.layout, LayoutMode.WIDE)
            self.assertEqual(table.row_count, 3)
            self.assertTrue(app.query_one(f"#{SIDE_COLUMN_ID}").display)
            self.assertIn("docksurf", str(app.query_one(f"#{TOP_BAR_ID}").content))

    async def test_boots_compact(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            self.assertEqual(app._view.layout, LayoutMode.COMPACT)
            self.assertEqual(table.row_count, 3)
            self.assertFalse(app.query_one(f"#{SIDE_COLUMN_ID}").display)
            bar = str(app.query_one(f"#{BOTTOM_BAR_ID}").content)
            self.assertEqual(len(bar.split("\n")), 2)


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_number_keys_and_brackets_switch_every_tab(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            await _ready(app, pilot)
            tabs = app.query_one(TabbedContent)
            for key, tab in (
                ("2", TabID.IMAGES),
                ("3", TabID.VOLUMES),
                ("4", TabID.NETWORKS),
                ("1", TabID.CONTAINERS),
            ):
                await pilot.press(key)
                await wait_until(lambda: tabs.active == tab, 2.0)
                entry = app._resource_registry[tab]
                table = app.query_one(f"#{entry.table_id}", DataTable)
                await wait_until(lambda: table.row_count > 0, 2.0)
            await pilot.press("right_square_bracket")
            await wait_until(lambda: tabs.active == TabID.IMAGES, 2.0)
            await pilot.press("left_square_bracket")
            await wait_until(lambda: tabs.active == TabID.CONTAINERS, 2.0)

    async def test_filter_narrows_the_rows_and_escape_hands_focus_back(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            await pilot.press("slash")
            await pilot.press("w", "e", "b")
            await wait_until(lambda: table.row_count == 1, 2.0)
            await pilot.press("escape")
            await wait_until(lambda: table.has_focus, 2.0)
            self.assertEqual(table.row_count, 1)  # the query stays active
            search = app.query_one(f"#{SEARCH_BAR_ID}")
            search.value = ""
            await wait_until(lambda: table.row_count == 3, 2.0)

    async def test_theme_key_cycles_through_the_themes_and_back(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            seen = [app.theme]
            for _ in range(3):
                await pilot.press("M")
                await pilot.pause()
                seen.append(app.theme)
            self.assertEqual(seen[0], seen[3])
            self.assertEqual(len(set(seen[:3])), 3)
            self.assertEqual(table.row_count, 3)


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_s_stops_the_focused_container(self) -> None:
        app, svc = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            _move_to(app, table, "web")
            await pilot.pause()
            await pilot.press("s")
            await wait_until(lambda: ("stop_container", "web") in svc.calls, 3.0)

    async def test_delete_waits_for_confirmation(self) -> None:
        app, svc = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            _move_to(app, table, "db")
            await pilot.pause()

            await pilot.press("d")
            await wait_until(lambda: _has(app, ConfirmDialog), 2.0)
            await pilot.click(f"#{BTN_CANCEL_ID}")
            await wait_until(lambda: not _has(app, ConfirmDialog), 2.0)
            await pilot.pause(0.2)
            self.assertFalse(any(c[0] == "remove_container" for c in svc.calls))

            await pilot.press("d")
            await wait_until(lambda: _has(app, ConfirmDialog), 2.0)
            await pilot.click(f"#{BTN_CONFIRM_ID}")
            await wait_until(
                lambda: any(c[:2] == ("remove_container", "db") for c in svc.calls),
                3.0,
            )

    async def test_space_marks_a_row_and_escape_clears_it(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=0)
            await pilot.press("space")
            await pilot.pause()
            self.assertTrue(app._tables.has_marks(TabID.CONTAINERS))
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._tables.has_marks(TabID.CONTAINERS))


class ModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_and_inspect_open_and_hand_focus_back(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            await pilot.press("question_mark")
            await wait_until(lambda: _has(app, HelpScreen), 2.0)
            await pilot.press("escape")
            await wait_until(lambda: not _has(app, HelpScreen), 2.0)

            _move_to(app, table, "web")
            await pilot.pause()
            await pilot.press("i")
            await wait_until(lambda: _has(app, InspectScreen), 3.0)
            await pilot.press("escape")
            await wait_until(lambda: not _has(app, InspectScreen), 2.0)
            await wait_until(lambda: table.has_focus, 2.0)

    async def test_a_picker_modal_with_focus_opens_and_closes(self) -> None:
        """Focus moving inside a modal reaches the app's focus handler, which must
        not look for base-screen widgets on the modal (it once raised NoMatches)."""
        app, svc = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            await pilot.press("P")
            await wait_until(lambda: _has(app, PruneScreen), 2.0)
            await pilot.pause(0.2)
            await pilot.press("escape")
            await wait_until(lambda: not _has(app, PruneScreen), 2.0)
            await wait_until(lambda: table.has_focus, 2.0)
            self.assertFalse(any(c[0].startswith("prune_") for c in svc.calls))

    async def test_prune_with_confirmation_off_runs_and_exits_cleanly(self) -> None:
        """Choosing a target in the picker prunes at once (no confirm dialog), and
        the app can exit straight after: focus events then reach the handler in
        teardown, which once raised NoMatches / IndexError."""
        svc = MockDockerService(lambda: EMPTY_SNAPSHOT)
        app = DockSurfApp(docker=svc, config=Config(confirm_prune=False))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.action_prune()
            await wait_until(lambda: _has(app, PruneScreen), 2.0)
            app.screen_stack[-1].dismiss("containers")
            await wait_until(lambda: ("prune_containers",) in svc.calls, 3.0)
            self.assertFalse(_has(app, ConfirmDialog))


class ResizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resize_keeps_the_cursor_row_and_marks(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            _move_to(app, table, "solo")
            await pilot.press("space")
            await pilot.pause()
            key = app._focused_row_key(TabID.CONTAINERS)
            marked = set(app._tables.marked[TabID.CONTAINERS])
            self.assertEqual(len(marked), 1)
            for size, mode in (((80, 24), "compact"), ((160, 40), "wide")):
                await pilot.resize_terminal(*size)
                await wait_until(lambda: app._view.layout.value == mode, 3.0)
                await pilot.pause()
                self.assertEqual(app._focused_row_key(TabID.CONTAINERS), key)
                self.assertEqual(set(app._tables.marked[TabID.CONTAINERS]), marked)

    async def test_streams_follow_the_layout(self) -> None:
        app, svc = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            _move_to(app, table, "web")
            await wait_until(lambda: app._live.stats_target == "web", 3.0)
            await pilot.resize_terminal(80, 24)
            await wait_until(lambda: app._live.stats_target is None, 3.0)
            await wait_until(lambda: app._live.log_target is None, 3.0)
            svc.calls.clear()
            await pilot.press("down", "up")
            await pilot.pause(0.6)
            self.assertFalse(
                any(c[0] in ("stream_stats", "stream_logs") for c in svc.calls)
            )

    async def test_z_overlay_round_trip_in_compact(self) -> None:
        app, _ = _app()
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            _move_to(app, table, "web")
            await pilot.press("z")
            await wait_until(lambda: app.query_one(f"#{SIDE_COLUMN_ID}").display, 2.0)
            self.assertFalse(app.query_one(f"#{LIST_PANEL_ID}").display)
            await pilot.press("escape")
            await wait_until(lambda: app.query_one(f"#{LIST_PANEL_ID}").display, 2.0)
            self.assertFalse(app.query_one(f"#{SIDE_COLUMN_ID}").display)
            await wait_until(lambda: table.has_focus, 2.0)


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_state_shows_and_recovers_after_a_daemon_drop(self) -> None:
        svc = MockDockerService(lambda: EMPTY_SNAPSHOT)
        streams = {"n": 0}

        def stream_events():
            streams["n"] += 1
            if streams["n"] == 1:
                return FakeEventStream(error=ConnectionError("daemon down"))
            return FakeEventStream()

        def ensure_connected() -> ConnectionState:
            svc._connected = True
            svc.connection = _CONNECTED_STATE
            return svc.connection

        svc.stream_events = stream_events  # type: ignore[method-assign]
        svc.ensure_connected = ensure_connected  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            # Generous timeouts: the retry backs off ~2 s and the full suite is busy.
            await wait_until(lambda: app._view.connection == "offline", 8.0)
            self.assertIn("offline", str(app.query_one(f"#{TOP_BAR_ID}").content))
            await wait_until(lambda: app._view.connection == "live", 10.0)
            self.assertEqual(app._view.connection_message, "")


if __name__ == "__main__":
    unittest.main()
