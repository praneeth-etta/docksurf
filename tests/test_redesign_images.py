"""Tide redesign, phase 03: the Images tab. Assertions run inside the
`run_test()` block — teardown resets live state."""

import unittest

from rich.console import Console
from rich.text import Text
from textual.content import Content
from textual.widgets import DataTable, Static, TabbedContent

from docksurf_py.core.models import (
    DiskUsageEntry,
    DockerSnapshot,
    Image,
    ImageLayer,
    ImagePlatform,
    SystemDf,
)
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import (
    CHIP_BAR_ID,
    EMPTY_STATE_IDS,
    LAYERS_PANEL_ID,
    LIST_PANEL_ID,
    TOOLBAR_LINE_ID,
    TableID,
)
from docksurf_py.tui.widgets import ChipBar, LayersPanel, ToolbarLine
from tests.test_app import MockDockerService, _image, wait_until
from tests.test_compose import make_container


def _snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[],
        images=[_image("alpine"), _image("redis", image_id="sha256:r")],
        volumes=[],
        networks=[],
    )


def _df() -> SystemDf:
    return SystemDf(
        entries=[DiskUsageEntry("Images", 2, 1, 100, 40)],
        total_size=100,
        total_reclaimable=40,
    )


class SystemDfFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_df_is_fetched_once_while_switching_tabs(self) -> None:
        svc = MockDockerService(_snapshot)
        calls: list[int] = []

        def system_df() -> SystemDf:
            calls.append(1)
            return _df()

        svc.system_df = system_df  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            self.assertEqual(calls, [])  # not needed on the Containers tab
            for key, tab in (
                ("2", TabID.IMAGES),
                ("3", TabID.VOLUMES),
                ("2", TabID.IMAGES),
                ("1", TabID.CONTAINERS),
            ):
                await pilot.press(key)
                await wait_until(lambda: app.query_one(TabbedContent).active == tab)
            await wait_until(lambda: app._details.system_df is not None)
            await pilot.pause()
            self.assertEqual(len(calls), 1)
            self.assertEqual(app._details.system_df, _df())

    async def test_w_action_stores_its_result_in_the_cache(self) -> None:
        svc = MockDockerService(_snapshot)
        svc.system_df = _df  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            app.action_system_df()
            await wait_until(lambda: app._details.system_df is not None)

    async def test_docker_event_marks_the_df_stale(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_snapshot))
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            app._details.store_system_df(_df(), 0.0)
            app._on_docker_event()
            self.assertTrue(app._details._system_df_stale)


class DiskLineTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _text(app: DockSurfApp) -> str:
        return app.query_one(f"#{TOOLBAR_LINE_ID}", ToolbarLine).content.plain

    async def test_line_is_only_shown_on_the_images_tab(self) -> None:
        svc = MockDockerService(_snapshot)
        svc.system_df = _df  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            line = app.query_one(f"#{TOOLBAR_LINE_ID}", ToolbarLine)
            await wait_until(lambda: not line.display)
            await pilot.press("2")
            await wait_until(lambda: line.display)
            await pilot.press("1")  # Containers have no images line
            await wait_until(lambda: not line.display)

    async def test_line_shows_the_df_figures_once_fetched(self) -> None:
        svc = MockDockerService(_snapshot)
        svc.system_df = _df  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(lambda: "reclaimable 40.00B" in self._text(app))
            text = self._text(app)
            self.assertTrue(text.startswith("100.00B on disk"))
            self.assertIn("in use 60.00B", text)
            self.assertTrue(text.rstrip().endswith("P prune"))

    async def test_line_falls_back_to_the_snapshot_sum_while_df_is_unknown(
        self,
    ) -> None:
        svc = MockDockerService(_snapshot)  # df stays empty -> never cached
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(lambda: "on disk" in self._text(app))
            self.assertTrue(self._text(app).startswith("≈200.00B on disk"))


def _chip_snapshot() -> DockerSnapshot:
    return DockerSnapshot(
        containers=[],
        images=[
            Image("sha256:a", "app", "latest", 10, False, ["web"], ""),
            Image("sha256:b", "app", "old", 10, False, [], ""),
            Image("sha256:d", "<none>", "<none>", 10, True, [], ""),
        ],
        volumes=[],
        networks=[],
    )


