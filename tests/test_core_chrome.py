"""Unit tests for core/render/chrome — top bar, key hints, bottom bar, titles."""

import unittest

from rich.text import Text

from docksurf_py.core.models import DockerSnapshot, Volume
from docksurf_py.core.render.chrome import (
    GLOBAL_HINTS,
    GlobalCounts,
    HintContext,
    TabSummary,
    bottom_bar,
    global_counts,
    key_hints,
    list_title,
    tab_summaries,
    top_bar,
)
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from docksurf_py.core.state.status import partition_counts
from docksurf_py.core.types import TabID
from tests.test_compose import make_container


def plain(markup: str) -> str:
    """Rendered text with each chip's padding collapsed (` s  stop` -> `s stop`)."""
    return " ".join(Text.from_markup(markup).plain.split())


def raw(markup: str) -> str:
    """Rendered text exactly as laid out (for width checks)."""
    return Text.from_markup(markup).plain


def _tabs(counts=(15, 58, 21, 6)) -> list[TabSummary]:
    labels = ["Containers", "Images", "Volumes", "Networks"]
    return [
        TabSummary(tab, i, label, counts[i - 1] if counts else None)
        for i, (tab, label) in enumerate(zip(TabID, labels), start=1)
    ]


def _failed(name: str):
    c = make_container(name, running=False)
    c.exit_code = 1
    return c


class PartitionTests(unittest.TestCase):
    def test_partition_sums_to_total(self) -> None:
        unhealthy = make_container("u", health="unhealthy")
        cs = [
            make_container("a"),
            make_container("b", running=False),
            _failed("f"),
            unhealthy,
        ]
        up, down, failed = partition_counts(cs)
        self.assertEqual((up, down, failed), (1, 1, 2))
        self.assertEqual(up + down + failed, len(cs))


class TabSummaryTests(unittest.TestCase):
    def test_counts_none_before_first_snapshot(self) -> None:
        self.assertTrue(all(t.count is None for t in tab_summaries(None)))

    def test_counts_from_snapshot(self) -> None:
        snap = DockerSnapshot([make_container("a")], [], [], [])
        counts = [t.count for t in tab_summaries(snap)]
        self.assertEqual(counts, [1, 0, 0, 0])


class TopBarTests(unittest.TestCase):
    def test_active_tab_is_a_primary_pill_others_are_muted(self) -> None:
        out = top_bar(_tabs(), TabID.IMAGES, "default", "live", "tide", P)
        self.assertIn(f"on {P.primary}] 2 Images 58 [/]", out)
        self.assertIn(f"[{P.muted}] 1 Containers 15 [/]", out)
        self.assertNotIn(f"on {P.primary}] 1 Containers", out)

    def test_tabs_are_clickable(self) -> None:
        out = top_bar(_tabs(), TabID.CONTAINERS, "default", "live", "tide", P)
        for n in (1, 2, 3, 4):
            self.assertIn(f"[@click=app.switch_tab_{n}]", out)

    def test_counts_show_ellipsis_before_first_snapshot(self) -> None:
        out = plain(
            top_bar(_tabs(None), TabID.CONTAINERS, "default", "live", "tide", P)
        )
        self.assertIn("1 Containers …", out)
        self.assertIn("2 Images …", out)

    def test_connection_states(self) -> None:
        for conn, text, role in (
            ("live", "● live", P.success),
            ("offline", "○ offline", P.error),
            ("connecting", "◐ connecting", P.warning),
        ):
            with self.subTest(conn=conn):
                out = top_bar(_tabs(), TabID.CONTAINERS, "default", conn, "tide", P)  # type: ignore[arg-type]
                self.assertIn(f"[{role}]{text}[/]", out)

    def test_context_and_theme_are_shown(self) -> None:
        out = plain(
            top_bar(_tabs(), TabID.CONTAINERS, "desktop-linux", "live", "tide", P)
        )
        self.assertIn("desktop-linux", out)
        self.assertIn("M tide", out)
        self.assertIn("◆ docksurf", out)

    def test_compact_keeps_label_only_on_active_tab(self) -> None:
        out = plain(
            top_bar(_tabs(), TabID.VOLUMES, "default", "live", "tide", P, compact=True)
        )
        self.assertIn("3 Volumes 21", out)
        self.assertNotIn("Containers", out)
        self.assertNotIn("Images", out)
        self.assertIn(" 1 ", out)

    def test_width_right_aligns_and_fills_exactly(self) -> None:
        out = raw(
            top_bar(_tabs(), TabID.CONTAINERS, "default", "live", "tide", P, width=120)
        )
        self.assertEqual(len(out), 120)
        self.assertTrue(out.rstrip().endswith("tide"))

    def test_narrow_width_drops_right_side_pieces(self) -> None:
        def bar(width: int) -> str:
            return top_bar(
                _tabs(), TabID.CONTAINERS, "default", "live", "tide", P, width=width
            )

        self.assertIn("M tide", plain(bar(120)))
        mid = plain(bar(108))
        self.assertNotIn("M tide", mid)
        self.assertIn("context default", mid)
        low = plain(bar(90))
        self.assertNotIn("default", low)
        self.assertIn("● live", low)
        tight = plain(bar(60))  # full labels no longer fit: inactive tabs -> numbers
        self.assertNotIn("Images", tight)
        self.assertIn("● live", tight)
        self.assertNotIn("live", plain(bar(30)))  # nothing fits: tabs only
        for width in (120, 108, 90):
            self.assertLessEqual(len(raw(bar(width))), width)

    def test_context_is_escaped(self) -> None:
        out = top_bar(_tabs(), TabID.CONTAINERS, "[bold]x", "live", "tide", P)
        self.assertIn("\\[bold]x", out)


