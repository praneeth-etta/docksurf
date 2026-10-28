"""Tide redesign, phase 01: the app frame (top bar, bottom bar, inline filter,
panel titles). Assertions run inside the `run_test()` block — teardown resets
live state."""

import itertools
import unittest

from rich.color import Color as RichColor
from rich.style import Style
from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.models import DockerSnapshot
from docksurf_py.core.render.chrome import GLOBAL_HINTS, HintContext, key_hints
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    BOTTOM_BAR_ID,
    CHIP_BAR_ID,
    LIST_PANEL_ID,
    SEARCH_BAR_ID,
    TOP_BAR_ID,
    TableID,
)
from docksurf_py.tui.widgets import BottomBar, SearchBar, TopBar
from tests.test_app import (
    MockDockerService,
    _image,
    _network,
    _volume,
    wait_until,
)
from tests.test_compose import make_container


def _snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[
            make_container("web", project="myapp", service="web"),
            make_container("db", project="myapp", service="db", running=False),
            make_container("solo"),
        ],
        images=[_image("alpine"), _image("redis", image_id="sha256:r")],
        volumes=[_volume("v1"), _volume("v2")],
        networks=[_network("n1")],
    )


def _plain(app: DockSurfApp, bar_id: str, kind: type) -> str:
    return " ".join(str(app.query_one(f"#{bar_id}", kind).content).split())


async def _ready(app: DockSurfApp, pilot, tab: TabID = TabID.CONTAINERS) -> None:
    await pilot.pause()
    await wait_until(lambda: app.snapshot is not None)
    if tab != TabID.CONTAINERS:
        app.query_one(TabbedContent).active = tab
    await wait_until(lambda: bool(app._tables.rows.get(tab)))
    await pilot.pause()


def _app() -> DockSurfApp:
    return DockSurfApp(docker=MockDockerService(_snapshot))


