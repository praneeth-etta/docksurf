"""Tide redesign, phase 02E: the side log panel (auto-follow, full view). Assertions
run inside the `run_test()` block — teardown resets live state."""

import unittest
from unittest.mock import patch

from rich.text import Text
from textual.widgets import DataTable, Input, TabbedContent

from docksurf_py.core.config import Config
from docksurf_py.core.models import DockerSnapshot
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    DETAIL_PANE_ID,
    LIST_PANEL_ID,
    LIVE_PANEL_ID,
    LOG_PANE_ID,
    LOG_PANE_SEARCH_ID,
    TableID,
)
from docksurf_py.tui.widgets import LogPane
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container


def _snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[
            make_container("web", project="shop", service="web"),
            make_container("db", project="shop", service="db", running=False),
            make_container("solo"),
            make_container("other"),
        ],
        images=[],
        volumes=[],
        networks=[],
    )


def _svc() -> MockDockerService:
    return MockDockerService(_snapshot)


def _stream_calls(svc: MockDockerService) -> list[str]:
    return [c[1] for c in svc.calls if c[0] == "stream_logs"]


def _title(pane: LogPane) -> str:
    return Text.from_markup(str(pane.border_title)).plain


async def _ready(app: DockSurfApp, pilot) -> DataTable:
    await pilot.pause()
    await wait_until(lambda: bool(app._tables.rows.get(TabID.CONTAINERS)))
    await pilot.pause()
    return app.query_one(f"#{TableID.CONTAINERS}", DataTable)