class KeyHintsTests(unittest.TestCase):
    def keys(self, **kw) -> list[str]:
        return [k for k, _ in key_hints(HintContext(**kw))]

    def test_running_container(self) -> None:
        keys = self.keys(tab=TabID.CONTAINERS, row="container_running")
        self.assertEqual(keys[:4], ["s", "x", "l", "e"])
        self.assertNotIn("B", keys)

    def test_rebuild_only_for_compose_services(self) -> None:
        keys = self.keys(tab=TabID.CONTAINERS, row="container_running", compose=True)
        self.assertIn("B", keys)

    def test_stopped_container_offers_start_not_stop(self) -> None:
        keys = self.keys(tab=TabID.CONTAINERS, row="container_stopped")
        self.assertIn("S", keys)
        self.assertNotIn("s", keys)

    def test_project_header(self) -> None:
        keys = self.keys(tab=TabID.CONTAINERS, row="project")
        self.assertIn("^u", keys)
        self.assertIn("^k", keys)

    def test_per_tab_hints(self) -> None:
        self.assertIn("h", self.keys(tab=TabID.IMAGES, row="image"))
        self.assertIn("b", self.keys(tab=TabID.VOLUMES, row="volume"))
        self.assertIn("v", self.keys(tab=TabID.NETWORKS, row="network"))

    def test_empty_tab_still_offers_create(self) -> None:
        self.assertEqual(self.keys(tab=TabID.IMAGES, row="none"), ["+"])

    def test_marks_prepend_clear_hint(self) -> None:
        keys = self.keys(tab=TabID.IMAGES, row="image", has_marks=True)
        self.assertEqual(keys[0], "esc")

    def test_filtering_replaces_row_hints(self) -> None:
        keys = self.keys(tab=TabID.CONTAINERS, row="container_running", filtering=True)
        self.assertEqual(keys, ["enter", "esc"])

    def test_logs_full_replaces_row_hints(self) -> None:
        keys = self.keys(tab=TabID.CONTAINERS, row="container_running", logs_full=True)
        self.assertIn("f", keys)
        self.assertNotIn("s", keys)


class GlobalCountsTests(unittest.TestCase):
    def test_none_before_snapshot(self) -> None:
        self.assertIsNone(global_counts(None))

    def test_counts(self) -> None:
        vols = [
            Volume("a", "local", "/m", used_by=["x"], labels={}),
            Volume("b", "local", "/m", used_by=[], labels={}),
        ]
        snap = DockerSnapshot(
            [make_container("a"), make_container("b", running=False), _failed("c")],
            [],
            vols,
            [],
        )
        self.assertEqual(global_counts(snap), GlobalCounts(1, 1, 1, 0, 1))


