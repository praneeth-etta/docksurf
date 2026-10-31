"""Tide redesign, phase 02A: Containers table rows (columns, row tints, cursor
gutter). Assertions run inside the `run_test()` block — teardown resets live
state."""

import unittest
from unittest.mock import patch

from rich.console import Console
from textual.widgets import DataTable, Static, TabbedContent

from docksurf_py.core.models import (
    ContainerDetail,
    ContainerStats,
    ContainerTop,
    DockerSnapshot,
    HealthProbe,
    PortBinding,
)
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    CHIP_BAR_ID,
    DETAIL_PANE_ID,
    EMPTY_STATE_IDS,
    LIVE_PANEL_ID,
    TableID,
)
from docksurf_py.tui.widgets import ChipBar, DetailPane, LivePanel
from docksurf_py.tui.widgets.tables import ResourceTable
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container


def _snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[
            make_container("web", project="myapp", service="web"),
            make_container("db", project="myapp", service="db", running=False),
            make_container("solo"),
        ],
        images=[],
        volumes=[],
        networks=[],
    )


async def _ready(app: DockSurfApp, pilot) -> DataTable:
    await pilot.pause()
    await wait_until(lambda: bool(app._tables.rows.get(TabID.CONTAINERS)))
    await pilot.pause()
    return app.query_one(f"#{TableID.CONTAINERS}", DataTable)


def _gutter(table: DataTable) -> list[str]:
    return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]


class RowStyleHookTests(unittest.TestCase):
    def test_textual_still_has_the_private_row_style_hook(self) -> None:
        """`ResourceTable` overrides this private hook to tint whole rows; if a
        Textual upgrade drops it the tints would silently disappear."""
        self.assertTrue(callable(getattr(DataTable, "_get_row_style", None)))
        self.assertIn("row_index", DataTable._get_row_style.__code__.co_varnames)


class ContainerColumnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_columns_and_cells(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            labels = [str(c.label) for c in table.ordered_columns]
            self.assertEqual(labels, ["", "Name", "State", "Uptime", "Ports", "Image"])
            self.assertEqual(len(table.get_row_at(0)), 6)

    async def test_sort_by_state_puts_failed_first(self) -> None:
        snapshot = _snapshot()
        broken = make_container("broken", running=False)
        broken.exit_code = 1
        snapshot.containers.append(broken)
        app = DockSurfApp(docker=MockDockerService(lambda: snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await _ready(app, pilot)
            kind = app._resource_registry[TabID.CONTAINERS].kind
            key = kind.sort_keys["State"]
            ordered = sorted(snapshot.containers, key=key)
            self.assertEqual(ordered[0].name, "broken")
            self.assertEqual(ordered[-1].name, "db")


class TintAndGutterTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_row_is_tinted_and_tables_are_resource_tables(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            self.assertIsInstance(table, ResourceTable)
            self.assertEqual(list(table.row_tints), [0])  # the myapp header

    async def test_marking_tints_the_row_and_clearing_removes_it(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=3)  # solo
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            self.assertIn(3, table.row_tints)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIn(3, table.row_tints)

    async def test_gutter_follows_the_cursor(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=1)
            await pilot.pause()
            self.assertEqual(sum("▌" in g for g in _gutter(table)), 1)
            self.assertIn("▌", _gutter(table)[1])
            for _ in range(2):
                await pilot.press("down")
            await pilot.pause()
            gutter = _gutter(table)
            self.assertEqual(sum("▌" in g for g in gutter), 1)
            self.assertIn("▌", gutter[table.cursor_row])

    async def test_gutter_survives_a_repopulate(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=2)
            await pilot.pause()
            app._rerender_active_table()
            await pilot.pause()
            gutter = _gutter(table)
            self.assertEqual(sum("▌" in g for g in gutter), 1)
            self.assertIn("▌", gutter[table.cursor_row])


class ChipTests(unittest.IsolatedAsyncioTestCase):
    async def test_f_cycles_chips_and_shrinks_the_table(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            self.assertEqual(table.row_count, 4)  # header, web, db, solo
            await pilot.press("F")
            await pilot.pause()
            self.assertEqual(app._tables.active_chip[TabID.CONTAINERS], "running")
            self.assertEqual(table.row_count, 3)  # header, web, solo
            await pilot.press("F")
            await pilot.pause()
            self.assertEqual(table.row_count, 2)  # header, db
            await pilot.press("F", "F")
            await pilot.pause()
            self.assertEqual(app._tables.active_chip[TabID.CONTAINERS], "all")
            self.assertEqual(table.row_count, 4)

    async def test_chip_bar_shows_counts_and_active_chip(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await _ready(app, pilot)
            bar = app.query_one(f"#{CHIP_BAR_ID}", ChipBar)
            self.assertIn("running 2", str(bar.content))
            await pilot.press("F")
            await pilot.pause()
            self.assertIn("running 2", str(bar.content))

    async def test_clicking_a_chip_sets_it(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await _ready(app, pilot)
            await app.run_action("set_chip('stopped')")
            await pilot.pause()
            self.assertEqual(app._tables.active_chip[TabID.CONTAINERS], "stopped")
            self.assertEqual(table.row_count, 2)

    async def test_empty_chip_shows_a_message_and_f_is_inert_without_chips(
        self,
    ) -> None:
        snapshot = _snapshot()
        snapshot.containers = [make_container("solo")]
        app = DockSurfApp(docker=MockDockerService(lambda: snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await _ready(app, pilot)
            await app.run_action("set_chip('failed')")
            await pilot.pause()
            empty = app.query_one(f"#{EMPTY_STATE_IDS[TabID.CONTAINERS]}", Static)
            self.assertTrue(empty.display)
            self.assertIn("No failed containers", str(empty.content))
            app.query_one(TabbedContent).active = TabID.NETWORKS
            await pilot.pause()
            await pilot.press("F")
            await pilot.pause()
            self.assertEqual(app._tables.active_chip[TabID.NETWORKS], "all")


def _card_snapshot() -> DockerSnapshot:
    web = make_container("shop-web-1", project="shop", service="web", health="healthy")
    web.ports = [PortBinding("80/tcp", "0.0.0.0", "8080")]
    web.networks = ["shop_default"]
    db = make_container("shop-db-1", running=False, project="shop", service="db")
    return DockerSnapshot([web, db, make_container("solo")], [], [], [])


def _card_service(snapshot: DockerSnapshot) -> MockDockerService:
    svc = MockDockerService(lambda: snapshot)
    web = snapshot.containers[0]
    svc.container_detail_map[web.id] = ContainerDetail(
        env=["PATH=/bin", "DB_PASSWORD=x"],
        health_log=[HealthProbe("2026-07-02T09:05:00Z", 0, "ok")],
        started_at="",
        restart_count=1,
        network_ips={"shop_default": "172.18.0.7"},
        health_interval_s=10.0,
    )
    return svc


def _body_text(pane: DetailPane) -> str:
    console = Console(width=80, no_color=True, highlight=False)
    with console.capture() as cap:
        console.print(pane._panel.content)
    return cap.get()


class DetailCardTests(unittest.IsolatedAsyncioTestCase):
    async def _select(self, app: DockSurfApp, pilot, name: str) -> DetailPane:
        table = await _ready(app, pilot)
        index = next(
            i
            for i, row in enumerate(app._tables.rows[TabID.CONTAINERS])
            if getattr(row, "name", "") == name
        )
        table.move_cursor(row=index)
        await pilot.pause()
        return app.query_one(f"#{DETAIL_PANE_ID}", DetailPane)

    async def test_container_card_is_painted_with_title_and_sections(self) -> None:
        app = DockSurfApp(docker=_card_service(_card_snapshot()))
        async with app.run_test(size=(160, 40)) as pilot:
            pane = await self._select(app, pilot, "shop-web-1")
            await wait_until(lambda: pane.section("env") is not None)
            self.assertIn("shop-web-1 · container", str(pane.border_title))
            self.assertEqual(pane.border_subtitle, "i inspect")
            await wait_until(lambda: "172.18.0.7" in _body_text(pane))
            body = _body_text(pane)
            self.assertIn("RUNNING", body)
            self.assertIn("shop › web", body)
            self.assertIn("0.0.0.0:8080 → 80/tcp", body)
            self.assertIn("NETWORK ─", body)
            self.assertIn("MORE ─", body)
            for key in ("env", "top", "health"):
                self.assertIsNotNone(pane.section(key), key)
                self.assertTrue(pane.section(key).collapsed)
            self.assertIn("2 vars · 1 masked", str(pane.section("env").title))
            self.assertIn("R reveal", str(pane.section("env").title))

    async def test_project_header_shows_the_project_card(self) -> None:
        app = DockSurfApp(docker=_card_service(_card_snapshot()))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=0)
            await pilot.pause()
            pane = app.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
            self.assertIn("shop · project", str(pane.border_title))
            self.assertIn("1/2 UP", _body_text(pane))
            self.assertIsNone(pane.section("env"))

    async def test_open_section_survives_a_refresh_and_resets_on_new_card(self) -> None:
        app = DockSurfApp(docker=_card_service(_card_snapshot()))
        async with app.run_test(size=(160, 40)) as pilot:
            pane = await self._select(app, pilot, "shop-web-1")
            await wait_until(lambda: pane.section("env") is not None)
            pane.expand_section("env")
            app._refresh_detail_card()
            await pilot.pause()
            self.assertFalse(pane.section("env").collapsed)
            await pilot.press("down")  # next row: a different card
            await pilot.pause()
            await pilot.press("up")
            await wait_until(lambda: pane.section("env") is not None)
            self.assertTrue(pane.section("env").collapsed)

    async def test_t_expands_top_and_fetches_then_collapses(self) -> None:
        snapshot = _card_snapshot()
        svc = _card_service(snapshot)
        svc.container_top = lambda cid: ContainerTop(  # type: ignore[method-assign]
            titles=["PID", "CMD"], processes=[["1", "nginx"], ["7", "sh"]]
        )
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            pane = await self._select(app, pilot, "shop-web-1")
            await wait_until(lambda: pane.section("top") is not None)
            await pilot.press("t")
            await wait_until(
                lambda: "2 processes" in str(pane.section("top").title), timeout=3.0
            )
            self.assertFalse(pane.section("top").collapsed)
            await pilot.press("t")
            await pilot.pause()
            self.assertTrue(pane.section("top").collapsed)
            self.assertNotIn("processes", str(pane.section("top").title))

    async def test_stopped_container_has_no_top_section(self) -> None:
        app = DockSurfApp(docker=_card_service(_card_snapshot()))
        async with app.run_test(size=(160, 40)) as pilot:
            pane = await self._select(app, pilot, "shop-db-1")
            await pilot.pause()
            self.assertIn("STOPPED", _body_text(pane))
            self.assertIsNone(pane.section("top"))

    async def test_card_title_renders_on_terminal_native_themes(self) -> None:
        app = DockSurfApp(docker=_card_service(_card_snapshot()))
        async with app.run_test(size=(160, 40)) as pilot:
            app.theme = "ansi-dark"
            await pilot.pause()
            pane = await self._select(app, pilot, "shop-web-1")
            await pilot.pause()
            self.assertIn("shop-web-1", str(pane.border_title))


class _SampleStream:
    """A stats stream that yields a couple of samples, then stops."""

    def __iter__(self):
        yield ContainerStats(
            12.5, 10 * 1024 * 1024, 100 * 1024 * 1024, 10.0, 0, 0, 0, 0
        )
        yield ContainerStats(
            25.0, 12 * 1024 * 1024, 100 * 1024 * 1024, 12.0, 4096, 1024, 0, 0
        )

    def stop(self) -> None:
        pass


class _IdleStream:
    def __iter__(self):
        return iter(())

    def stop(self) -> None:
        pass


def _many_running() -> DockerSnapshot:
    names = [f"c{i}" for i in range(8)]
    return DockerSnapshot([make_container(n) for n in names], [], [], [])


class LivePanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_shows_sparklines_for_a_running_container(self) -> None:
        snapshot = _card_snapshot()
        svc = _card_service(snapshot)
        svc.stream_stats = lambda cid: _SampleStream()  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=2)  # shop-web-1
            await wait_until(
                lambda: app._live.stats_target == "shop-web-1", timeout=3.0
            )
            panel = app.query_one(f"#{LIVE_PANEL_ID}", LivePanel)
            await wait_until(lambda: len(app._live.history.cpu) == 2, timeout=3.0)
            await pilot.pause()
            self.assertTrue(panel.display)
            self.assertIn("live", str(panel.border_title))
            console = Console(width=80, no_color=True, highlight=False)
            with console.capture() as cap:
                console.print(panel.content)
            text = cap.get()
            self.assertIn("25.0%", text)
            self.assertIn("MEM", text)
            self.assertIn("↓", text)

    async def test_panel_is_hidden_for_stopped_containers_and_project_headers(
        self,
    ) -> None:
        snapshot = _card_snapshot()
        svc = _card_service(snapshot)
        svc.stream_stats = lambda cid: _IdleStream()  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            panel = app.query_one(f"#{LIVE_PANEL_ID}", LivePanel)
            table.move_cursor(row=2)
            await wait_until(lambda: panel.display, timeout=3.0)
            table.move_cursor(row=1)  # shop-db-1, stopped
            await wait_until(lambda: not panel.display, timeout=3.0)
            self.assertIsNone(app._live.stats_target)
            table.move_cursor(row=3)  # solo, running
            await wait_until(lambda: panel.display, timeout=3.0)
            table.move_cursor(row=0)  # the shop project header
            await wait_until(lambda: not panel.display, timeout=3.0)

    async def test_fast_cursor_moves_start_one_stats_stream(self) -> None:
        started: list[str] = []
        svc = MockDockerService(_many_running)

        def stream_stats(cid: str):
            started.append(cid)
            return _IdleStream()

        svc.stream_stats = stream_stats  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        # A long settle delay keeps this about the debounce, not machine speed
        # (the burst below re-renders the card five times).
        with patch("docksurf_py.tui.observability.SIDE_RETARGET_DELAY", 1.0):
            await self._fast_burst(app, started)

    async def _fast_burst(self, app: DockSurfApp, started: list[str]) -> None:
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            await wait_until(lambda: app._live.stats_target == "c0", timeout=4.0)
            started.clear()
            # One burst, as holding `↓` produces (separate `press` calls can be
            # slower than the settle delay under load).
            for row in range(1, 6):
                table.move_cursor(row=row)
            await pilot.pause()
            self.assertEqual(table.cursor_row, 5)
            self.assertEqual(started, [])  # still waiting for the cursor to settle
            await wait_until(lambda: app._live.stats_target == "c5", timeout=4.0)
            await pilot.pause(0.5)
            self.assertEqual(started, ["c5"])

    async def test_card_follows_the_cursor_immediately(self) -> None:
        svc = MockDockerService(_many_running)
        svc.stream_stats = lambda cid: _IdleStream()  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await _ready(app, pilot)
            await pilot.press("down", "down")
            await pilot.pause()
            pane = app.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
            self.assertIn("c2 · container", str(pane.border_title))
