"""Unit tests for core/state — table state, row identity, search matching, log
filtering and export, and delete-command binding. Plain pytest, no Textual app."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from docksurf_py.core.models import (
    CommandResult,
    ComposeProject,
    DockerSnapshot,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.state.logs import LogBuffer, line_matches, write_log_export
from docksurf_py.core.state.ops import DeletePlan, bind_delete_command
from docksurf_py.core.state.tables import (
    ResourceKind,
    TablesState,
    matches_container,
    matches_image,
    matches_network,
    matches_volume,
    row_key,
)
from docksurf_py.core.types import LogLine, TabID
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


def _kinds() -> dict[TabID, ResourceKind]:
    return {
        TabID.CONTAINERS: ResourceKind(
            label="container",
            snapshot_items=lambda snap: snap.containers,
            matches=matches_container,
            sort_keys={"Name": lambda c: c.name.lower()},
            groups_by_project=True,
        ),
        TabID.IMAGES: ResourceKind(
            label="image",
            snapshot_items=lambda snap: snap.images,
            matches=matches_image,
            sort_keys={"Tag": lambda i: i.tag},
        ),
    }


def _snap(containers=(), images=()) -> DockerSnapshot:
    return DockerSnapshot(list(containers), list(images), [], [])


def _names(rows) -> list[str]:
    return [r.name if not isinstance(r, Image) else r.tag for r in rows]


class TablesStateRowTests(unittest.TestCase):
    def test_rows_keep_snapshot_order_when_unsorted(self) -> None:
        tables = TablesState(_kinds())
        snap = _snap([make_container("b"), make_container("a")])
        self.assertEqual(_names(tables.build_rows(TabID.CONTAINERS, snap)), ["b", "a"])
        self.assertEqual(_names(tables.rows[TabID.CONTAINERS]), ["b", "a"])

    def test_projects_group_before_standalone_and_collapse_hides_services(
        self,
    ) -> None:
        tables = TablesState(_kinds())
        snap = _snap(
            [
                make_container("solo"),
                make_container("shop-web", project="shop", service="web"),
                make_container("shop-db", project="shop", service="db"),
            ]
        )
        rows = tables.build_rows(TabID.CONTAINERS, snap)
        self.assertIsInstance(rows[0], ComposeProject)
        self.assertEqual(_names(rows), ["shop", "shop-db", "shop-web", "solo"])

        self.assertTrue(tables.toggle_collapse("shop"))
        self.assertEqual(
            _names(tables.build_rows(TabID.CONTAINERS, snap)), ["shop", "solo"]
        )
        self.assertFalse(tables.toggle_collapse("shop"))

    def test_query_filters_case_insensitively_before_grouping(self) -> None:
        tables = TablesState(_kinds())
        snap = _snap(
            [
                make_container("shop-web", project="shop", service="web"),
                make_container("shop-db", project="shop", service="db"),
                make_container("solo"),
            ]
        )
        rows = tables.build_rows(TabID.CONTAINERS, snap, query="WEB")
        self.assertEqual(_names(rows), ["shop", "shop-web"])
        self.assertEqual(rows[0].containers, [rows[1]])

    def test_sort_applies_to_services_within_a_project_and_standalone(self) -> None:
        tables = TablesState(_kinds())
        snap = _snap(
            [
                make_container("zeta"),
                make_container("alpha"),
                make_container("p-z", project="p", service="z"),
                make_container("p-a", project="p", service="a"),
            ]
        )
        tables.cycle_sort(TabID.CONTAINERS, "Name")
        tables.cycle_sort(TabID.CONTAINERS, "Name")  # descending
        self.assertEqual(
            _names(tables.build_rows(TabID.CONTAINERS, snap)),
            ["p", "p-z", "p-a", "zeta", "alpha"],
        )

    def test_index_of_finds_row_by_key(self) -> None:
        tables = TablesState(_kinds())
        tables.build_rows(TabID.CONTAINERS, _snap([make_container("a")]))
        self.assertEqual(tables.index_of(TabID.CONTAINERS, ("container", "a")), 0)
        self.assertIsNone(tables.index_of(TabID.CONTAINERS, ("container", "x")))


class TablesStateSortTests(unittest.TestCase):
    def test_cycle_sort_flips_direction_on_same_column(self) -> None:
        tables = TablesState(_kinds())
        self.assertEqual(tables.cycle_sort(TabID.IMAGES, "Tag"), ("Tag", False))
        self.assertEqual(tables.cycle_sort(TabID.IMAGES, "Tag"), ("Tag", True))
        self.assertEqual(tables.cycle_sort(TabID.IMAGES, "Tag"), ("Tag", False))

    def test_unsortable_column_leaves_state_unchanged(self) -> None:
        tables = TablesState(_kinds(), sort_state={TabID.IMAGES: ("Tag", True)})
        self.assertIsNone(tables.cycle_sort(TabID.IMAGES, "Size"))
        self.assertEqual(tables.sort(TabID.IMAGES), ("Tag", True))

    def test_restored_sort_for_unknown_column_keeps_snapshot_order(self) -> None:
        tables = TablesState(_kinds(), sort_state={TabID.IMAGES: ("Gone", False)})
        snap = _snap(images=[_image("x", "2"), _image("x", "1")])
        self.assertEqual(_names(tables.build_rows(TabID.IMAGES, snap)), ["2", "1"])


class TablesStateMarkTests(unittest.TestCase):
    def test_toggle_mark_reports_new_state(self) -> None:
        tables = TablesState(_kinds())
        key = ("container", "a")
        self.assertTrue(tables.toggle_mark(TabID.CONTAINERS, key))
        self.assertTrue(tables.is_marked(TabID.CONTAINERS, key))
        self.assertFalse(tables.toggle_mark(TabID.CONTAINERS, key))
        self.assertFalse(tables.has_marks(TabID.CONTAINERS))
        self.assertFalse(tables.is_marked(TabID.CONTAINERS, None))

    def test_apply_snapshot_prunes_vanished_marks_on_every_tab(self) -> None:
        tables = TablesState(_kinds())
        tables.mark(TabID.CONTAINERS, [("container", "a"), ("container", "gone")])
        tables.mark(TabID.IMAGES, [("image", "sha256:abc|x:old")])
        tables.apply_snapshot(_snap([make_container("a")]), TabID.CONTAINERS)
        self.assertEqual(tables.marked[TabID.CONTAINERS], {("container", "a")})
        self.assertEqual(tables.marked[TabID.IMAGES], set())

    def test_marked_items_resolve_against_snapshot(self) -> None:
        tables = TablesState(_kinds())
        a, b = make_container("a"), make_container("b")
        tables.mark(TabID.CONTAINERS, [("container", "b")])
        self.assertEqual(tables.marked_items(TabID.CONTAINERS, _snap([a, b])), [b])
        self.assertEqual(tables.marked_items(TabID.CONTAINERS, None), [])

    def test_clear_and_unmark(self) -> None:
        tables = TablesState(_kinds())
        tables.mark(TabID.CONTAINERS, [("container", "a"), ("container", "b")])
        tables.unmark(TabID.CONTAINERS, [("container", "a")])
        self.assertEqual(tables.marked[TabID.CONTAINERS], {("container", "b")})
        self.assertTrue(tables.clear_marks(TabID.CONTAINERS))
        self.assertFalse(tables.clear_marks(TabID.CONTAINERS))


class TablesStateDirtyTests(unittest.TestCase):
    def test_apply_snapshot_dirties_only_inactive_tabs(self) -> None:
        tables = TablesState(_kinds())
        tables.apply_snapshot(_snap(), TabID.CONTAINERS)
        self.assertFalse(tables.is_dirty(TabID.CONTAINERS))
        self.assertTrue(tables.is_dirty(TabID.IMAGES))
        tables.mark_clean(TabID.IMAGES)
        self.assertFalse(tables.is_dirty(TabID.IMAGES))

    def test_build_rows_does_not_clear_dirty(self) -> None:
        tables = TablesState(_kinds())
        tables.apply_snapshot(_snap(), TabID.CONTAINERS)
        tables.build_rows(TabID.IMAGES, _snap())
        self.assertTrue(tables.is_dirty(TabID.IMAGES))


def _log(*texts: str) -> list[LogLine]:
    return [LogLine(text=t, ts=f"t{i}") for i, t in enumerate(texts)]


class LogBufferTests(unittest.TestCase):
    def test_bounded_drops_oldest(self) -> None:
        buf = LogBuffer(maxlen=3)
        buf.ingest(_log("a", "b", "c", "d"), render=False)
        self.assertEqual([line.text for line in buf.lines], ["b", "c", "d"])

    def test_render_all_filters_and_counts_matches(self) -> None:
        buf = LogBuffer()
        buf.ingest(_log("ok", "ERR one", "err two"), render=False)
        self.assertEqual(len(buf.render_all()), 3)
        self.assertEqual(buf.match_count, 0)  # no filter, no matches
        buf.set_filter("err")
        self.assertEqual(len(buf.render_all()), 2)
        self.assertEqual(buf.match_count, 2)

    def test_ingest_counts_new_matches_only_when_rendering(self) -> None:
        buf = LogBuffer()
        buf.set_filter("x")
        self.assertEqual(buf.ingest(_log("x1", "y"), render=False), [])
        self.assertEqual(buf.match_count, 0)
        self.assertEqual(len(buf.ingest(_log("x2"), render=True)), 1)
        self.assertEqual(buf.match_count, 1)
        self.assertEqual(len(buf.lines), 3)

    def test_jump_wraps_and_noops_without_matches(self) -> None:
        buf = LogBuffer()
        self.assertIsNone(buf.jump(1))
        buf.ingest(_log("a1", "a2", "a3"), render=False)
        buf.set_filter("a")
        buf.render_all()
        self.assertEqual(buf.jump(-1), 2)  # first prev lands on the last
        self.assertEqual(buf.jump(1), 0)  # wraps forward
        self.assertEqual(buf.jump(-1), 2)  # wraps back

    def test_cursor_is_clamped_when_matches_shrink(self) -> None:
        buf = LogBuffer()
        buf.ingest(_log("a1", "a2", "a3"), render=False)
        buf.set_filter("a")
        buf.render_all()
        buf.jump(-1)
        buf.lines.clear()
        buf.lines.extend(_log("a1"))
        buf.render_all()
        self.assertEqual(buf.match_cursor, 0)

    def test_filter_hint_text(self) -> None:
        buf = LogBuffer()
        self.assertEqual(buf.filter_hint(), "")
        buf.ingest(_log("a", "a"), render=False)
        buf.set_filter("a")
        buf.render_all()
        self.assertEqual(buf.filter_hint(), "  |  filter: [bold]a[/]  |  2 matches")
        buf.jump(1)
        self.assertTrue(buf.filter_hint().endswith("match 1/2"))
        buf.set_filter("[z]")
        buf.render_all()
        self.assertEqual(
            buf.filter_hint(), "  |  filter: [bold]\\[z][/]  |  [dim]no matches[/]"
        )

    def test_reset_keeps_filter_only_when_asked(self) -> None:
        buf = LogBuffer()
        buf.ingest(_log("a"), render=False)
        buf.set_filter("a")
        buf.render_all()
        buf.reset(keep_filter=True)
        self.assertEqual((len(buf.lines), buf.filter, buf.match_count), (0, "a", 0))
        buf.reset(keep_filter=False)
        self.assertEqual(buf.filter, "")

    def test_export_ignores_filter(self) -> None:
        buf = LogBuffer()
        buf.ingest(_log("a", "b"), render=False)
        buf.set_filter("a")
        self.assertEqual(buf.export_text(), "t0 a\nt1 b")
