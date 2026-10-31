"""Characterization tests for write-action decisions: delete plans and
refusals, bulk runs and their summaries, and container verb guards.

Pins today's messages, dialogs and Docker calls so moving the decisions out
of the Textual mixins (local/UI_SEPARATION_PLAN.md, Phase 3c) can be checked
against it. Only behavior the existing app tests don't already cover.
"""

import unittest
from unittest.mock import patch

from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.models import (
    CommandErrorKind,
    CommandResult,
    DockerSnapshot,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import TableID
from docksurf_py.tui.widgets import ConfirmDialog
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container

TABLES = {
    TabID.CONTAINERS: TableID.CONTAINERS,
    TabID.IMAGES: TableID.IMAGES,
    TabID.VOLUMES: TableID.VOLUMES,
    TabID.NETWORKS: TableID.NETWORKS,
}


def _image(repo: str, tag: str, used_by: list[str] | None = None) -> Image:
    return Image(
        id=f"sha256:{repo}",
        repository=repo,
        tag=tag,
        size_bytes=1,
        is_dangling=repo == "<none>",
        used_by=used_by or [],
        created="",
    )


def _volume(name: str, used_by: list[str] | None = None) -> Volume:
    return Volume(
        name=name, driver="local", mountpoint="/m", used_by=used_by or [], labels={}
    )


def _network(name: str) -> Network:
    return Network(
        id=name,
        name=name,
        driver="bridge",
        subnet="",
        gateway="",
        scope="local",
        used_by=[],
    )


async def _focus(app: DockSurfApp, pilot, tab: TabID, row: int = 0) -> DataTable:
    await wait_until(lambda: app.snapshot is not None and not app._refresh.in_progress)
    await pilot.pause()
    tabs = app.query_one(TabbedContent)
    table = app.query_one(f"#{TABLES[tab]}", DataTable)
    for _ in range(20):
        if tabs.active != tab:
            tabs.active = tab
        await pilot.pause()
        if tabs.active == tab and table.row_count > 0:
            break
    table.focus()
    table.move_cursor(row=row)
    await pilot.pause()
    return table


async def _mark_rows(app: DockSurfApp, pilot, tab: TabID, rows: list[int]) -> None:
    table = app.query_one(f"#{TABLES[tab]}", DataTable)
    for row in rows:
        table.move_cursor(row=row)
        await pilot.pause()
        app.action_toggle_mark()
        await pilot.pause()


async def _dialog(app: DockSurfApp) -> ConfirmDialog:
    await wait_until(lambda: isinstance(app.screen_stack[-1], ConfirmDialog))
    return app.screen_stack[-1]


def _messages(notify) -> list[tuple[str, str | None]]:
    return [(c.args[0], c.kwargs.get("severity")) for c in notify.call_args_list]


class DeletePlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_builtin_network_is_refused_without_a_dialog(self) -> None:
        svc = MockDockerService(
            lambda: DockerSnapshot([], [], [], [_network("bridge")])
        )
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.NETWORKS, row=1)
            with patch.object(app, "notify") as notify:
                app.action_delete()
                await pilot.pause()
                self.assertEqual(
                    _messages(notify),
                    [("Cannot remove built-in network 'bridge'", "warning")],
                )
            self.assertFalse(isinstance(app.screen_stack[-1], ConfirmDialog))
            self.assertNotIn("remove_network", [c[0] for c in svc.calls])

    async def test_in_use_tagged_image_dialog_wording_and_remove_ref(self) -> None:
        svc = MockDockerService(
            lambda: DockerSnapshot(
                [], [_image("nginx", "1.27", used_by=["web"])], [], []
            )
        )
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.IMAGES)
            app.action_delete()
            dialog = await _dialog(app)
            self.assertEqual(
                dialog._message,
                "Force-remove IN-USE image 'nginx:1.27'? Untags only — the image "
                "is removed once its last tag is gone.",
            )
            self.assertTrue(dialog._force_default)
            dialog.dismiss((True, True))
            await wait_until(lambda: ("remove_image", "nginx:1.27", True) in svc.calls)

    async def test_dangling_image_is_removed_by_id_with_plain_wording(self) -> None:
        svc = MockDockerService(
            lambda: DockerSnapshot([], [_image("<none>", "<none>")], [], [])
        )
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.IMAGES)
            app.action_delete()
            dialog = await _dialog(app)
            self.assertEqual(dialog._message, "Remove image '<none>:<none>'?")
            self.assertFalse(dialog._force_default)
            dialog.dismiss((True, False))
            await wait_until(
                lambda: ("remove_image", "sha256:<none>", False) in svc.calls
            )

    async def test_failed_delete_reports_error_and_not_found_refreshes(self) -> None:
        svc = MockDockerService(lambda: DockerSnapshot([], [], [_volume("data")], []))
        svc.remove_volume = lambda name: CommandResult.failure(  # type: ignore[method-assign]
            "no such volume", CommandErrorKind.NOT_FOUND
        )
        refreshes: list[int] = []
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            # Row 0 is the group header; the volume is row 1.
            await _focus(app, pilot, TabID.VOLUMES, row=1)
            original = app.start_refresh
            app.start_refresh = lambda: (refreshes.append(1), original())[1]  # type: ignore[method-assign]
            with patch.object(app, "notify") as notify:
                app.action_delete()
                dialog = await _dialog(app)
                self.assertEqual(dialog._message, "Remove volume 'data'?")
                dialog.dismiss(True)
                await wait_until(lambda: bool(notify.call_args_list))
                self.assertEqual(
                    _messages(notify), [("Error: no such volume", "error")]
                )
            self.assertEqual(len(refreshes), 1)


class BulkTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_delete_skips_refused_items_and_previews_the_rest(self) -> None:
        vols = [_volume("a"), _volume("busy", used_by=["web"]), _volume("c")]
        svc = MockDockerService(lambda: DockerSnapshot([], [], vols, []))
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.VOLUMES)
            # IN USE header, busy, ORPHANED header, a, c: mark the three volumes.
            await _mark_rows(app, pilot, TabID.VOLUMES, [1, 3, 4])
            with patch.object(app, "notify") as notify:
                app.action_delete()
                dialog = await _dialog(app)
                self.assertEqual(dialog._message, "Delete 2 volume(s)? a, c")
                self.assertEqual(
                    _messages(notify),
                    [("Volume 'busy' is in use by web — stop them first", "warning")],
                )
                dialog.dismiss(True)
                await wait_until(lambda: ("remove_volume", "c") in svc.calls)
                await wait_until(lambda: len(notify.call_args_list) == 2)
                self.assertEqual(_messages(notify)[1], ("Deleted 2/2", None))
            self.assertEqual(app._tables.marked[TabID.VOLUMES], {("volume", "busy")})

    async def test_bulk_delete_with_nothing_eligible_warns_and_clears_marks(
        self,
    ) -> None:
        svc = MockDockerService(
            lambda: DockerSnapshot([], [], [], [_network("host"), _network("none")])
        )
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.NETWORKS)
            await _mark_rows(app, pilot, TabID.NETWORKS, [1, 2])
            with patch.object(app, "notify") as notify:
                app.action_delete()
                await wait_until(lambda: len(notify.call_args_list) == 3)
                self.assertEqual(
                    _messages(notify)[-1],
                    ("No marked networks eligible to delete", "warning"),
                )
            self.assertFalse(app._tables.has_marks(TabID.NETWORKS))

    async def test_bulk_partial_failure_summary_and_marks(self) -> None:
        snap = DockerSnapshot(
            [make_container(n) for n in ("a", "b", "c", "d", "e")], [], [], []
        )
        svc = MockDockerService(lambda: snap)
        failing = {"b", "c", "d", "e"}

        def stop(container_id: str) -> CommandResult:
            svc.calls.append(("stop_container", container_id))
            if container_id in failing:
                return CommandResult.failure(f"boom {container_id}")
            return CommandResult.success()

        svc.stop_container = stop  # type: ignore[method-assign]
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.CONTAINERS)
            await _mark_rows(app, pilot, TabID.CONTAINERS, [0, 1, 2, 3, 4])
            with patch.object(app, "notify") as notify:
                app.action_stop_container()
                await wait_until(lambda: bool(notify.call_args_list), timeout=3.0)
                self.assertEqual(
                    _messages(notify),
                    [
                        (
                            "Stop 1/5 — failed: b (boom b), c (boom c), "
                            "d (boom d), +1 more",
                            "error",
                        )
                    ],
                )
            # Every executed job is unmarked, failed or not.
            self.assertFalse(app._tables.has_marks(TabID.CONTAINERS))

    async def test_bulk_verb_with_nothing_eligible_warns_and_clears_marks(
        self,
    ) -> None:
        snap = DockerSnapshot(
            [make_container("a", running=False), make_container("b", running=False)],
            [],
            [],
            [],
        )
        svc = MockDockerService(lambda: snap)
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.CONTAINERS)
            await _mark_rows(app, pilot, TabID.CONTAINERS, [0, 1])
            with patch.object(app, "notify") as notify:
                app.action_stop_container()
                await pilot.pause()
                self.assertEqual(
                    _messages(notify),
                    [("No marked containers eligible to stop", "warning")],
                )
            self.assertFalse(app._tables.has_marks(TabID.CONTAINERS))
            self.assertNotIn("stop_container", [c[0] for c in svc.calls])


class ContainerGuardTests(unittest.IsolatedAsyncioTestCase):
    async def _guarded(
        self, running: bool, action: str
    ) -> list[tuple[str, str | None]]:
        snap = DockerSnapshot([make_container("web", running=running)], [], [], [])
        svc = MockDockerService(lambda: snap)
        app = DockSurfApp(docker=svc)
        async with app.run_test() as pilot:
            await _focus(app, pilot, TabID.CONTAINERS)
            with patch.object(app, "notify") as notify:
                getattr(app, action)()
                await pilot.pause()
                messages = _messages(notify)
            self.assertNotIn(action.removeprefix("action_"), [c[0] for c in svc.calls])
            return messages

    async def test_stop_on_stopped_container_is_guarded(self) -> None:
        self.assertEqual(
            await self._guarded(False, "action_stop_container"),
            [("web is not running", "information")],
        )

    async def test_start_on_running_container_is_guarded(self) -> None:
        self.assertEqual(
            await self._guarded(True, "action_start_container"),
            [("web is already running", "information")],
        )