class ImageChipTests(unittest.IsolatedAsyncioTestCase):
    async def _open_images(self, app: DockSurfApp, pilot) -> DataTable:
        await pilot.pause()
        await wait_until(lambda: app.snapshot is not None)
        await pilot.press("2")
        await wait_until(lambda: app.query_one(TabbedContent).active == TabID.IMAGES)
        table = app.query_one(f"#{TableID.IMAGES}", DataTable)
        await wait_until(lambda: table.row_count == 3)
        return table

    async def test_chip_bar_shows_the_image_counts(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_chip_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await self._open_images(app, pilot)
            text = " ".join(
                str(app.query_one(f"#{CHIP_BAR_ID}", ChipBar).content).split()
            )
            self.assertEqual(text, "all 3 in use 1 unused 1 dangling 1")

    async def test_f_cycles_and_filters_the_table(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_chip_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            table = await self._open_images(app, pilot)
            await pilot.press("F")
            await wait_until(lambda: app._tables.active_chip[TabID.IMAGES] == "in_use")
            await wait_until(lambda: table.row_count == 1)
            await app.run_action("set_chip('dangling')")
            await wait_until(lambda: table.row_count == 1)
            self.assertEqual(app._get_focused_resource(TabID.IMAGES).is_dangling, True)

    async def test_empty_chip_message_uses_the_label(self) -> None:
        snapshot = _chip_snapshot()
        snapshot.images = snapshot.images[:2]  # nothing dangling
        app = DockSurfApp(docker=MockDockerService(lambda: snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(
                lambda: app.query_one(TabbedContent).active == TabID.IMAGES
            )
            await app.run_action("set_chip('dangling')")
            empty = app.query_one(f"#{EMPTY_STATE_IDS[TabID.IMAGES]}", Static)
            await wait_until(lambda: empty.display)
            self.assertIn("No dangling images", str(empty.content))


class ImageColumnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_columns_gutter_alignment_and_default_sort(self) -> None:
        snapshot = _chip_snapshot()
        snapshot.images[0].size_bytes = 5
        snapshot.images[1].size_bytes = 900
        snapshot.images[2].size_bytes = 70
        app = DockSurfApp(docker=MockDockerService(lambda: snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            table = app.query_one(f"#{TableID.IMAGES}", DataTable)
            await wait_until(lambda: table.row_count == 3)
            # Largest first by default.
            self.assertEqual(
                [i.size_bytes for i in app._tables.rows[TabID.IMAGES]], [900, 70, 5]
            )
            labels = [str(c.label).strip() for c in table.ordered_columns]
            self.assertEqual(
                labels, ["", "Repository", "Tag", "Size ▼", "Created", "Status"]
            )
            sizes = [Text.from_markup(table.get_row_at(i)[3]).plain for i in range(3)]
            self.assertEqual({len(s) for s in sizes}, {12})  # right-aligned, padded
            gutter = Text.from_markup(table.get_row_at(table.cursor_row)[0]).plain
            self.assertEqual(gutter, "▌")


class MarkedFooterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _footer(app: DockSurfApp) -> str:
        subtitle = app.query_one(f"#{LIST_PANEL_ID}").border_subtitle
        return Content.from_markup(str(subtitle or "")).plain

    async def _open(self, app: DockSurfApp, pilot) -> DataTable:
        await pilot.pause()
        await wait_until(lambda: app.snapshot is not None)
        await pilot.press("2")
        table = app.query_one(f"#{TableID.IMAGES}", DataTable)
        await wait_until(lambda: table.row_count == 3)
        return table

    async def test_footer_follows_marks_and_deduplicates_shared_images(self) -> None:
        snapshot = _chip_snapshot()
        for image in snapshot.images:
            image.size_bytes = 1024**2
        snapshot.images[1] = Image("sha256:a", "app", "old", 1024**2, False, [], "")
        app = DockSurfApp(docker=MockDockerService(lambda: snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await self._open(app, pilot)
            self.assertEqual(
                self._footer(app), "registry prefixes shortened · full name in detail"
            )
            await pilot.press("space", "space")  # marks two rows, cursor moves on
            await wait_until(lambda: "2 marked" in self._footer(app))
            ids = {i.id for i in app._marked_items(TabID.IMAGES)}
            expected = "1.00MB" if len(ids) == 1 else "2.00MB"
            self.assertIn(f"✓ 2 marked · {expected}", self._footer(app))
            self.assertIn("d delete marked", self._footer(app))
            await pilot.press("escape")
            await wait_until(lambda: "marked" not in self._footer(app).split("·")[0])
            self.assertIn("registry prefixes shortened", self._footer(app))

    async def test_mark_dangling_updates_the_footer(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_chip_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await self._open(app, pilot)
            await pilot.press("a")
            await wait_until(lambda: "1 marked" in self._footer(app))

    async def test_other_tabs_have_no_footer(self) -> None:
        app = DockSurfApp(docker=MockDockerService(_chip_snapshot))
        async with app.run_test(size=(160, 30)) as pilot:
            await self._open(app, pilot)
            await pilot.press("1")  # Containers have no footer
            await wait_until(lambda: self._footer(app) == "")


class ImageCardTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _snapshot() -> DockerSnapshot:
        used = Image("img", "shop-app", "latest", 5000, False, ["web"], "")
        also = Image("img", "shop-app", "1.0", 5000, False, ["web"], "")
        return DockerSnapshot(
            containers=[make_container("web", project="shop", service="web")],
            images=[used, also],
            volumes=[],
            networks=[],
        )

    @staticmethod
    def _text(app: DockSurfApp) -> str:
        pane = app.query_one("#detail-pane")
        console = Console(width=80, no_color=True, highlight=False)
        with console.capture() as cap:
            console.print(pane._panel.content)
        return cap.get()

    async def test_card_shows_platform_tags_and_used_by_once_settled(self) -> None:
        svc = MockDockerService(self._snapshot)
        svc.platform_map["img"] = ImagePlatform("arm64", "linux")
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(lambda: "arm64" in self._text(app), timeout=4.0)
            text = self._text(app)
            self.assertIn("IN USE", text)
            self.assertIn("linux", text)
            self.assertIn("latest, 1.0", text)
            self.assertIn("shop › web", text)
            self.assertIn("⏎ jump", text)
            pane = app.query_one("#detail-pane")
            self.assertIn("shop-app:", str(pane.border_title))
            self.assertEqual(
                [c for c in svc.calls if c[0] == "image_platform"],
                [("image_platform", "img")],  # one fetch for both tag rows
            )


class LayersPanelTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _snapshot() -> DockerSnapshot:
        return DockerSnapshot(
            containers=[],
            images=[
                Image("sha256:big", "big", "1", 900, False, [], ""),
                Image("sha256:small", "small", "1", 5, False, [], ""),
            ],
            volumes=[],
            networks=[],
        )

    @staticmethod
    def _text(app: DockSurfApp) -> str:
        panel = app.query_one(f"#{LAYERS_PANEL_ID}", LayersPanel)
        console = Console(width=80, no_color=True, highlight=False)
        with console.capture() as cap:
            console.print(panel.render())
        return cap.get()

    @staticmethod
    def _title(app: DockSurfApp) -> str:
        return str(app.query_one(f"#{LAYERS_PANEL_ID}", LayersPanel).border_title)

    @staticmethod
    def _history_calls(svc: MockDockerService) -> list:
        return [c for c in svc.calls if c[0] == "image_history"]

    async def test_panel_is_shown_on_the_images_tab_only(self) -> None:
        app = DockSurfApp(docker=MockDockerService(self._snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            panel = app.query_one(f"#{LAYERS_PANEL_ID}", LayersPanel)
            self.assertFalse(panel.display)
            await pilot.press("2")
            await wait_until(lambda: panel.display)
            await pilot.press("3")
            await wait_until(lambda: not panel.display)

    async def test_layers_load_once_the_cursor_settles_and_are_cached(self) -> None:
        svc = MockDockerService(self._snapshot)
        svc.history_layers = [
            ImageLayer("/bin/sh -c #(nop)  CMD x", 0, "0"),
            ImageLayer("/bin/sh -c apk add curl", 4096, "0"),
        ]
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(
                lambda: self._title(app) == "layers · 2 · largest first", timeout=4.0
            )
            panel = app.query_one(f"#{LAYERS_PANEL_ID}", LayersPanel)
            self.assertEqual(panel.border_subtitle, "h full history")
            self.assertEqual(
                self._history_calls(svc), [("image_history", "sha256:big")]
            )
            # Moving away and back reuses the cache: no second fetch for "big".
            await pilot.press("down")
            await wait_until(lambda: len(self._history_calls(svc)) == 2, timeout=4.0)
            await pilot.press("up")
            await pilot.pause(0.6)
            self.assertEqual(
                self._history_calls(svc),
                [("image_history", "sha256:big"), ("image_history", "sha256:small")],
            )

    async def test_unavailable_history_says_so_and_is_not_retried(self) -> None:
        svc = MockDockerService(self._snapshot)
        svc.image_history = lambda image_id: (  # type: ignore[method-assign]
            svc.calls.append(("image_history", image_id)) or None
        )
        app = DockSurfApp(docker=svc)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            await pilot.press("2")
            await wait_until(lambda: "unavailable" in self._text(app), timeout=4.0)
            await pilot.pause(0.6)
            self.assertEqual(len(self._history_calls(svc)), 1)
