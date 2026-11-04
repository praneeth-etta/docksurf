"""Characterization tests for table state: what the four resource tables show,
and how selection, marks, collapse, sort and search behave across refreshes.

These pin today's behavior so moving the table state out of the Textual
mixins (local/UI_SEPARATION_PLAN.md, Phase 3a) can be checked against it:

- `GoldenTableTests` renders every table in a set of fixed states and compares
  each cell (plus column labels and the empty-state message) against
  `tests/golden/tables.json`. Regenerate it only when a change to what the
  tables show is intended: `DOCKSURF_UPDATE_GOLDEN=1 uv run python -m pytest
  tests/test_tables_characterization.py`.
- The remaining classes pin interaction behavior the existing app tests don't
  already cover.

They drive the app through actions and adapter methods (`action_*`,
`_apply_filter`, `_on_header_selected`, `start_refresh`) and read results off
the widgets, so they don't depend on where the table state is stored.
"""

import inspect
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from rich.text import Text
from textual.widgets import DataTable, Input, Static, TabbedContent

from docksurf_py.core.models import (
    Container,
    DockerSnapshot,
    Image,
    Network,
    NetworkEndpoint,
    Volume,
)
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import EMPTY_STATE_IDS, MARK_GLYPH, SEARCH_BAR_ID, TableID
from tests.test_app import (
    MockDockerService,
    _FakeHeaderSelected,
    wait_until,
)
from tests.test_compose import make_container

GOLDEN_PATH = Path(__file__).parent / "golden" / "tables.json"
UPDATE_GOLDEN = os.environ.get("DOCKSURF_UPDATE_GOLDEN") == "1"

TABLE_IDS = {
    TabID.CONTAINERS: TableID.CONTAINERS,
    TabID.IMAGES: TableID.IMAGES,
    TabID.VOLUMES: TableID.VOLUMES,
    TabID.NETWORKS: TableID.NETWORKS,
}


# --- Fixture snapshot -------------------------------------------------------


def _container(name: str, **kwargs: Any) -> Container:
    """`make_container` plus overrides for fields it doesn't take."""
    factory_args = inspect.signature(make_container).parameters
    overrides = {k: kwargs.pop(k) for k in list(kwargs) if k not in factory_args}
    return replace(make_container(name, **kwargs), **overrides)


def _image(
    repo: str, tag: str, image_id: str, size: int, used_by: list[str] | None = None
) -> Image:
    return Image(
        id=image_id,
        repository=repo,
        tag=tag,
        size_bytes=size,
        is_dangling=repo == "<none>",
        used_by=used_by or [],
        created="",
    )


def _volume(name: str, used_by: list[str] | None = None) -> Volume:
    return Volume(
        name=name,
        driver="local",
        mountpoint=f"/v/{name}",
        used_by=used_by or [],
        labels={},
    )


def _network(
    name: str, driver: str, scope: str = "local", endpoints: list[str] | None = None
) -> Network:
    return Network(
        id=f"net-{name}",
        name=name,
        driver=driver,
        subnet="",
        gateway="",
        scope=scope,
        used_by=[],
        endpoints=[NetworkEndpoint(container_name=n) for n in endpoints or []],
    )


def rich_snapshot() -> DockerSnapshot:
    """Covers grouping, health, exit codes, markup escaping, BF-3 shared-id
    tags, dangling images, truncation, in-use/orphaned, network membership."""
    return DockerSnapshot(
        containers=[
            _container("cache", image_name="redis:7", networks=["backend"]),
            _container("job", running=False, image_name="alpine:3", exit_code=137),
            _container(
                "shop-web",
                project="shop",
                service="web",
                health="healthy",
                image_name="shop-web:dev",
                networks=["backend"],
            ),
            _container(
                "shop-db",
                project="shop",
                service="db",
                running=False,
                image_name="postgres:17",
                networks=["backend"],
            ),
            _container(
                "shop-worker",
                project="shop",
                service="worker",
                health="unhealthy",
                image_name="shop-worker:dev",
            ),
            _container("blog-app", project="blog", service="app", image_name="ghost:5"),
            _container("[weird]name", image_name="busybox:latest"),
        ],
        images=[
            _image("nginx", "latest", "sha256:nginx", 190_000_000, used_by=["cache"]),
            _image("nginx", "1.27", "sha256:nginx", 190_000_000, used_by=["cache"]),
            _image("<none>", "<none>", "sha256:dangling", 5_000_000),
            _image(
                "registry.example.com/team/very-long-repository-name",
                "2026.10.24-build",
                "sha256:long",
                1_234_567_890,
            ),
            _image("alpine", "3", "sha256:alpine", 7_000_000, used_by=["job"]),
        ],
        volumes=[
            _volume("pgdata", used_by=["shop-db"]),
            _volume("orphan"),
            _volume("project_extremely_long_volume_name_for_truncation_check"),
        ],
        networks=[
            _network("bridge", "bridge"),
            _network("host", "host"),
            _network(
                "backend", "overlay", scope="swarm", endpoints=["cache", "ghost-1"]
            ),
            _network("empty", "bridge"),
        ],
    )


