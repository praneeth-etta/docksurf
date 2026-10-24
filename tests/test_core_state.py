"""Unit tests for core/state — row identity, search matching, log filtering and
export, and delete-command binding. Plain pytest, no Textual app."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from docksurf_py.core.models import (
    CommandResult,
    ComposeProject,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.state.logs import line_matches, write_log_export
from docksurf_py.core.state.ops import DeletePlan, bind_delete_command
from docksurf_py.core.state.tables import (
    matches_container,
    matches_image,
    matches_network,
    matches_volume,
    row_key,
)
from docksurf_py.core.types import LogLine
from tests.test_compose import make_container


def _image(repo: str, tag: str, image_id: str = "sha256:abc") -> Image:
    return Image(
        id=image_id,
        repository=repo,
        tag=tag,
        size_bytes=0,
        is_dangling=False,
        used_by=[],
        created="",
    )


def _volume(name: str, driver: str = "local", mountpoint: str = "/data") -> Volume:
    return Volume(
        name=name, driver=driver, mountpoint=mountpoint, used_by=[], labels={}
    )


def _network(name: str, driver: str = "bridge", subnet: str = "") -> Network:
    return Network(
        id=name,
        name=name,
        driver=driver,
        subnet=subnet,
        gateway="",
        scope="local",
        used_by=[],
    )


class RowKeyTests(unittest.TestCase):
    def test_each_resource_kind_gets_its_own_namespace(self) -> None:
        c = make_container("web")
        project = ComposeProject(
            name="shop", containers=[c], config_files="", working_dir=""
        )
        self.assertEqual(row_key(c), ("container", "web"))
        self.assertEqual(row_key(project), ("project", "shop"))
        self.assertEqual(row_key(_volume("data")), ("volume", "data"))
        self.assertEqual(row_key(_network("net")), ("network", "net"))

    def test_image_tags_sharing_an_id_get_distinct_keys(self) -> None:
        # BF-3: one image with two tags is two rows; marking/deleting one tag
        # row must not affect the other.
        latest = _image("nginx", "latest")
        stable = _image("nginx", "1.27")
        self.assertNotEqual(row_key(latest), row_key(stable))
        self.assertEqual(row_key(latest), ("image", "sha256:abc|nginx:latest"))

    def test_unknown_object_has_no_key(self) -> None:
        self.assertIsNone(row_key("not a resource"))
        self.assertIsNone(row_key(None))


class MatchesTests(unittest.TestCase):
    def test_container_matches_name_image_status_id_and_compose_fields(self) -> None:
        c = make_container("web-1", project="shop", service="frontend")
        for q in ("web", "img:latest", "running", "shop", "frontend"):
            with self.subTest(q=q):
                self.assertTrue(matches_container(c, q))
        self.assertFalse(matches_container(c, "postgres"))

    def test_query_is_expected_lowercased_by_caller(self) -> None:
        # The search controller lowercases the query once; matchers only
        # lowercase the fields.
        c = make_container("Web")
        self.assertTrue(matches_container(c, "web"))
        self.assertFalse(matches_container(c, "Web"))

    def test_image_matches_repository_or_tag_and_tolerates_none(self) -> None:
        self.assertTrue(matches_image(_image("nginx", "latest"), "ngi"))
        self.assertTrue(matches_image(_image("nginx", "1.27"), "1.27"))
        dangling = _image(None, None)  # type: ignore[arg-type]
        self.assertFalse(matches_image(dangling, "x"))

    def test_volume_matches_name_driver_or_mountpoint(self) -> None:
        v = _volume("pgdata", driver="local", mountpoint="/var/lib/docker/v")
        for q in ("pgdata", "local", "/var/lib"):
            with self.subTest(q=q):
                self.assertTrue(matches_volume(v, q))
        self.assertFalse(matches_volume(v, "nfs"))

    def test_network_matches_name_driver_or_subnet(self) -> None:
        n = _network("backend", driver="overlay", subnet="10.0.0.0/24")
        for q in ("back", "overlay", "10.0.0"):
            with self.subTest(q=q):
                self.assertTrue(matches_network(n, q))
        self.assertFalse(matches_network(n, "bridge"))


class LineMatchesTests(unittest.TestCase):
    def test_empty_term_matches_everything(self) -> None:
        self.assertTrue(line_matches(LogLine(text="anything"), ""))

    def test_case_insensitive_on_message_only(self) -> None:
        line = LogLine(text="Connection REFUSED", ts="2026-10-24T10:00", service="db")
        self.assertTrue(line_matches(line, "refused"))
        self.assertFalse(line_matches(line, "2026"))  # timestamp ignored
        self.assertFalse(line_matches(line, "db"))  # service prefix ignored


class WriteLogExportTests(unittest.TestCase):
    def test_writes_under_exports_with_sanitized_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("docksurf_py.core.state.logs.DATA_DIR", Path(tmp)):
                path = write_log_export("my app/web:1", "line one\nline two")
            self.assertEqual(path.parent, Path(tmp) / "exports")
            self.assertTrue(path.name.startswith("my_app_web_1-"))
            self.assertTrue(path.name.endswith(".log"))
            self.assertEqual(path.read_text(encoding="utf-8"), "line one\nline two")

    def test_name_with_no_safe_characters_falls_back_to_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("docksurf_py.core.state.logs.DATA_DIR", Path(tmp)):
                path = write_log_export("///", "")
            self.assertTrue(path.name.startswith("logs-"))


class BindDeleteCommandTests(unittest.TestCase):
    def test_binds_the_given_force_flag(self) -> None:
        command = MagicMock(return_value=CommandResult(ok=True, message="ok"))
        plan = DeletePlan(
            confirm_message="Remove?",
            command=command,
            success_message="Removed",
            force_default=True,
        )
        result = bind_delete_command(plan, force=False)()
        command.assert_called_once_with(False)
        self.assertTrue(result.ok)