class TopBarTests(unittest.IsolatedAsyncioTestCase):
    async def test_shows_tab_counts_after_first_snapshot(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            text = _plain(app, TOP_BAR_ID, TopBar)
            self.assertIn("1 Containers 3", text)
            self.assertIn("2 Images 2", text)
            self.assertIn("3 Volumes 2", text)
            self.assertIn("4 Networks 1", text)
            self.assertIn("● live", text)

    async def test_tab_switch_moves_the_active_pill(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            primary = RichColor.parse(app._view.palette.primary)

            def pilled() -> list[str]:
                bar = app.query_one(f"#{TOP_BAR_ID}", TopBar)
                text = bar.content
                return [
                    text.plain[s.start : s.end].strip()
                    for s in text.spans
                    if Style.parse(str(s.style)).bgcolor == primary
                ]

            self.assertEqual(pilled(), ["1 Containers 3"])
            await pilot.press("2")
            await wait_until(lambda: pilled() == ["2 Images 2"])

    async def test_clicking_a_tab_switches_to_it(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            plain = str(app.query_one(f"#{TOP_BAR_ID}", TopBar).content)
            x = plain.index("3 Volumes") + 2
            await pilot.click(f"#{TOP_BAR_ID}", offset=(x, 0))
            await wait_until(
                lambda: app.query_one(TabbedContent).active == TabID.VOLUMES
            )

    async def test_counts_show_ellipsis_before_the_first_snapshot(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(140, 30)) as pilot:
            app.snapshot = None  # as at boot, before the fetch lands
            app._repaint_top_bar()
            await pilot.pause()
            self.assertIn("1 Containers …", _plain(app, TOP_BAR_ID, TopBar))

    async def test_offline_state_survives_a_narrow_terminal(self) -> None:
        app = _app()
        async with app.run_test(size=(80, 24)) as pilot:
            await _ready(app, pilot)
            app._view.connection = "offline"
            app._repaint_top_bar()
            await pilot.pause()
            self.assertIn("○ offline", _plain(app, TOP_BAR_ID, TopBar))

    async def test_theme_change_repaints_with_the_new_theme_name(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            self.assertIn("M tide", _plain(app, TOP_BAR_ID, TopBar))
            app.theme = "docksurf-forest"
            await wait_until(lambda: "M forest" in _plain(app, TOP_BAR_ID, TopBar))


class BottomBarTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_hints_then_image_hints(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(app, pilot)
            table = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
            table.move_cursor(row=2)  # myapp's running `web` (row 0 is the header)
            await wait_until(lambda: "e shell" in _plain(app, BOTTOM_BAR_ID, BottomBar))
            text = _plain(app, BOTTOM_BAR_ID, BottomBar)
            self.assertIn("s stop", text)
            self.assertIn("B rebuild", text)  # a Compose service
            self.assertIn("? all keys", text)

            await pilot.press("2")
            await wait_until(
                lambda: "h history" in _plain(app, BOTTOM_BAR_ID, BottomBar)
            )
            self.assertNotIn("s stop", _plain(app, BOTTOM_BAR_ID, BottomBar))

    async def test_stopped_container_offers_start(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(app, pilot)
            table = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
            table.move_cursor(row=1)  # myapp's stopped `db`
            await wait_until(
                lambda: "x restart" not in _plain(app, BOTTOM_BAR_ID, BottomBar)
            )
            text = _plain(app, BOTTOM_BAR_ID, BottomBar)
            self.assertIn("S start", text)
            self.assertNotIn("s stop", text)

    async def test_counts_on_the_right(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(app, pilot)
            text = _plain(app, BOTTOM_BAR_ID, BottomBar)
            self.assertIn("● 2 up", text)
            self.assertIn("○ 1 down", text)
            self.assertIn("2 images · 2 orphaned volumes", text)

    async def test_marking_a_row_adds_the_clear_hint(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            self.assertNotIn("esc clear marks", _plain(app, BOTTOM_BAR_ID, BottomBar))
            await pilot.press("space")
            await wait_until(
                lambda: "esc clear marks" in _plain(app, BOTTOM_BAR_ID, BottomBar)
            )

    async def test_disconnect_message_replaces_the_counts(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 30)) as pilot:
            await _ready(app, pilot)
            app._view.connection = "offline"
            app._view.connection_message = "Docker daemon is not running"
            app._repaint_bottom_bar()
            await pilot.pause()
            text = _plain(app, BOTTOM_BAR_ID, BottomBar)
            self.assertIn("Docker daemon is not running", text)
            self.assertNotIn("orphaned", text)


class HintKeysAreBoundTests(unittest.TestCase):
    # Display name in a hint -> the key Textual's bindings use.
    _ALIASES = {"esc": "escape", "^u": "ctrl+u", "^k": "ctrl+k", "+": "plus"}
    # Handled by widgets rather than app bindings.
    _WIDGET_KEYS = {"enter", "^p"}  # Input submit; Textual's command palette

    def test_every_hinted_key_is_bound(self) -> None:
        bound: set[str] = set()
        for b in DockSurfApp.BINDINGS:
            key = b[0] if isinstance(b, tuple) else b.key
            bound.update(key.split(","))
        bound.update(SearchBar.BINDINGS[i][0] for i in range(len(SearchBar.BINDINGS)))

        rows = (
            "none container_running container_stopped project image volume "
            "volume_group network network_group"
        ).split()
        seen: set[str] = set()
        for tab, row, logs, marks, compose, filtering in itertools.product(
            TabID, rows, (False, True), (False, True), (False, True), (False, True)
        ):
            ctx = HintContext(tab, row, logs, marks, compose, filtering)  # type: ignore[arg-type]
            seen.update(k for k, _ in key_hints(ctx))
        seen.update(k for k, _ in GLOBAL_HINTS)

        missing = {
            k
            for k in seen
            if k not in self._WIDGET_KEYS and self._ALIASES.get(k, k) not in bound
        }
        self.assertEqual(missing, set(), "hints name keys that aren't bound")
        self.assertGreater(len(seen), 15)  # the walk really covered the hints


class InlineFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_table_has_focus_at_boot_not_the_filter(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            self.assertIsInstance(app.focused, DataTable)

    async def test_slash_focuses_the_filter_and_typing_goes_to_it(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            await pilot.press("slash")
            await pilot.pause()
            search = app.query_one(f"#{SEARCH_BAR_ID}", SearchBar)
            self.assertTrue(search.has_focus)
            await pilot.press("r", "e", "d")
            self.assertEqual(search.value, "red")
            # Typing never fired the table's single-key actions.
            self.assertEqual(app.query_one(TabbedContent).active, TabID.IMAGES)

    async def test_filter_hints_show_while_typing(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            await pilot.press("slash")
            await wait_until(
                lambda: "esc back to list" in _plain(app, BOTTOM_BAR_ID, BottomBar)
            )
            await pilot.press("escape")
            await wait_until(
                lambda: "esc back to list" not in _plain(app, BOTTOM_BAR_ID, BottomBar)
            )

    async def test_escape_returns_focus_and_keeps_the_query(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            await pilot.press("slash", "r", "e", "d")
            await wait_until(
                lambda: (
                    [i.repository for i in app._tables.rows[TabID.IMAGES]] == ["redis"]
                )
            )
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.focused, DataTable)
            search = app.query_one(f"#{SEARCH_BAR_ID}", SearchBar)
            self.assertEqual(search.value, "red")
            self.assertEqual(
                [i.repository for i in app._tables.rows[TabID.IMAGES]], ["redis"]
            )

    async def test_enter_also_returns_focus(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            await pilot.press("slash", "a", "enter")
            await pilot.pause()
            self.assertIsInstance(app.focused, DataTable)

    async def test_escape_does_not_reset_the_cursor(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            await pilot.press("slash", "e", "escape")
            await pilot.pause(0.5)  # debounce settles
            table = app.query_one(f"#{TableID.IMAGES}", DataTable)
            table.move_cursor(row=1)
            await pilot.press("slash", "escape")
            await pilot.pause()
            self.assertEqual(table.cursor_row, 1)

    async def test_placeholder_follows_the_tab(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            search = app.query_one(f"#{SEARCH_BAR_ID}", SearchBar)
            self.assertIn("project", search.placeholder)
            await pilot.press("2")
            await wait_until(lambda: "repository" in search.placeholder)
            self.assertNotIn("🔍", search.placeholder)

    async def test_query_persists_across_a_refresh(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            await pilot.press("slash", "r", "e", "d", "escape")
            await wait_until(
                lambda: (
                    [i.repository for i in app._tables.rows[TabID.IMAGES]] == ["redis"]
                )
            )
            app.start_refresh()
            await pilot.pause(0.5)
            self.assertEqual(
                [i.repository for i in app._tables.rows[TabID.IMAGES]], ["redis"]
            )


class PanelTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_containers_title_is_grouped(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            self.assertEqual(panel.border_title, "containers · 3 · grouped by project")

    async def test_title_follows_tab_filter_and_sort(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            # Images start sorted largest first (no saved session).
            self.assertEqual(panel.border_title, "images · 2 · sorted by size ▾")
            await pilot.press("slash", "r", "e", "d")
            await wait_until(
                lambda: panel.border_title == "images · 1 of 2 · sorted by size ▾"
            )
            app._tables.cycle_sort(TabID.IMAGES, "Size")
            app._repaint_list_panel()
            self.assertEqual(panel.border_title, "images · 1 of 2 · sorted by size ▴")

    async def test_detail_pane_title_is_the_focused_resource(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot, TabID.IMAGES)
            pane = app.query_one("#detail-pane")
            await wait_until(lambda: "alpine" in str(pane.border_title))


class FrameStructureTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_chrome_is_gone_and_chip_bar_exists(self) -> None:
        from textual.widgets import Footer, Header

        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            self.assertEqual(len(app.query(Header)), 0)
            self.assertEqual(len(app.query(Footer)), 0)
            self.assertEqual(len(app.query(f"#{CHIP_BAR_ID}")), 1)
            # The tab strip is hidden: the top bar owns tab display.
            tabs = app.query_one(TabbedContent).query_one("Tabs")
            self.assertEqual(tabs.size.height, 0)

    async def test_focus_moves_panel_border_to_primary(self) -> None:
        app = _app()
        async with app.run_test(size=(140, 30)) as pilot:
            await _ready(app, pilot)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            focused = panel.styles.border_top[1]
            app.set_focus(None)
            await pilot.pause()
            self.assertNotEqual(panel.styles.border_top[1], focused)