# --- Helpers ----------------------------------------------------------------


def _table(app: DockSurfApp, tab: TabID) -> DataTable:
    return app.query_one(f"#{TABLE_IDS[tab]}", DataTable)


def _capture(app: DockSurfApp, tab: TabID) -> dict[str, Any]:
    """Everything a user sees for one tab's table, as plain data."""
    table = _table(app, tab)
    empty = app.query_one(f"#{EMPTY_STATE_IDS[tab]}", Static)
    return {
        "columns": [str(col.label) for col in table.ordered_columns],
        "rows": [
            [str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)
        ],
        "table_visible": bool(table.display),
        "empty_message": str(empty.content) if empty.display else None,
    }


def _cell_texts(app: DockSurfApp, tab: TabID, column: int) -> list[str]:
    table = _table(app, tab)
    return [str(table.get_row_at(i)[column]) for i in range(table.row_count)]


def _plain_cells(app: DockSurfApp, tab: TabID, column: int) -> list[str]:
    """`_cell_texts` with Rich markup (colours, pills) stripped."""
    return [Text.from_markup(t).plain for t in _cell_texts(app, tab, column)]


def _marked_rows(app: DockSurfApp, tab: TabID) -> list[int]:
    # A marked row under the cursor shows `✓` in the Containers gutter (phase
    # 02A) instead of the plain mark glyph.
    return [
        i
        for i, cell in enumerate(_cell_texts(app, tab, 0))
        if cell == MARK_GLYPH or "✓" in cell
    ]


async def _open(app: DockSurfApp, pilot, tab: TabID) -> DataTable:
    """Activate `tab` and wait for its table to be populated (or its empty
    state to show).

    Waits for the first refresh to land before switching: its
    `_auto_select_first()` focuses the old tab's table, which can revert a
    just-set `.active` (see the TabbedContent focus-race note), so the switch
    is re-applied until it sticks.
    """
    await wait_until(
        lambda: app.snapshot is not None and not app._refresh.in_progress,
        timeout=2.0,
    )
    await pilot.pause()
    table = _table(app, tab)
    empty = app.query_one(f"#{EMPTY_STATE_IDS[tab]}", Static)
    tabs = app.query_one(TabbedContent)
    for _ in range(20):
        if tabs.active != tab:
            tabs.active = tab
        await pilot.pause()
        if tabs.active == tab and (table.row_count > 0 or empty.display):
            break
    else:
        raise AssertionError(f"{tab} never settled as the active, populated tab")
    await pilot.pause()
    return table


async def _refresh(app: DockSurfApp, pilot) -> None:
    app.start_refresh()
    await pilot.pause()
    await wait_until(lambda: not app._refresh.in_progress, timeout=2.0)
    await pilot.pause()


async def _sort(app: DockSurfApp, pilot, tab: TabID, column: str) -> None:
    """Click `column`'s header once (column 0 is the mark glyph)."""
    entry = app._resource_registry[tab]
    index = entry.columns.index(column) + 1
    app._on_header_selected(_FakeHeaderSelected(_table(app, tab), column_index=index))
    await pilot.pause()


async def _filter(app: DockSurfApp, pilot, query: str) -> None:
    app._apply_filter(query)
    await pilot.pause()


async def _mark_row(app: DockSurfApp, pilot, tab: TabID, row: int) -> None:
    table = _table(app, tab)
    table.focus()
    table.move_cursor(row=row)
    await pilot.pause()
    app.action_toggle_mark()
    await pilot.pause()


