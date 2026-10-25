"""Unit tests for core/state/ops — delete plans and refusals, bulk runs, and
container verb guards. Plain pytest, no Textual app."""

import unittest
from dataclasses import replace
from unittest.mock import MagicMock

from docksurf_py.core.models import (
    CommandResult,
    ComposeProject,
    Image,
    Network,
    NetworkEndpoint,
    Volume,
)
from docksurf_py.core.state.ops import (
    BulkResult,
    DeletePlan,
    Refusal,
    already_running_reason,
    not_running_reason,
    pause_verb,
    plan_container_delete,
    plan_image_delete,
    plan_network_delete,
    plan_volume_delete,
    rebuild_skip_reason,
    run_bulk,
)
from tests.test_compose import make_container


def _docker() -> MagicMock:
    docker = MagicMock()
    for name in ("remove_container", "remove_image", "remove_volume", "remove_network"):
        getattr(docker, name).return_value = CommandResult.success()
    return docker


def _image(repo: str, tag: str, used_by: list[str] | None = None) -> Image:
    return Image(
        id="sha256:x",
        repository=repo,
        tag=tag,
        size_bytes=1,
        is_dangling=repo == "<none>",
        used_by=used_by or [],
        created="",
    )


def _network(name: str, endpoints: list[str] | None = None) -> Network:
    return Network(
        id=name,
        name=name,
        driver="bridge",
        subnet="",
        gateway="",
        scope="local",
        used_by=[],
        endpoints=[NetworkEndpoint(container_name=n) for n in endpoints or []],
    )


class DeletePlanTests(unittest.TestCase):
    def test_running_container_defaults_to_force(self) -> None:
        docker = _docker()
        plan = plan_container_delete(docker, make_container("web"))
        assert isinstance(plan, DeletePlan)
        self.assertTrue(plan.force_default)
        self.assertIn("RUNNING", plan.confirm_message)
        plan.command(True)
        docker.remove_container.assert_called_once_with("web", force=True)

    def test_project_header_has_no_container_plan(self) -> None:
        header = ComposeProject(
            name="p", containers=[], config_files="", working_dir=""
        )
        self.assertIsNone(plan_container_delete(_docker(), header))

    def test_names_are_escaped_in_messages(self) -> None:
        plan = plan_container_delete(_docker(), make_container("[x]", running=False))
        assert isinstance(plan, DeletePlan)
        self.assertEqual(plan.confirm_message, "Remove container '\\[x]'?")

    def test_tagged_image_untags_by_ref_dangling_removes_by_id(self) -> None:
        docker = _docker()
        tagged = plan_image_delete(docker, _image("nginx", "1"))
        self.assertIn("Untags only", tagged.confirm_message)
        tagged.command(False)
        docker.remove_image.assert_called_with("nginx:1", force=False)
        dangling = plan_image_delete(docker, _image("<none>", "<none>"))
        self.assertNotIn("Untags only", dangling.confirm_message)
        dangling.command(False)
        docker.remove_image.assert_called_with("sha256:x", force=False)

    def test_in_use_volume_is_refused_naming_blockers(self) -> None:
        vol = Volume(
            name="data", driver="local", mountpoint="/m", used_by=["a", "b"], labels={}
        )
        refusal = plan_volume_delete(_docker(), vol)
        self.assertEqual(
            refusal, Refusal("Volume 'data' is in use by a, b — stop them first")
        )

    def test_builtin_networks_are_refused(self) -> None:
        for name in ("bridge", "host", "none"):
            with self.subTest(name=name):
                self.assertIsInstance(
                    plan_network_delete(_docker(), _network(name)), Refusal
                )

    def test_network_force_disconnects_endpoints_first(self) -> None:
        docker = _docker()
        plan = plan_network_delete(docker, _network("app", endpoints=["web", "db"]))
        assert isinstance(plan, DeletePlan)
        self.assertTrue(plan.force_default)
        plan.command(True)
        self.assertEqual(docker.disconnect_container.call_count, 2)
        docker.remove_network.assert_called_once_with("app")

    def test_network_without_endpoints_has_plain_dialog(self) -> None:
        plan = plan_network_delete(_docker(), _network("app"))
        assert isinstance(plan, DeletePlan)
        self.assertIsNone(plan.force_default)


class BulkTests(unittest.TestCase):
    def test_run_bulk_tallies_and_records_every_executed_key(self) -> None:
        jobs = [
            (("container", "a"), "a", lambda: CommandResult.success()),
            (("container", "b"), "b", lambda: CommandResult.failure("nope")),
        ]
        result = run_bulk(jobs)
        self.assertEqual(result.ok_count, 1)
        self.assertEqual(result.failures, [("b", "nope")])
        self.assertEqual(result.executed_keys, {("container", "a"), ("container", "b")})

    def test_summary_success_and_capped_failure_list(self) -> None:
        self.assertEqual(
            BulkResult(total=2, ok_count=2).summary("Stop"), ("Stop 2/2", False)
        )
        result = BulkResult(total=4, failures=[(n, "x") for n in "abcd"])
        self.assertEqual(
            result.summary("Deleted"),
            ("Deleted 0/4 — failed: a (x), b (x), c (x), +1 more", True),
        )

    def test_summary_escapes_names(self) -> None:
        result = BulkResult(total=1, failures=[("[n]", "m")])
        self.assertIn("\\[n] (m)", result.summary("Stop")[0])


class GuardTests(unittest.TestCase):
    def test_running_guards(self) -> None:
        up, down = make_container("web"), make_container("web", running=False)
        self.assertIsNone(not_running_reason(up))
        self.assertEqual(not_running_reason(down), "web is not running")
        self.assertEqual(already_running_reason(up), "web is already running")
        self.assertIsNone(already_running_reason(down))

    def test_pause_verb(self) -> None:
        paused = replace(make_container("w", running=False), state="paused")
        self.assertEqual(pause_verb(paused), "unpause")
        self.assertEqual(pause_verb(make_container("w")), "pause")
        self.assertIsNone(pause_verb(make_container("w", running=False)))

    def test_rebuild_skip_reason(self) -> None:
        c = make_container("p-db", project="p", service="db")
        self.assertIsNone(rebuild_skip_reason(c, None))
        self.assertIsNone(rebuild_skip_reason(c, {"db"}))
        self.assertEqual(
            rebuild_skip_reason(c, {"web"}),
            "No build defined for service 'db' — nothing to rebuild",
        )
