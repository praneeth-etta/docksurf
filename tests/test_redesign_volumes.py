"""Tide redesign, phase 04: the Volumes tab. Assertions run inside the
`run_test()` block — teardown resets live state."""

import threading
import unittest

from rich.console import Console
from rich.text import Text
from textual.content import Content
from textual.widgets import DataTable

from docksurf_py.core.config import Config
from docksurf_py.core.models import DiskUsageEntry, DockerSnapshot, SystemDf, Volume
from docksurf_py.core.state.tables import RowGroup
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    BOTTOM_BAR_ID,
    DETAIL_PANE_ID,
    DISK_PANEL_ID,
    LAYERS_PANEL_ID,
    LIST_PANEL_ID,
    LIVE_PANEL_ID,
    LOG_PANE_ID,
    SAFE_DELETE_PANEL_ID,
    TOOLBAR_LINE_ID,
    TableID,
)
from docksurf_py.tui.widgets import (
    BottomBar,
    ConfirmDialog,
    DetailPane,
    DiskPanel,
    PruneScreen,
    SafeDeletePanel,
    ToolbarLine,
)
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container

HASH_A = "a" * 64
HASH_B = "b" * 64


def _vol(name: str, used_by: list[str] | None = None, **labels: str) -> Volume:
    return Volume(
        name=name,
        driver="local",
        mountpoint="/m",
        used_by=used_by or [],
        labels=labels,
    )


def _snapshot() -> DockerSnapshot:
    volumes = [
        _vol("pgdata", ["shop-db"], **{"com.docker.compose.project": "shop"}),
        _vol("cache", ["web", "worker"]),
        _vol("scratch"),
        _vol(HASH_A),
        _vol(HASH_B),
    ]
    return DockerSnapshot([], [], volumes, [])


def _plain(markup: object) -> str:
    return Text.from_markup(str(markup)).plain


async def _open(app: DockSurfApp, pilot) -> DataTable:
    await pilot.pause()
    await wait_until(lambda: app.snapshot is not None)
    await pilot.press("3")
    table = app.query_one(f"#{TableID.VOLUMES}", DataTable)
    await wait_until(lambda: table.row_count > 0)
    return table


def _cells(table: DataTable, row: int) -> list[str]:
    return [_plain(c) for c in table.get_row_at(row)]