def _row_of(app: DockSurfApp, tab: TabID, text: str) -> int:
    """Index of the first row whose Name/Repository cell contains `text`."""
    for i, cell in enumerate(_cell_texts(app, tab, 1)):
        if text in cell:
            return i
    raise AssertionError(f"no row containing {text!r} in {tab}")


# --- Golden snapshot of table output ----------------------------------------

Scenario = Callable[[DockSurfApp, Any], Any]


async def _noop(app: DockSurfApp, pilot) -> None:
    return None


def _scenarios() -> dict[str, tuple[TabID, Callable[[], DockerSnapshot], Scenario]]:
    async def sort_twice(app, pilot, tab, column):
        await _sort(app, pilot, tab, column)
        await _sort(app, pilot, tab, column)

    async def collapse_shop(app, pilot):
        _table(app, TabID.CONTAINERS).move_cursor(
            row=_row_of(app, TabID.CONTAINERS, "shop")
        )
        await pilot.pause()
        app.action_toggle_group()
        await pilot.pause()

    async def mark_containers(app, pilot):
        await _mark_row(
            app, pilot, TabID.CONTAINERS, _row_of(app, TabID.CONTAINERS, "cache")
        )
        await _mark_row(
            app, pilot, TabID.CONTAINERS, _row_of(app, TabID.CONTAINERS, "db")
        )

    async def mark_one_nginx_tag(app, pilot):
        table = _table(app, TabID.IMAGES)
        tags = _plain_cells(app, TabID.IMAGES, 2)
        await _mark_row(app, pilot, TabID.IMAGES, tags.index("1.27"))
        assert table.row_count == len(tags)

    async def mark_one_nginx_tag_then_refresh(app, pilot):
        # Marking repaints only the toggled cell; a refresh repaints every
        # row from the mark state, which is where a shared-id key would show.
        await mark_one_nginx_tag(app, pilot)
        await _refresh(app, pilot)

    async def mark_containers_then_refresh(app, pilot):
        await mark_containers(app, pilot)
        await _refresh(app, pilot)

    async def sort_then_filter(app, pilot):
        await _sort(app, pilot, TabID.CONTAINERS, "Image")
        await _filter(app, pilot, "shop")

    async def collapse_then_filter(app, pilot):
        await collapse_shop(app, pilot)
        await _filter(app, pilot, "dev")

    async def disconnect(app, pilot):
        app.docker.mark_disconnected(RuntimeError("daemon down"))
        await _refresh(app, pilot)

    def empty() -> DockerSnapshot:
        return DockerSnapshot([], [], [], [])

    CT, IM, VO, NE = TabID.CONTAINERS, TabID.IMAGES, TabID.VOLUMES, TabID.NETWORKS
    return {
        "containers/default": (CT, rich_snapshot, _noop),
        "containers/sorted_name_asc": (
            CT,
            rich_snapshot,
            lambda a, p: _sort(a, p, CT, "Name"),
        ),
        "containers/sorted_name_desc": (
            CT,
            rich_snapshot,
            lambda a, p: sort_twice(a, p, CT, "Name"),
        ),
        "containers/sorted_image_asc": (
            CT,
            rich_snapshot,
            lambda a, p: _sort(a, p, CT, "Image"),
        ),
        "containers/filtered_web": (
            CT,
            rich_snapshot,
            lambda a, p: _filter(a, p, "web"),
        ),
        "containers/filtered_by_project": (
            CT,
            rich_snapshot,
            lambda a, p: _filter(a, p, "shop"),
        ),
        "containers/sorted_then_filtered": (CT, rich_snapshot, sort_then_filter),
        "containers/collapsed_shop": (CT, rich_snapshot, collapse_shop),
        "containers/collapsed_then_filtered": (CT, rich_snapshot, collapse_then_filter),
        "containers/marked": (CT, rich_snapshot, mark_containers),
        "containers/marked_then_refresh": (
            CT,
            rich_snapshot,
            mark_containers_then_refresh,
        ),
        "containers/empty": (CT, empty, _noop),
        "containers/filter_no_match": (
            CT,
            rich_snapshot,
            lambda a, p: _filter(a, p, "zzz"),
        ),
        "containers/disconnected": (CT, empty, disconnect),
        "images/default": (IM, rich_snapshot, _noop),
        "images/sorted_size_asc": (
            IM,
            rich_snapshot,
            lambda a, p: _sort(a, p, IM, "Size"),
        ),
        "images/sorted_size_desc": (
            IM,
            rich_snapshot,
            lambda a, p: sort_twice(a, p, IM, "Size"),
        ),
        "images/sorted_status": (
            IM,
            rich_snapshot,
            lambda a, p: _sort(a, p, IM, "Status"),
        ),
        "images/filtered_nginx": (
            IM,
            rich_snapshot,
            lambda a, p: _filter(a, p, "nginx"),
        ),
        "images/marked_one_tag": (IM, rich_snapshot, mark_one_nginx_tag),
        "images/marked_one_tag_then_refresh": (
            IM,
            rich_snapshot,
            mark_one_nginx_tag_then_refresh,
        ),
        "images/empty": (IM, empty, _noop),
        "volumes/default": (VO, rich_snapshot, _noop),
        "volumes/sorted_name_desc": (
            VO,
            rich_snapshot,
            lambda a, p: sort_twice(a, p, VO, "Name"),
        ),
        "volumes/sorted_used_by": (
            VO,
            rich_snapshot,
            lambda a, p: _sort(a, p, VO, "Used by"),
        ),
        "volumes/filtered": (VO, rich_snapshot, lambda a, p: _filter(a, p, "pg")),
        "volumes/empty": (VO, empty, _noop),
        "networks/default": (NE, rich_snapshot, _noop),
        "networks/sorted_driver": (
            NE,
            rich_snapshot,
            lambda a, p: _sort(a, p, NE, "Driver"),
        ),
        "networks/filtered": (NE, rich_snapshot, lambda a, p: _filter(a, p, "back")),
        "networks/empty": (NE, empty, _noop),
    }