# Rows: 0 shop header, 1 db (stopped), 2 web, 3 solo, 4 other.
class SideLogsTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_follows_the_focused_container_after_settling(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
            self.assertTrue(pane.display)
            table.move_cursor(row=2)  # web
            await wait_until(lambda: "web" in _stream_calls(svc), timeout=3.0)
            await wait_until(lambda: "logs · web" in _title(pane), timeout=3.0)
            self.assertIn("l full view", str(pane.border_subtitle))

    async def test_project_header_loads_aggregated_logs(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
            table.move_cursor(row=2)
            await wait_until(lambda: "web" in _stream_calls(svc), timeout=3.0)
            table.move_cursor(row=0)  # the shop header
            await wait_until(
                lambda: any(c[0] == "stream_project_logs" for c in svc.calls),
                timeout=3.0,
            )
            await wait_until(lambda: "project: shop" in _title(pane), timeout=3.0)

    async def test_stopped_container_still_loads_its_logs(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=1)  # db, stopped
            await wait_until(lambda: "db" in _stream_calls(svc), timeout=3.0)

    async def test_fast_cursor_moves_load_once(self) -> None:
        # A long settle delay keeps this about the debounce, not machine speed.
        with patch("docksurf_py.tui.observability.SIDE_RETARGET_DELAY", 1.0):
            await self._fast_moves_load_once()

    async def _fast_moves_load_once(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            await wait_until(lambda: app._live.log_target is not None, timeout=4.0)
            await pilot.pause(1.2)
            svc.calls.clear()
            for row in range(1, 4):  # one burst, as holding `↓` produces
                table.move_cursor(row=row)
            await pilot.pause()
            self.assertEqual(_stream_calls(svc), [])  # still waiting to settle
            await wait_until(lambda: bool(_stream_calls(svc)), timeout=4.0)
            await pilot.pause(0.5)
            self.assertEqual(_stream_calls(svc), ["solo"])

    async def test_leaving_the_containers_tab_stops_and_returning_reloads(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
            table.move_cursor(row=2)
            await wait_until(lambda: "logs · web" in _title(pane), timeout=3.0)
            app.query_one(TabbedContent).active = TabID.IMAGES
            await wait_until(lambda: app._live.log_target is None, timeout=3.0)
            self.assertFalse(pane.display)
            self.assertFalse(pane._following)
            self.assertEqual(_title(pane), "logs")
            await pilot.pause(0.3)  # let the tab activation settle (focus race)
            svc.calls.clear()
            await pilot.press("1")
            await wait_until(
                lambda: app.query_one(TabbedContent).active == TabID.CONTAINERS,
                timeout=3.0,
            )
            # A tab switch resets the cursor to the first row (the shop header).
            await wait_until(
                lambda: any(c[0] == "stream_project_logs" for c in svc.calls),
                timeout=3.0,
            )
            self.assertTrue(pane.display)


class FullViewTests(unittest.IsolatedAsyncioTestCase):
    async def _focus(self, app: DockSurfApp, pilot, row: int) -> DataTable:
        table = await _ready(app, pilot)
        table.focus()
        table.move_cursor(row=row)
        await pilot.pause()
        return table

    async def test_l_toggles_full_view_and_hides_the_rest(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
            await pilot.press("l")
            await pilot.pause()
            self.assertTrue(app._view.logs_full)
            self.assertTrue(pane.has_class("expanded"))
            self.assertFalse(app.query_one(f"#{LIST_PANEL_ID}").display)
            self.assertFalse(app.query_one(f"#{DETAIL_PANE_ID}").display)
            self.assertFalse(app.query_one(f"#{LIVE_PANEL_ID}").display)
            self.assertIn("l back", str(pane.border_subtitle))
            await pilot.press("l")
            await pilot.pause()
            self.assertFalse(app._view.logs_full)
            self.assertFalse(pane.has_class("expanded"))
            self.assertTrue(app.query_one(f"#{LIST_PANEL_ID}").display)
            self.assertTrue(app.query_one(f"#{DETAIL_PANE_ID}").display)

    async def test_escape_leaves_full_view_before_clearing_marks(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            table = await self._focus(app, pilot, 4)  # other
            await pilot.press("space")  # mark it
            await pilot.pause()
            self.assertTrue(app._tables.has_marks(TabID.CONTAINERS))
            table.move_cursor(row=2)
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._view.logs_full)
            self.assertTrue(app._tables.has_marks(TabID.CONTAINERS))  # kept
            self.assertTrue(table.has_focus)
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app._tables.has_marks(TabID.CONTAINERS))

    async def test_slash_filters_the_logs_in_full_view_only(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            search = app.query_one(f"#{LOG_PANE_SEARCH_ID}", Input)
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            self.assertTrue(search.display)
            self.assertTrue(search.has_focus)
            await pilot.press("escape")  # closes the log filter, stays in full view
            await pilot.pause()
            self.assertFalse(search.display)
            self.assertTrue(app._view.logs_full)
            await pilot.press("escape")  # now leaves full view
            await pilot.pause()
            self.assertFalse(app._view.logs_full)
            # Back on the list, `/` is the table filter again.
            await pilot.press("slash")
            await pilot.pause()
            self.assertFalse(search.display)
            self.assertTrue(app.query_one("#search-bar").has_focus)

    async def test_l_needs_a_container_or_project(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            app.query_one(TabbedContent).active = TabID.IMAGES
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            self.assertFalse(app._view.logs_full)

    async def test_switching_tab_leaves_full_view(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("2")  # to Images
            await pilot.pause()
            self.assertFalse(app._view.logs_full)
            self.assertTrue(app.query_one(f"#{LIST_PANEL_ID}").display)


class SideLogsDisabledTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_until_l_opens_full_view(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc, config=Config(side_logs=False))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
            table.focus()
            table.move_cursor(row=2)
            await pilot.pause(0.6)
            self.assertFalse(pane.display)
            self.assertEqual(_stream_calls(svc), [])
            await pilot.press("l")
            await wait_until(lambda: "web" in _stream_calls(svc), timeout=3.0)
            self.assertTrue(pane.display)
            self.assertTrue(pane.has_class("expanded"))
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(pane.display)
            self.assertFalse(pane._following)
            self.assertIsNone(app._live.log_target)


class ToggleSideTests(unittest.IsolatedAsyncioTestCase):
    async def _focus(self, app: DockSurfApp, pilot, row: int) -> DataTable:
        table = await _ready(app, pilot)
        table.focus()
        table.move_cursor(row=row)
        await pilot.pause()
        return table

    async def test_z_hides_the_side_column_and_stops_its_streams(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)  # web, running
            side = app.query_one("#side-column")
            pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
            await wait_until(lambda: app._live.stats_target == "web", timeout=3.0)
            await wait_until(lambda: app._live.log_target == "web", timeout=3.0)
            self.assertTrue(side.display)

            await pilot.press("z")
            await pilot.pause()
            self.assertTrue(app._view.side_hidden)
            self.assertFalse(side.display)
            self.assertIsNone(app._live.stats_target)
            self.assertIsNone(app._live.log_target)
            self.assertFalse(pane._following)
            # The list now has (nearly) the whole width, not two thirds of it.
            self.assertGreater(
                app.query_one(f"#{LIST_PANEL_ID}").size.width, app.size.width * 0.9
            )

    async def test_cursor_moves_while_hidden_start_no_streams(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            await pilot.press("z")
            await pilot.pause()
            svc.calls.clear()
            await pilot.press("down", "down")
            await pilot.pause(0.6)
            self.assertEqual(_stream_calls(svc), [])
            self.assertIsNone(app._live.stats_target)

    async def test_z_again_restores_and_retargets(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            await pilot.press("z")
            await pilot.pause()
            svc.calls.clear()
            await pilot.press("z")
            await wait_until(lambda: app._live.stats_target == "web", timeout=3.0)
            await wait_until(lambda: "web" in _stream_calls(svc), timeout=3.0)
            self.assertTrue(app.query_one("#side-column").display)
            self.assertFalse(app._view.side_hidden)

    async def test_full_view_logs_show_even_with_the_side_column_hidden(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, 2)
            await pilot.press("z")
            await pilot.pause()
            await pilot.press("l")
            await wait_until(lambda: "web" in _stream_calls(svc), timeout=3.0)
            self.assertTrue(app.query_one("#side-column").display)
            self.assertTrue(app.query_one(f"#{LOG_PANE_ID}").display)
            await pilot.press("z")  # ignored while the logs fill the screen
            await pilot.pause()
            self.assertTrue(app._view.side_hidden)
            await pilot.press("escape")
            await pilot.pause()
            # Back to the list with the side column still hidden.
            self.assertFalse(app.query_one("#side-column").display)
            self.assertTrue(app.query_one(f"#{LIST_PANEL_ID}").display)

    async def test_z_works_on_other_tabs(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await _ready(app, pilot)
            await pilot.press("2")
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            self.assertFalse(app.query_one("#side-column").display)
            await pilot.press("1")
            await pilot.pause()
            self.assertFalse(app.query_one("#side-column").display)  # stays hidden
