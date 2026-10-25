"""Characterization tests for the log pane's buffer/filter/match behavior that
the existing log tests don't cover: multi-match navigation and its header
hints, and pause buffering. Pins today's behavior so moving the buffer state
into core (local/UI_SEPARATION_PLAN.md, Phase 3e) can be checked against it.
"""

import unittest

from textual.widgets import DataTable, Label, RichLog

from docksurf_py.core.models import DockerSnapshot
from docksurf_py.core.types import LogLine, LogOptions
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import LOG_PANE_HEADER_ID, LOG_PANE_ID, TableID
from docksurf_py.tui.widgets import LogPane
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container

LINES = [
    LogLine(text="GET /a 200", ts="t1"),
    LogLine(text="error: db down", ts="t2", stream="stderr"),
    LogLine(text="GET /b 500", ts="t3"),
    LogLine(text="Error: retry", ts="t4"),
]


async def _open(app: DockSurfApp, pilot, svc: MockDockerService) -> LogPane:
    await wait_until(lambda: app.snapshot is not None)
    await pilot.pause()
    table = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
    table.focus()
    table.move_cursor(row=0)
    await pilot.press("l")
    pane = app.query_one(f"#{LOG_PANE_ID}", LogPane)
    await wait_until(lambda: len(pane.export_text().splitlines()) == len(svc.log_lines))
    await pilot.pause()
    return pane


def _header(pane: LogPane) -> str:
    return str(pane.query_one(f"#{LOG_PANE_HEADER_ID}", Label).render())


def _view_lines(pane: LogPane) -> list[str]:
    return [strip.text for strip in pane.query_one(RichLog).lines]


def _svc() -> MockDockerService:
    svc = MockDockerService(lambda: DockerSnapshot([make_container("web")], [], [], []))
    svc.log_lines = list(LINES)
    return svc


class LogMatchNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_matches_are_case_insensitive_and_cycle_both_ways(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            pane = await _open(app, pilot, svc)
            pane._filter = "error"
            pane._render_to_view()
            self.assertEqual(_view_lines(pane), ["error: db down", "Error: retry"])
            self.assertIn("filter: error  |  2 matches", _header(pane))

            app.action_next_match()
            self.assertIn("match 1/2", _header(pane))
            app.action_next_match()
            self.assertIn("match 2/2", _header(pane))
            app.action_next_match()
            self.assertIn("match 1/2", _header(pane))
            app.action_prev_match()
            self.assertIn("match 2/2", _header(pane))

    async def test_prev_first_jumps_to_last_match(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            pane = await _open(app, pilot, svc)
            pane._filter = "GET"
            pane._render_to_view()
            app.action_prev_match()
            self.assertIn("match 2/2", _header(pane))

    async def test_no_matches_hint_and_jump_is_a_noop(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            pane = await _open(app, pilot, svc)
            pane._filter = "zzz"
            pane._render_to_view()
            self.assertEqual(_view_lines(pane), [])
            self.assertIn("no matches", _header(pane))
            app.action_next_match()
            self.assertIn("no matches", _header(pane))

    async def test_timestamps_toggle_rerenders_with_ts_prefix(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            pane = await _open(app, pilot, svc)
            self.assertEqual(_view_lines(pane)[0], "GET /a 200")
            pane.toggle_timestamps()
            await pilot.pause()
            self.assertEqual(_view_lines(pane)[0], "t1 GET /a 200")

    async def test_export_text_ignores_filter_and_timestamp_toggle(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            pane = await _open(app, pilot, svc)
            pane._filter = "GET"
            pane._render_to_view()
            self.assertEqual(
                pane.export_text().splitlines(),
                [
                    "t1 GET /a 200",
                    "t2 [stderr] error: db down",
                    "t3 GET /b 500",
                    "t4 Error: retry",
                ],
            )

    async def test_new_options_keep_filter_but_reset_matches(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            pane = await _open(app, pilot, svc)
            pane._filter = "error"
            pane._render_to_view()
            app.action_next_match()
            pane.set_options(LogOptions(tail=100))
            await wait_until(lambda: "2 matches" in _header(pane), timeout=2.0)
            self.assertEqual(pane._filter, "error")
            self.assertEqual(_view_lines(pane), ["error: db down", "Error: retry"])