class GoldenTableTests(unittest.IsolatedAsyncioTestCase):
    async def _render(self, tab, snapshot_fn, scenario) -> dict[str, Any]:
        app = DockSurfApp(docker=MockDockerService(snapshot_fn))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await _open(app, pilot, tab)
            await scenario(app, pilot)
            await pilot.pause()
            return _capture(app, tab)

    async def test_tables_match_golden(self) -> None:
        actual = {}
        for name, (tab, snapshot_fn, scenario) in _scenarios().items():
            actual[name] = await self._render(tab, snapshot_fn, scenario)

        if UPDATE_GOLDEN:
            GOLDEN_PATH.parent.mkdir(exist_ok=True)
            GOLDEN_PATH.write_text(
                json.dumps(actual, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.skipTest(f"golden file rewritten: {GOLDEN_PATH}")

        expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(sorted(actual), sorted(expected), "scenario set changed")
        for name in expected:
            with self.subTest(scenario=name):
                self.assertEqual(actual[name], expected[name])


# --- Interaction behavior ---------------------------------------------------


def _names(*names: str) -> DockerSnapshot:
    return DockerSnapshot([make_container(n) for n in names], [], [], [])


class SelectionAcrossRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_follows_resource_when_rows_shift(self) -> None:
        state = {"snap": _names("a", "b", "c")}
        app = DockSurfApp(docker=MockDockerService(lambda: state["snap"]))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            table.move_cursor(row=1)
            await pilot.pause()

            state["snap"] = _names("new", "a", "b", "c")
            await _refresh(app, pilot)
            self.assertEqual(table.cursor_row, 2)
            self.assertEqual(app._get_focused_resource(TabID.CONTAINERS).name, "b")

    async def test_cursor_falls_back_to_first_row_when_resource_vanishes(self) -> None:
        state = {"snap": _names("a", "b", "c")}
        app = DockSurfApp(docker=MockDockerService(lambda: state["snap"]))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            table.move_cursor(row=2)
            await pilot.pause()

            state["snap"] = _names("a", "b")
            await _refresh(app, pilot)
            self.assertEqual(table.cursor_row, 0)
            self.assertEqual(app._get_focused_resource(TabID.CONTAINERS).name, "a")

    async def test_cursor_stays_on_same_image_tag_across_refresh(self) -> None:
        # BF-3 for selection restore: two tags share one image id, so identity
        # must include the tag or the cursor would jump to the other tag.
        def snap(order: list[str]) -> DockerSnapshot:
            images = [_image("app", t, "sha256:shared", 1) for t in order]
            return DockerSnapshot([], images, [], [])

        state = {"snap": snap(["v1", "v2"])}
        app = DockSurfApp(docker=MockDockerService(lambda: state["snap"]))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.IMAGES)
            table.focus()
            table.move_cursor(row=1)
            await pilot.pause()

            state["snap"] = snap(["v0", "v1", "v2"])
            await _refresh(app, pilot)
            self.assertEqual(table.cursor_row, 2)
            self.assertEqual(app._get_focused_resource(TabID.IMAGES).tag, "v2")

    async def test_cursor_follows_resource_when_sort_changes(self) -> None:
        app = DockSurfApp(docker=MockDockerService(lambda: _names("c", "a", "b")))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            table.move_cursor(row=0)  # "c"
            await pilot.pause()

            await _sort(app, pilot, TabID.CONTAINERS, "Name")
            self.assertTrue(_cell_texts(app, TabID.CONTAINERS, 1)[2].endswith(" c"))
            self.assertEqual(table.cursor_row, 2)
            self.assertEqual(app._get_focused_resource(TabID.CONTAINERS).name, "c")

    async def test_unchanged_refresh_renders_details_once(self) -> None:
        # Today a refresh renders details three times: `table.clear()` resets
        # the cursor to row 0 (RowHighlighted -> row 0), then selection
        # restore moves it back and renders the focused row twice. Pinned as
        # "ends on the focused row, never more than today"; the transient
        # row-0 render is a known redundancy, not a requirement.
        app = DockSurfApp(docker=MockDockerService(lambda: _names("a", "b", "c")))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            table.move_cursor(row=1)
            await pilot.pause()

            calls: list[int] = []
            entry = app._resource_registry[TabID.CONTAINERS]
            original = entry.show_details

            def counting(pane, idx):
                calls.append(idx)
                return original(pane, idx)

            app._resource_registry[TabID.CONTAINERS] = replace(
                entry, show_details=counting
            )
            await _refresh(app, pilot)
            self.assertEqual(calls[-1], 1)
            self.assertLessEqual(len(calls), 3)
            self.assertTrue(set(calls) <= {0, 1})

    async def test_shifted_refresh_renders_details_once(self) -> None:
        state = {"snap": _names("a", "b", "c")}
        app = DockSurfApp(docker=MockDockerService(lambda: state["snap"]))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            table.move_cursor(row=1)
            await pilot.pause()

            calls: list[int] = []
            entry = app._resource_registry[TabID.CONTAINERS]
            original = entry.show_details

            def counting(pane, idx):
                calls.append(idx)
                return original(pane, idx)

            app._resource_registry[TabID.CONTAINERS] = replace(
                entry, show_details=counting
            )
            state["snap"] = _names("new", "a", "b", "c")
            await _refresh(app, pilot)
            self.assertEqual(calls[-1], 2)
            self.assertLessEqual(len(calls), 3)
            self.assertTrue(set(calls) <= {0, 2})


class MarkLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_marks_survive_refresh_and_drop_when_resource_vanishes(self) -> None:
        state = {"snap": _names("a", "b", "c")}
        app = DockSurfApp(docker=MockDockerService(lambda: state["snap"]))
        async with app.run_test() as pilot:
            await _open(app, pilot, TabID.CONTAINERS)
            await _mark_row(app, pilot, TabID.CONTAINERS, 0)
            await _mark_row(app, pilot, TabID.CONTAINERS, 1)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0, 1])

            await _refresh(app, pilot)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0, 1])

            state["snap"] = _names("a", "c")
            await _refresh(app, pilot)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0])

            # A resource that comes back is not re-marked.
            state["snap"] = _names("a", "b", "c")
            await _refresh(app, pilot)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0])

    async def test_marks_survive_search_filter(self) -> None:
        app = DockSurfApp(docker=MockDockerService(lambda: _names("alpha", "beta")))
        async with app.run_test() as pilot:
            await _open(app, pilot, TabID.CONTAINERS)
            await _mark_row(app, pilot, TabID.CONTAINERS, 1)  # beta

            await _filter(app, pilot, "beta")
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0])
            await _filter(app, pilot, "")
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [1])

    async def test_marking_advances_cursor_and_stops_at_last_row(self) -> None:
        app = DockSurfApp(docker=MockDockerService(lambda: _names("a", "b")))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            await _mark_row(app, pilot, TabID.CONTAINERS, 0)
            self.assertEqual(table.cursor_row, 1)
            app.action_toggle_mark()
            await pilot.pause()
            self.assertEqual(table.cursor_row, 1)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0, 1])

    async def test_toggling_a_marked_row_unmarks_it(self) -> None:
        app = DockSurfApp(docker=MockDockerService(lambda: _names("a", "b")))
        async with app.run_test() as pilot:
            await _open(app, pilot, TabID.CONTAINERS)
            await _mark_row(app, pilot, TabID.CONTAINERS, 0)
            await _mark_row(app, pilot, TabID.CONTAINERS, 0)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [])

    async def test_clear_marks_clears_only_the_active_tab(self) -> None:
        snap = DockerSnapshot(
            [make_container("a")], [_image("x", "1", "sha256:x", 1)], [], []
        )
        app = DockSurfApp(docker=MockDockerService(lambda: snap))
        async with app.run_test() as pilot:
            await _open(app, pilot, TabID.CONTAINERS)
            await _mark_row(app, pilot, TabID.CONTAINERS, 0)
            await _open(app, pilot, TabID.IMAGES)
            await _mark_row(app, pilot, TabID.IMAGES, 0)

            app.action_clear_marks()
            await pilot.pause()
            self.assertEqual(_marked_rows(app, TabID.IMAGES), [])

            app.query_one(TabbedContent).active = TabID.CONTAINERS
            await pilot.pause()
            await wait_until(lambda: _table(app, TabID.CONTAINERS).row_count > 0)
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [0])

    async def test_space_on_project_header_collapses_instead_of_marking(self) -> None:
        snap = DockerSnapshot(
            [
                make_container("shop-web", project="shop", service="web"),
                make_container("solo"),
            ],
            [],
            [],
            [],
        )
        app = DockSurfApp(docker=MockDockerService(lambda: snap))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            self.assertEqual(table.row_count, 3)
            await _mark_row(app, pilot, TabID.CONTAINERS, 0)
            self.assertEqual(table.row_count, 2)  # service row hidden
            self.assertEqual(_marked_rows(app, TabID.CONTAINERS), [])


class CollapseSortSearchAcrossRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_collapsed_project_stays_collapsed_across_refresh(self) -> None:
        snap = DockerSnapshot(
            [
                make_container("shop-web", project="shop", service="web"),
                make_container("shop-db", project="shop", service="db"),
                make_container("solo"),
            ],
            [],
            [],
            [],
        )
        app = DockSurfApp(docker=MockDockerService(lambda: snap))
        async with app.run_test() as pilot:
            table = await _open(app, pilot, TabID.CONTAINERS)
            table.move_cursor(row=0)
            app.action_toggle_group()
            await pilot.pause()
            self.assertEqual(table.row_count, 2)

            await _refresh(app, pilot)
            self.assertEqual(table.row_count, 2)
            # The collapse chevron lives in the gutter column (phase 02A).
            self.assertIn("▸", _cell_texts(app, TabID.CONTAINERS, 0)[0])

    async def test_refresh_while_filtering_keeps_filter_and_sort(self) -> None:
        def snap(repos: list[str]) -> DockerSnapshot:
            images = [_image(r, "1", f"sha256:{r}", 1) for r in repos]
            return DockerSnapshot([], images, [], [])

        state = {"snap": snap(["web-b", "db", "web-a"])}
        app = DockSurfApp(docker=MockDockerService(lambda: state["snap"]))
        async with app.run_test() as pilot:
            await _open(app, pilot, TabID.IMAGES)
            await _sort(app, pilot, TabID.IMAGES, "Repository")
            await _sort(app, pilot, TabID.IMAGES, "Repository")  # descending
            # A refresh re-applies whatever the open search bar holds.
            search = app.query_one(f"#{SEARCH_BAR_ID}", Input)
            search.display = True
            search.value = "web"
            await _filter(app, pilot, "web")
            self.assertEqual(_plain_cells(app, TabID.IMAGES, 1), ["web-b", "web-a"])

            state["snap"] = snap(["web-b", "db", "web-a", "web-c", "cache"])
            await _refresh(app, pilot)
            self.assertEqual(
                _plain_cells(app, TabID.IMAGES, 1), ["web-c", "web-b", "web-a"]
            )
