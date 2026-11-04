"""Tide redesign, phase 06: the compact (80x24) layout. Assertions run inside the
`run_test()` block — teardown resets live state."""

import re
import unittest
from dataclasses import replace

from rich.text import Text
from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.models import DockerSnapshot
from docksurf_py.core.state.view import LayoutMode
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    BOTTOM_BAR_ID,
    LIST_PANEL_ID,
    LIST_TOOLBAR_ID,
    SEARCH_BAR_ID,
    TOOLBAR_LINE_ID,
    TOP_BAR_ID,
    TableID,
)
from docksurf_py.tui.widgets import BottomBar, SearchBar, TopBar
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container
from tests.test_redesign_jump import _snapshot as _full_snapshot


def _snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[
            make_container("web", project="shop", service="web"),
            replace(
                make_container("db", project="shop", service="db", running=False),
                status="Exited (0) 14 minutes ago",
            ),
            make_container("solo"),
        ],
        images=[],
        volumes=[],
        networks=[],
    )


def _svc() -> MockDockerService:
    return MockDockerService(_snapshot)


def _stream_calls(svc: MockDockerService) -> list[str]:
    return [c[0] for c in svc.calls if c[0] in ("stream_stats", "stream_logs")]


async def _ready(app: DockSurfApp, pilot) -> DataTable:
    await pilot.pause()
    await wait_until(lambda: bool(app._tables.rows.get(TabID.CONTAINERS)))
    await pilot.pause()
    table = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
    table.focus()
    return table


class LayoutModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_boots_compact_at_80x24(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(80, 24)) as pilot:
            await _ready(app, pilot)
            self.assertEqual(app._view.layout, LayoutMode.COMPACT)
            self.assertTrue(app.has_class("-compact"))
            self.assertFalse(app.query_one("#side-column").display)
            self.assertTrue(app.query_one(f"#{LIST_PANEL_ID}").display)

    async def test_boots_wide_at_160x40(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await _ready(app, pilot)
            self.assertEqual(app._view.layout, LayoutMode.WIDE)
            self.assertFalse(app.has_class("-compact"))
            self.assertTrue(app.query_one("#side-column").display)

    async def test_no_streams_while_compact_with_the_side_hidden(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=2)
            await pilot.press("down", "up")
            await pilot.pause(0.6)
            self.assertEqual(_stream_calls(svc), [])
            self.assertIsNone(app._live.stats_target)
            self.assertIsNone(app._live.log_target)

    async def test_resize_to_wide_starts_streams_and_back_stops_them(self) -> None:
        svc = _svc()
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=2)  # web, running
            await pilot.pause(0.5)
            self.assertIsNone(app._live.stats_target)

            await pilot.resize_terminal(200, 50)
            await wait_until(lambda: app._live.stats_target == "web", timeout=3.0)
            self.assertEqual(app._view.layout, LayoutMode.WIDE)
            self.assertFalse(app.has_class("-compact"))
            self.assertTrue(app.query_one("#side-column").display)

            await pilot.resize_terminal(80, 24)
            await wait_until(lambda: app._live.stats_target is None, timeout=3.0)
            await wait_until(lambda: app._live.log_target is None, timeout=3.0)
            self.assertEqual(app._view.layout, LayoutMode.COMPACT)
            self.assertTrue(app.has_class("-compact"))
            self.assertFalse(app.query_one("#side-column").display)


def _names(table: DataTable) -> list[str]:
    """Column labels without the sort arrow or right-alignment padding."""
    return [
        re.sub(r"\s*[▲▼]$", "", Text.from_markup(str(c.label)).plain).strip()
        for c in table.columns.values()
    ]


def _plain(table: DataTable, row: int) -> list[str]:
    return [Text.from_markup(str(c)).plain for c in table.get_row_at(row)]


_TAB_KEYS = {
    TabID.CONTAINERS: "1",
    TabID.IMAGES: "2",
    TabID.VOLUMES: "3",
    TabID.NETWORKS: "4",
}


class CompactColumnTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, app: DockSurfApp, pilot, tab: TabID) -> DataTable:
        await pilot.pause()
        await wait_until(lambda: app.snapshot is not None)
        await pilot.press(_TAB_KEYS[tab])
        await wait_until(lambda: app.query_one(TabbedContent).active == tab)
        entry = app._resource_registry[tab]
        table = app.query_one(f"#{entry.table_id}", DataTable)
        await wait_until(lambda: table.row_count > 0)
        return table

    async def test_every_tab_shows_its_compact_columns(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_full_snapshot))
        async with app.run_test(size=(80, 24)) as pilot:
            for tab, entry in app._resource_registry.items():
                table = await self._open(app, pilot, tab)
                self.assertEqual(_names(table), ["", *entry.visible_columns(True)])
                self.assertEqual(len(table.get_row_at(0)), len(table.columns))
            self.assertEqual(
                [
                    list(e.visible_columns(True))
                    for e in app._resource_registry.values()
                ],
                [
                    ["Name", "State", "Uptime"],
                    ["Repository", "Size", "Status"],
                    ["Name", "Size", "Used by"],
                    ["Name", "Containers"],
                ],
            )

    async def test_every_tab_shows_all_columns_when_wide(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_full_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            for tab, entry in app._resource_registry.items():
                table = await self._open(app, pilot, tab)
                self.assertEqual(_names(table), ["", *entry.columns])
                self.assertEqual(len(table.get_row_at(0)), len(table.columns))

    async def test_resize_swaps_the_columns_on_every_tab(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_full_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await self._open(app, pilot, TabID.CONTAINERS)
            await pilot.resize_terminal(80, 24)
            await wait_until(lambda: app._view.layout == LayoutMode.COMPACT)
            for tab, entry in app._resource_registry.items():
                table = await self._open(app, pilot, tab)
                self.assertEqual(_names(table), ["", *entry.visible_columns(True)])
            await pilot.resize_terminal(160, 40)
            await wait_until(lambda: app._view.layout == LayoutMode.WIDE)
            for tab, entry in app._resource_registry.items():
                table = await self._open(app, pilot, tab)
                self.assertEqual(_names(table), ["", *entry.columns])

    async def test_container_cells_are_shortened_in_compact(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            rows = [" ".join(_plain(table, i)) for i in range(table.row_count)]
            header = rows[0]
            self.assertIn("shop", header)
            self.assertNotIn("up", header)  # dots only, no `1/2 up`
            db = next(r for r in rows if "db" in r)
            self.assertIn("14m", db)
            self.assertNotIn("ago", db)

        wide = DockSurfApp(docker=_svc())
        async with wide.run_test(size=(160, 40)) as pilot:
            table = await _ready(wide, pilot)
            rows = [" ".join(_plain(table, i)) for i in range(table.row_count)]
            self.assertIn("1/2 up", rows[0])
            self.assertIn("14m ago", next(r for r in rows if "db" in r))

    async def test_resize_keeps_the_cursor_row_and_marks(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _ready(app, pilot)
            table.move_cursor(row=3)  # solo
            await pilot.press("space")
            await pilot.pause()
            key = app._focused_row_key(TabID.CONTAINERS)
            marked = set(app._tables.marked[TabID.CONTAINERS])
            self.assertEqual(len(marked), 1)

            for size, mode in (((80, 24), "compact"), ((160, 40), "wide")):
                await pilot.resize_terminal(*size)
                await wait_until(lambda: app._view.layout.value == mode)
                await pilot.pause()
                self.assertEqual(app._focused_row_key(TabID.CONTAINERS), key)
                self.assertEqual(set(app._tables.marked[TabID.CONTAINERS]), marked)

    async def test_header_click_sorts_by_the_visible_column(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            # Compact columns: gutter, Name, State, Uptime -> index 3 is Uptime.
            event = DataTable.HeaderSelected(
                table, list(table.columns)[3], 3, Text("Uptime")
            )
            app._on_header_selected(event)
            await pilot.pause()
            sort = app._tables.sort(TabID.CONTAINERS)
            self.assertIsNotNone(sort)
            assert sort is not None
            self.assertEqual(sort[0], "Uptime")
            self.assertIn("Uptime", _names(table))


def _bar(app: DockSurfApp, bar_id: str, kind: type) -> str:
    return str(app.query_one(f"#{bar_id}", kind).content)


class CompactChromeTests(unittest.IsolatedAsyncioTestCase):
    async def test_top_bar_is_short(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(80, 24)) as pilot:
            await _ready(app, pilot)
            top = _bar(app, TOP_BAR_ID, TopBar)
            self.assertIn("◆", top)
            self.assertNotIn("docksurf", top)
            self.assertIn("1 Containers", top)
            self.assertNotIn("Images", top)
            self.assertIn("live", top)

        wide = DockSurfApp(docker=_svc())
        async with wide.run_test(size=(160, 40)) as pilot:
            await _ready(wide, pilot)
            top = _bar(wide, TOP_BAR_ID, TopBar)
            self.assertIn("◆ docksurf", top)
            self.assertIn("Images", top)

    async def test_bottom_bar_is_two_lines(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(80, 24)) as pilot:
            await _ready(app, pilot)
            bar = app.query_one(f"#{BOTTOM_BAR_ID}", BottomBar)
            self.assertEqual(bar.size.height, 2)
            counts, hints = str(bar.content).split("\n")
            self.assertIn("● 2", counts)
            self.assertIn("orphan vols", counts)
            self.assertIn("? more", " ".join(hints.split()))
            self.assertLessEqual(len(hints), 78)

        wide = DockSurfApp(docker=_svc())
        async with wide.run_test(size=(160, 40)) as pilot:
            await _ready(wide, pilot)
            bar = wide.query_one(f"#{BOTTOM_BAR_ID}", BottomBar)
            self.assertEqual(bar.size.height, 1)
            self.assertNotIn("\n", str(bar.content))

    async def test_panel_title_and_subtitle(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(80, 24)) as pilot:
            await _ready(app, pilot)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            self.assertEqual(
                Text.from_markup(str(panel.border_title)).plain, "containers · 2 up"
            )
            self.assertEqual(
                Text.from_markup(str(panel.border_subtitle)).plain, "z detail"
            )
            await pilot.resize_terminal(160, 40)
            await wait_until(lambda: app._view.layout == LayoutMode.WIDE)
            await pilot.pause()
            title = Text.from_markup(str(panel.border_title)).plain
            self.assertIn("grouped by project", title)

    async def test_filter_row_hidden_until_slash(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _ready(app, pilot)
            toolbar = app.query_one(f"#{LIST_TOOLBAR_ID}")
            search = app.query_one(f"#{SEARCH_BAR_ID}", SearchBar)
            self.assertFalse(toolbar.display)

            await pilot.press("/")
            await wait_until(lambda: search.has_focus, timeout=2.0)
            self.assertTrue(toolbar.display)
            self.assertTrue(app.has_class("-filtering"))

            await pilot.press("s", "o", "l")
            await wait_until(lambda: table.row_count == 1, timeout=2.0)
            await pilot.press("escape")
            await pilot.pause()
            # The query stays active, so its row stays visible.
            self.assertEqual(search.value, "sol")
            self.assertTrue(toolbar.display)

            search.value = ""
            await pilot.pause()
            await pilot.press("slash")  # re-open, then leave with the box empty
            await wait_until(lambda: search.has_focus, timeout=2.0)
            await pilot.press("escape")
            await wait_until(lambda: not toolbar.display, timeout=2.0)
            self.assertFalse(app.has_class("-filtering"))

    async def test_filter_row_always_shown_when_wide(self) -> None:
        app = DockSurfApp(docker=_svc())
        async with app.run_test(size=(160, 40)) as pilot:
            await _ready(app, pilot)
            self.assertTrue(app.query_one(f"#{LIST_TOOLBAR_ID}").display)

    async def test_summary_line_is_hidden_in_compact(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_full_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(
                lambda: app.query_one(TabbedContent).active == TabID.IMAGES
            )
            line = app.query_one(f"#{TOOLBAR_LINE_ID}")
            self.assertTrue(line.display)
            await pilot.resize_terminal(80, 24)
            await wait_until(lambda: app._view.layout == LayoutMode.COMPACT)
            await pilot.pause()
            self.assertFalse(line.display)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            self.assertEqual(
                Text.from_markup(str(panel.border_title)).plain, "images · 4"
            )
            await pilot.resize_terminal(160, 40)
            await wait_until(lambda: app._view.layout == LayoutMode.WIDE)
            await pilot.pause()
            self.assertTrue(line.display)