class BottomBarTests(unittest.TestCase):
    COUNTS = GlobalCounts(2, 12, 1, 58, 21)

    def test_hints_counts_and_separator(self) -> None:
        out = plain(bottom_bar([("s", "stop")], self.COUNTS, P))
        self.assertIn("s stop", out)
        self.assertIn("/ filter", out)
        self.assertIn("^p palette", out)
        self.assertIn("● 2 up", out)
        self.assertIn("○ 12 down", out)
        self.assertIn("✕ 1 failed", out)
        self.assertIn("58 images · 21 orphaned volumes", out)

    def test_orphaned_uses_warning_only_when_positive(self) -> None:
        warn = bottom_bar([], self.COUNTS, P)
        self.assertIn(f"[{P.warning}]21 orphaned volumes[/]", warn)
        calm = bottom_bar([], GlobalCounts(1, 0, 0, 3, 0), P)
        self.assertNotIn(f"[{P.warning}]", calm)

    def test_message_replaces_counts(self) -> None:
        out = plain(
            bottom_bar([], self.COUNTS, P, message="Docker daemon is not running")
        )
        self.assertIn("Docker daemon is not running", out)
        self.assertNotIn("orphaned", out)

    def test_no_counts_before_snapshot(self) -> None:
        out = plain(bottom_bar([("s", "stop")], None, P))
        self.assertNotIn("up", out.replace("palette", ""))

    def test_fits_width_exactly_when_it_can(self) -> None:
        out = raw(
            bottom_bar([("s", "stop"), ("x", "restart")], self.COUNTS, P, width=150)
        )
        self.assertEqual(len(out), 150)

    def test_drop_order_palette_then_context_then_filter_and_help(self) -> None:
        hints = [("s", "stop"), ("x", "restart"), ("l", "logs"), ("e", "shell")]
        right = len(raw(bottom_bar([], self.COUNTS, P, width=0, global_hints=()))) - 2

        def bar(extra: int) -> str:
            return plain(bottom_bar(hints, self.COUNTS, P, width=right + extra))

        self.assertIn("^p palette", bar(200))
        no_palette = bar(75)
        self.assertNotIn("^p palette", no_palette)
        self.assertIn("e shell", no_palette)
        self.assertIn("? all keys", no_palette)
        fewer = bar(55)
        self.assertNotIn("e shell", fewer)
        self.assertIn("/ filter", fewer)
        self.assertIn("? all keys", fewer)
        tiny = bar(20)
        self.assertNotIn("s stop", tiny)
        self.assertIn("? all keys", tiny)
        for out in (no_palette, fewer, tiny):
            self.assertIn("orphaned volumes", out)  # counts always kept

    def test_global_hints_constant_is_stable(self) -> None:
        self.assertEqual([k for k, _ in GLOBAL_HINTS], ["/", "?", "^p"])


class ListTitleTests(unittest.TestCase):
    def test_grouped_containers(self) -> None:
        self.assertEqual(
            list_title(TabID.CONTAINERS, 15, None, grouped=True),
            "containers · 15 · grouped by project",
        )

    def test_sorted_images(self) -> None:
        self.assertEqual(
            list_title(TabID.IMAGES, 58, ("Size", True), grouped=False),
            "images · 58 · sorted by size ▾",
        )
        self.assertIn("▴", list_title(TabID.IMAGES, 58, ("Size", False), grouped=False))

    def test_filtered_count(self) -> None:
        self.assertEqual(
            list_title(TabID.VOLUMES, 3, None, grouped=False, total=21),
            "volumes · 3 of 21",
        )
        self.assertEqual(
            list_title(TabID.VOLUMES, 3, None, grouped=False, total=3), "volumes · 3"
        )


class ChipBarTests(unittest.TestCase):
    COUNTS = {"all": 7, "running": 5, "stopped": 1, "failed": 1}

    def _plain(self, active: str, counts=None) -> str:
        from docksurf_py.core.render.chrome import chip_bar
        from docksurf_py.core.state.tables import CONTAINER_CHIPS

        return Text.from_markup(
            chip_bar(CONTAINER_CHIPS, counts or self.COUNTS, active, P)
        ).plain

    def test_shows_every_chip_with_counts(self) -> None:
        self.assertEqual(
            " ".join(self._plain("all").split()),
            "all 7 running 5 stopped 1 failed 1",
        )

    def test_active_chip_is_a_primary_pill(self) -> None:
        from docksurf_py.core.render.chrome import chip_bar
        from docksurf_py.core.state.tables import CONTAINER_CHIPS

        out = chip_bar(CONTAINER_CHIPS, self.COUNTS, "running", P)
        self.assertIn(f"on {P.primary}] running 5 [/]", out)

    def test_failed_chip_is_tinted_only_when_nonzero(self) -> None:
        from docksurf_py.core.render.chrome import chip_bar
        from docksurf_py.core.state.tables import CONTAINER_CHIPS

        hot = chip_bar(CONTAINER_CHIPS, self.COUNTS, "all", P)
        cold = chip_bar(CONTAINER_CHIPS, {**self.COUNTS, "failed": 0}, "all", P)
        self.assertIn(f"[{P.error} on #", hot)
        self.assertNotIn(f"[{P.error} on #", cold)

    def test_chips_are_clickable(self) -> None:
        from docksurf_py.core.render.chrome import chip_bar
        from docksurf_py.core.state.tables import CONTAINER_CHIPS

        out = chip_bar(CONTAINER_CHIPS, self.COUNTS, "all", P)
        for key in ("all", "running", "stopped", "failed"):
            self.assertIn(f"[@click=app.set_chip('{key}')]", out)

    def test_no_chips_is_empty(self) -> None:
        from docksurf_py.core.render.chrome import chip_bar

        self.assertEqual(chip_bar((), {}, "all", P), "")
