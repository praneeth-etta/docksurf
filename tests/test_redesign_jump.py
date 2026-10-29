"""Tide redesign, phase 03: `enter` jumps from an image / volume / network to
the container(s) using it. Assertions run inside the `run_test()` block."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from textual.widgets import DataTable, Input, OptionList, TabbedContent

from docksurf_py.core.models import (
    DockerSnapshot,
    Image,
    Network,
    NetworkEndpoint,
    Volume,
)
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import BOTTOM_BAR_ID, PICKER_LIST_ID, SEARCH_BAR_ID, TableID
from docksurf_py.tui.widgets import BottomBar, ContainerPickerScreen
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container


def _c(name: str, **changes):
    return replace(make_container(name), **changes)


def _snapshot() -> DockerSnapshot:
    web = _c(
        "web", image_id="sha256:web", labels=make_container("x", project="shop").labels
    )
    db = _c(
        "db",
        image_id="sha256:db",
        running=False,
        state="exited",
        status="exited",
        mounts=["pgdata"],
        networks=["backend"],
        labels=make_container("x", project="shop").labels,
    )
    worker_a = _c("worker-a", image_id="sha256:shared", networks=["backend"])
    worker_b = _c("worker-b", image_id="sha256:shared", networks=["backend"])
    solo = _c("solo", image_id="sha256:solo")
    images = [
        Image("sha256:web", "shop-web", "latest", 500, False, ["web"], ""),
        Image("sha256:shared", "worker", "1", 400, False, ["worker-a", "worker-b"], ""),
        Image("sha256:unused", "idle", "1", 300, False, [], ""),
        Image("sha256:solo", "solo", "1", 200, False, ["solo"], ""),
    ]
    return DockerSnapshot(
        containers=[web, db, worker_a, worker_b, solo],
        images=images,
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
                endpoints=[NetworkEndpoint("worker-a"), NetworkEndpoint("worker-b")],
            ),
            Network("n2", "empty", "bridge", "", "", "local", []),
        ],
    )


def _app() -> DockSurfApp:
    return DockSurfApp(docker=MockDockerService(_snapshot))


async def _open(app: DockSurfApp, pilot, tab: TabID, key: str) -> DataTable:
    await pilot.pause()
    await wait_until(lambda: app.snapshot is not None)
    await pilot.press(key)
    await wait_until(lambda: app.query_one(TabbedContent).active == tab)
    table = app.query_one(f"#{TableID(tab.value.replace('tab-', 'table-'))}", DataTable)
    await wait_until(lambda: table.row_count > 0)
    return table


def _focus_row(app: DockSurfApp, tab: TabID, table: DataTable, match) -> None:
    index = next(i for i, item in enumerate(app._tables.rows[tab]) if match(item))
    table.move_cursor(row=index)


async def _focused_container(app: DockSurfApp, name: str) -> None:
    await wait_until(
        lambda: (
            app.query_one(TabbedContent).active == TabID.CONTAINERS
            and getattr(app._get_focused_resource(TabID.CONTAINERS), "name", None)
            == name
        ),
        timeout=4.0,
    )


class JumpFromImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_on_an_in_use_image_lands_on_its_container_row(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "shop-web")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await _focused_container(app, "web")
            containers = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
            self.assertEqual(
                app._tables.rows[TabID.CONTAINERS][containers.cursor_row].name, "web"
            )

    async def test_the_collapsed_project_is_expanded(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            app._tables.toggle_collapse("shop")
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "shop-web")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await _focused_container(app, "web")
            self.assertFalse(app._tables.is_collapsed("shop"))

    async def test_a_chip_that_hides_the_container_is_cleared(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            app._tables.set_chip(TabID.CONTAINERS, "stopped")
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "solo")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await _focused_container(app, "solo")
            self.assertEqual(app._tables.active_chip[TabID.CONTAINERS], "all")

    async def test_a_filter_that_hides_the_container_is_cleared(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.IMAGES, "2")
            search = app.query_one(f"#{SEARCH_BAR_ID}", Input)
            # Matches the image `shop-web`, but not its container `web`.
            search.value = "shop-web"
            await wait_until(lambda: table.row_count == 1)
            await pilot.press("enter")
            await _focused_container(app, "web")
            self.assertEqual(search.value, "")
            containers = app.query_one(f"#{TableID.CONTAINERS}", DataTable)
            await wait_until(lambda: containers.row_count >= 5)

    async def test_an_unused_image_notifies_instead_of_jumping(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "idle")
            await pilot.pause(0.1)
            with patch.object(app, "notify") as notify:
                await pilot.press("enter")
                await wait_until(lambda: notify.called)
            self.assertIn("Nothing to jump to", notify.call_args.args[0])
            self.assertEqual(app.query_one(TabbedContent).active, TabID.IMAGES)

    async def test_several_containers_open_the_picker_then_jump(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "worker")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await wait_until(
                lambda: any(
                    isinstance(s, ContainerPickerScreen) for s in app.screen_stack
                )
            )
            picker = app.screen_stack[-1]
            assert isinstance(picker, ContainerPickerScreen)
            self.assertEqual(
                [cid for cid, _ in picker._options], ["worker-a", "worker-b"]
            )
            option_list = picker.query_one(f"#{PICKER_LIST_ID}", OptionList)
            option_list.highlighted = 1
            await pilot.press("enter")
            await _focused_container(app, "worker-b")

    async def test_cancelling_the_picker_stays_on_the_images_tab(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "worker")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await wait_until(
                lambda: any(
                    isinstance(s, ContainerPickerScreen) for s in app.screen_stack
                )
            )
            await pilot.press("escape")
            await wait_until(
                lambda: (
                    not any(
                        isinstance(s, ContainerPickerScreen) for s in app.screen_stack
                    )
                )
            )
            self.assertEqual(app.query_one(TabbedContent).active, TabID.IMAGES)


class JumpFromOtherTabsTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_on_a_volume_jumps_to_the_container_mounting_it(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.VOLUMES, "3")
            _focus_row(app, TabID.VOLUMES, table, lambda v: v.name == "pgdata")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await _focused_container(app, "db")  # a stopped container, in a project

    async def test_enter_on_a_network_with_two_endpoints_asks_which(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            table = await _open(app, pilot, TabID.NETWORKS, "4")
            _focus_row(app, TabID.NETWORKS, table, lambda n: n.name == "backend")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await wait_until(
                lambda: any(
                    isinstance(s, ContainerPickerScreen) for s in app.screen_stack
                )
            )
            picker = app.screen_stack[-1]
            assert isinstance(picker, ContainerPickerScreen)
            # `db` is on the network too, so all three are offered.
            self.assertEqual(
                [cid for cid, _ in picker._options], ["db", "worker-a", "worker-b"]
            )

    async def test_enter_on_the_containers_tab_does_not_jump(self) -> None:
        app = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: app.snapshot is not None)
            with patch.object(app, "action_jump_related") as jump:
                await pilot.press("enter")
                await pilot.pause(0.2)
            jump.assert_not_called()


def _bar(app: DockSurfApp) -> str:
    return " ".join(str(app.query_one(f"#{BOTTOM_BAR_ID}", BottomBar).content).split())


class JumpHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_hint_shows_only_for_rows_with_a_container(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 40)) as pilot:
            table = await _open(app, pilot, TabID.IMAGES, "2")
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "shop-web")
            await wait_until(lambda: "⏎ jump" in _bar(app))
            self.assertIn("h history", _bar(app))
            self.assertIn("P prune", _bar(app))
            _focus_row(app, TabID.IMAGES, table, lambda i: i.repository == "idle")
            await wait_until(lambda: "⏎ jump" not in _bar(app))
            self.assertIn("h history", _bar(app))

    async def test_hint_on_volumes_and_networks(self) -> None:
        app = _app()
        async with app.run_test(size=(200, 40)) as pilot:
            volumes = await _open(app, pilot, TabID.VOLUMES, "3")
            _focus_row(app, TabID.VOLUMES, volumes, lambda v: v.name == "pgdata")
            await wait_until(lambda: "⏎ jump" in _bar(app))
            _focus_row(app, TabID.VOLUMES, volumes, lambda v: v.name == "orphan")
            await wait_until(lambda: "⏎ jump" not in _bar(app))
            networks = await _open(app, pilot, TabID.NETWORKS, "4")
            _focus_row(app, TabID.NETWORKS, networks, lambda n: n.name == "backend")
            await wait_until(lambda: "⏎ jump" in _bar(app))
            _focus_row(app, TabID.NETWORKS, networks, lambda n: n.name == "empty")
            await wait_until(lambda: "⏎ jump" not in _bar(app))