class VolumeTableTests(unittest.IsolatedAsyncioTestCase):
    async def test_groups_headers_and_volumes_in_order(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            names = [_cells(table, i)[1].strip() for i in range(table.row_count)]
            self.assertEqual(
                names,
                [
                    "● IN USE · 2",
                    "cache",
                    "pgdata",
                    "○ ORPHANED · 3",
                    "scratch",
                    "◇ aaaaaaaa…" + "a" * 14,
                    "◇ bbbbbbbb…" + "b" * 14,
                ],
            )

    async def test_columns_and_cells(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            labels = [str(c.label).strip() for c in table.columns.values()]
            self.assertEqual(
                labels, ["", "Name", "Project", "Driver", "Size", "Used by"]
            )
            pgdata = next(
                i for i in range(table.row_count) if _cells(table, i)[1] == "pgdata"
            )
            cells = _cells(table, pgdata)
            self.assertEqual(
                [c.strip() for c in cells[1:]],
                ["pgdata", "shop", "local", "· · ·", "shop-db"],
            )
            cache = next(
                i for i in range(table.row_count) if _cells(table, i)[1] == "cache"
            )
            self.assertEqual(_cells(table, cache)[5], "web +1")

    async def test_orphaned_header_offers_prune_and_headers_are_tinted(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            self.assertEqual(_cells(table, 0)[5], "")
            self.assertIn("prune orphans", _cells(table, 3)[5])
            self.assertEqual([_cells(table, i)[0] for i in (0, 3)], ["▾", "▾"])
            self.assertIn(0, table.row_tints)
            self.assertIn(3, table.row_tints)
            self.assertNotIn(1, table.row_tints)

    async def test_space_on_a_header_collapses_its_volumes(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            table.focus()
            table.move_cursor(row=3)  # ORPHANED
            await pilot.pause()
            await pilot.press("space")
            await wait_until(lambda: table.row_count == 4)
            self.assertEqual(_cells(table, 3)[0], "▸")
            self.assertEqual(table.cursor_row, 3)
            self.assertFalse(app._tables.has_marks(TabID.VOLUMES))
            await pilot.press("space")
            await wait_until(lambda: table.row_count == 7)
            self.assertEqual(_cells(table, 3)[0], "▾")

    async def test_cursor_gutter_follows_the_cursor(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            table.move_cursor(row=2)
            await wait_until(lambda: _cells(table, 2)[0] == "▌")

    async def test_sizes_fill_in_and_sum_per_group(self) -> None:
        svc = MockDockerService(_snapshot)
        svc.volume_size_map = {"cache": 2048, "pgdata": 1024, "scratch": 512}
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            app.action_volume_size()
            await wait_until(lambda: "3.00KB" in _cells(table, 0)[4])
            self.assertEqual(_cells(table, 3)[4].strip(), "512.00B")
            cache = _cells(table, 1)
            self.assertEqual(cache[1], "cache")
            self.assertEqual(cache[4].strip(), "2.00KB")
            self.assertEqual(_cells(table, 5)[4].strip(), "· · ·")


class VolumeChromeTests(unittest.IsolatedAsyncioTestCase):
    async def test_title_names_the_grouping(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            await wait_until(
                lambda: (
                    "grouped by usage"
                    in Content.from_markup(str(panel.border_title)).plain
                )
            )
            title = Content.from_markup(str(panel.border_title)).plain
            self.assertTrue(title.startswith("volumes · 5 · grouped by usage"))

    async def test_group_headers_are_not_counted_as_volumes(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            self.assertEqual(table.row_count, 7)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            title = Content.from_markup(str(panel.border_title)).plain
            self.assertIn("volumes · 5", title)

    async def test_toolbar_shows_how_many_sizes_are_known(self) -> None:
        svc = MockDockerService(_snapshot)
        svc.volume_size_map = {"cache": 1, "pgdata": 2}
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            line = app.query_one(f"#{TOOLBAR_LINE_ID}", ToolbarLine)
            await wait_until(lambda: line.display)
            await wait_until(lambda: "sizes 0/5  b size all" in line.content.plain)
            self.assertIn("named first · anonymous after", line.content.plain)
            app.action_volume_size()
            await wait_until(lambda: "sizes 2/5  b size all" in line.content.plain)

    async def test_footer_is_the_legend(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            await wait_until(
                lambda: (
                    "anonymous volume (hash name)"
                    in Content.from_markup(str(panel.border_subtitle or "")).plain
                )
            )

    async def test_marking_keeps_the_legend(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            table.focus()
            table.move_cursor(row=4)
            await pilot.pause()
            await pilot.press("space")
            await wait_until(lambda: app._tables.has_marks(TabID.VOLUMES))
            panel = app.query_one(f"#{LIST_PANEL_ID}")
            plain = Content.from_markup(str(panel.border_subtitle or "")).plain
            self.assertIn("anonymous volume", plain)

    async def test_the_line_and_title_follow_a_tab_change(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            line = app.query_one(f"#{TOOLBAR_LINE_ID}", ToolbarLine)
            await pilot.press("4")
            await wait_until(lambda: not line.display)
            await pilot.press("3")
            await wait_until(lambda: line.display)


class VolumeSortTests(unittest.IsolatedAsyncioTestCase):
    async def test_sorting_by_name_orders_within_each_group(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            app._tables.cycle_sort(TabID.VOLUMES, "Name")
            app._tables.cycle_sort(TabID.VOLUMES, "Name")  # descending
            app._rerender_active_table()
            await pilot.pause()
            rows = app._tables.rows[TabID.VOLUMES]
            self.assertEqual(
                [r.name if isinstance(r, Volume) else r.key for r in rows],
                ["in_use", "pgdata", "cache", "orphaned", "scratch", HASH_B, HASH_A],
            )
            self.assertTrue(isinstance(rows[0], RowGroup))


def _df() -> SystemDf:
    gb = 1024**3
    entries = [
        DiskUsageEntry("Images", 10, 4, 20 * gb, 12 * gb),
        DiskUsageEntry("Local Volumes", 5, 2, 5 * gb, 4 * gb),
        DiskUsageEntry("Containers", 3, 2, 1 * gb, 0),
        DiskUsageEntry("Build Cache", 9, 0, 10 * gb, 8 * gb),
    ]
    return SystemDf(entries, sum(e.size_bytes for e in entries), 24 * gb)


def _row_of(app: DockSurfApp, name: str) -> int:
    rows = app._tables.rows[TabID.VOLUMES]
    return next(i for i, r in enumerate(rows) if getattr(r, "name", None) == name)


def _title(widget) -> str:
    return Content.from_markup(str(widget.border_title)).plain


class VolumeSideColumnTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_follows_the_cursor(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            pane = app.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
            table.move_cursor(row=_row_of(app, "pgdata"))
            await wait_until(lambda: _title(pane) == "pgdata · volume")
            table.move_cursor(row=_row_of(app, HASH_A))
            await wait_until(lambda: _title(pane).startswith("◇ aaaaaaaa…aaaaaa"))

    async def test_in_use_volume_has_no_safe_to_delete_card(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            safe = app.query_one(f"#{SAFE_DELETE_PANEL_ID}", SafeDeletePanel)
            table.move_cursor(row=_row_of(app, "pgdata"))
            await pilot.pause(0.1)
            self.assertFalse(safe.display)

    async def test_orphan_shows_the_safe_to_delete_card(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            safe = app.query_one(f"#{SAFE_DELETE_PANEL_ID}", SafeDeletePanel)
            table.move_cursor(row=_row_of(app, "scratch"))
            await wait_until(lambda: safe.display)
            text = safe.content.plain
            self.assertIn("No container mounts this volume", text)
            self.assertIn("d delete this   P prune all 3 orphans", text)
            self.assertEqual(_title(safe), "safe to delete")
            self.assertTrue(safe.has_class("-warning"))

    async def test_card_leaves_when_the_cursor_moves_to_a_header_or_in_use(
        self,
    ) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            safe = app.query_one(f"#{SAFE_DELETE_PANEL_ID}", SafeDeletePanel)
            table.move_cursor(row=_row_of(app, "scratch"))
            await wait_until(lambda: safe.display)
            table.move_cursor(row=3)  # the ORPHANED header
            await wait_until(lambda: not safe.display)
            pane = app.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
            self.assertEqual(_title(pane), "")

    async def test_computed_sizes_reach_the_card_and_the_orphan_total(self) -> None:
        svc = MockDockerService(_snapshot)
        svc.volume_size_map = {"scratch": 2048, HASH_A: 2048, HASH_B: 2048}
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            safe = app.query_one(f"#{SAFE_DELETE_PANEL_ID}", SafeDeletePanel)
            table.move_cursor(row=_row_of(app, "scratch"))
            await wait_until(lambda: safe.display)
            self.assertIn("Deleting removes its data for good.", safe.content.plain)
            app.action_volume_size()
            await wait_until(lambda: "its 2.00KB of data" in safe.content.plain)
            self.assertIn("P prune all 3 orphans · 6.00KB", safe.content.plain)

    async def test_only_the_volumes_panels_show_on_the_volumes_tab(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            shown = {
                name: app.query_one(f"#{panel_id}").display
                for name, panel_id in {
                    "disk": DISK_PANEL_ID,
                    "layers": LAYERS_PANEL_ID,
                    "live": LIVE_PANEL_ID,
                    "logs": LOG_PANE_ID,
                }.items()
            }
            self.assertEqual(
                shown, {"disk": True, "layers": False, "live": False, "logs": False}
            )
            await pilot.press("2")
            await wait_until(lambda: not app.query_one(f"#{DISK_PANEL_ID}").display)
            await pilot.press("3")
            await wait_until(lambda: app.query_one(f"#{DISK_PANEL_ID}").display)

    async def test_safe_card_is_hidden_after_leaving_the_tab(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            safe = app.query_one(f"#{SAFE_DELETE_PANEL_ID}", SafeDeletePanel)
            table.move_cursor(row=_row_of(app, "scratch"))
            await wait_until(lambda: safe.display)
            await pilot.press("4")
            await wait_until(lambda: not safe.display)


class VolumeDiskPanelTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _text(app: DockSurfApp) -> str:
        content = app.query_one(f"#{DISK_PANEL_ID}", DiskPanel).content
        if hasattr(content, "plain"):
            return content.plain
        console = Console(width=100, no_color=True, highlight=False)
        with console.capture() as cap:
            console.print(content)
        return "\n".join(line.rstrip() for line in cap.get().splitlines())

    async def test_placeholder_until_the_df_lands_then_the_bars(self) -> None:
        svc = MockDockerService(_snapshot)
        gate = threading.Event()

        def slow_df() -> SystemDf:
            gate.wait(5)
            return _df()

        svc.system_df = slow_df  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            panel = app.query_one(f"#{DISK_PANEL_ID}", DiskPanel)
            await wait_until(lambda: "computing (w to refresh)" in self._text(app))
            self.assertEqual(_title(panel), "disk usage · docker system df")
            self.assertEqual(
                Content.from_markup(str(panel.border_subtitle)).plain, "w full report"
            )
            gate.set()
            await wait_until(lambda: "20.00GB · 12.00GB free-able" in self._text(app))
            lines = [line.split()[0] for line in self._text(app).splitlines()]
            self.assertEqual(lines, ["images", "build", "volumes", "containers"])

    async def test_the_df_fetched_for_images_is_reused(self) -> None:
        svc = MockDockerService(_snapshot)
        calls: list[int] = []

        def counting_df() -> SystemDf:
            calls.append(1)
            return _df()

        svc.system_df = counting_df  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(lambda: len(calls) == 1)
            await wait_until(lambda: app._details.system_df is not None)
            await pilot.press("3")
            await wait_until(lambda: "free-able" in self._text(app))
            self.assertEqual(len(calls), 1)


class OrphanPruneTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _dialogs(app: DockSurfApp) -> list[ConfirmDialog]:
        return [s for s in app.screen_stack if isinstance(s, ConfirmDialog)]

    @staticmethod
    def _removed(svc: MockDockerService) -> list[str]:
        return [c[1] for c in svc.calls if c[0] == "remove_volume"]

    async def _focus(self, app: DockSurfApp, pilot, name: str) -> None:
        table = await _open(app, pilot)
        table.focus()
        table.move_cursor(row=_row_of(app, name))
        await pilot.pause(0.1)

    async def test_p_on_the_orphaned_header_confirms_then_removes_each_orphan(
        self,
    ) -> None:
        svc = MockDockerService(_snapshot)
        svc.volume_size_map = {"scratch": 1024, HASH_A: 1024, HASH_B: 2048}
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            app.action_volume_size()
            await wait_until(lambda: app._details.volume_sizes != {})
            table.focus()
            table.move_cursor(row=3)  # the ORPHANED header
            await pilot.pause(0.1)
            await pilot.press("P")
            await wait_until(lambda: bool(self._dialogs(app)))
            self.assertEqual(
                self._dialogs(app)[0]._message,
                "Delete 3 orphaned volume(s) (4.00KB)? Their data is removed for good.",
            )
            self.assertEqual(self._removed(svc), [])  # nothing before the answer
            self._dialogs(app)[0].dismiss(True)
            await wait_until(lambda: len(self._removed(svc)) == 3)
            self.assertEqual(
                sorted(self._removed(svc)), sorted(["scratch", HASH_A, HASH_B])
            )
            self.assertNotIn(("prune_volumes",), svc.calls)

    async def test_the_in_use_volumes_are_never_removed(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            table.focus()
            table.move_cursor(row=3)
            await pilot.pause(0.1)
            await pilot.press("P")
            await wait_until(lambda: bool(self._dialogs(app)))
            self._dialogs(app)[0].dismiss(True)
            await wait_until(lambda: len(self._removed(svc)) == 3)
            self.assertFalse({"pgdata", "cache"} & set(self._removed(svc)))

    async def test_p_on_an_orphaned_volume_prunes_the_whole_group(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, "scratch")
            await pilot.press("P")
            await wait_until(lambda: bool(self._dialogs(app)))
            self.assertIn("Delete 3 orphaned volume(s)", self._dialogs(app)[0]._message)
            self._dialogs(app)[0].dismiss(True)
            await wait_until(lambda: len(self._removed(svc)) == 3)

    async def test_cancelling_removes_nothing(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, "scratch")
            await pilot.press("P")
            await wait_until(lambda: bool(self._dialogs(app)))
            self._dialogs(app)[0].dismiss(False)
            await pilot.pause(0.3)
            self.assertEqual(self._removed(svc), [])

    async def test_confirm_prune_off_skips_the_dialog(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc, config=Config(confirm_prune=False))
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, "scratch")
            await pilot.press("P")
            await wait_until(lambda: len(self._removed(svc)) == 3)
            self.assertEqual(self._dialogs(app), [])

    async def test_a_filtered_group_prunes_only_what_is_listed(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await _open(app, pilot)
            app._apply_filter("scratch")
            await pilot.pause(0.2)
            table = app.query_one(f"#{TableID.VOLUMES}", DataTable)
            table.focus()
            table.move_cursor(row=0)  # the ORPHANED header, the only group left
            await pilot.pause(0.1)
            await pilot.press("P")
            await wait_until(lambda: bool(self._dialogs(app)))
            self.assertIn("Delete 1 orphaned volume(s)", self._dialogs(app)[0]._message)
            self._dialogs(app)[0].dismiss(True)
            await wait_until(lambda: self._removed(svc) == ["scratch"])

    async def test_p_on_an_in_use_volume_still_opens_the_prune_picker(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, "pgdata")
            await pilot.press("P")
            await wait_until(
                lambda: any(isinstance(s, PruneScreen) for s in app.screen_stack)
            )
            self.assertEqual(self._dialogs(app), [])

    async def test_p_on_the_in_use_header_opens_the_prune_picker(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot)
            table.focus()
            table.move_cursor(row=0)
            await pilot.pause(0.1)
            await pilot.press("P")
            await wait_until(
                lambda: any(isinstance(s, PruneScreen) for s in app.screen_stack)
            )

    async def test_the_picker_volumes_target_says_it_is_anonymous_only(self) -> None:
        svc = MockDockerService(_snapshot)
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await self._focus(app, pilot, "pgdata")
            await pilot.press("P")
            await wait_until(
                lambda: any(isinstance(s, PruneScreen) for s in app.screen_stack)
            )
            screen = app.screen_stack[-1]
            self.assertIn("anonymous", screen._LABELS["volumes"])
            screen.dismiss("volumes")
            await wait_until(lambda: bool(self._dialogs(app)))
            message = self._dialogs(app)[0]._message
            self.assertIn("anonymous volumes", message)
            self.assertIn("Named volumes are kept", message)
            self._dialogs(app)[0].dismiss(True)
            await wait_until(lambda: ("prune_volumes",) in svc.calls)


class VolumeHintTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _bar(app: DockSurfApp) -> str:
        bar = app.query_one(f"#{BOTTOM_BAR_ID}", BottomBar)
        return " ".join(str(bar.content).split())

    async def test_volume_rows_show_the_designed_hints(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(200, 40)) as pilot:
            table = await _open(app, pilot)
            table.move_cursor(row=_row_of(app, "scratch"))
            await wait_until(lambda: "d delete" in self._bar(app))
            bar = self._bar(app)
            for hint in ("b size", "+ new", "space mark", "d delete", "P prune"):
                self.assertIn(hint, bar)
            self.assertNotIn("⏎ jump", bar)
            self.assertLess(bar.index("b size"), bar.index("P prune"))

    async def test_an_in_use_volume_adds_jump(self) -> None:
        def snapshot() -> DockerSnapshot:
            base = _snapshot()
            base.containers.append(make_container("shop-db"))  # mounts `pgdata`
            return base

        app = DockSurfApp(docker=MockDockerService(snapshot))
        async with app.run_test(size=(200, 40)) as pilot:
            table = await _open(app, pilot)
            table.move_cursor(row=_row_of(app, "pgdata"))
            await wait_until(lambda: "⏎ jump" in self._bar(app))

    async def test_a_group_header_offers_collapse_not_mark_or_delete(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(200, 40)) as pilot:
            table = await _open(app, pilot)
            table.move_cursor(row=0)
            await wait_until(lambda: "space collapse" in self._bar(app))
            bar = self._bar(app)
            self.assertIn("P prune", bar)
            self.assertNotIn("space mark", bar)
            self.assertNotIn("d delete", bar)
