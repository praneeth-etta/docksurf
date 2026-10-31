"""Unit tests for the Volumes tab helpers (`core/render/volumes.py`)."""

import unittest

from rich.text import Text

from docksurf_py.core.models import DockerSnapshot, Volume
from docksurf_py.core.render.groups import group_name_cell
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from docksurf_py.core.render.volumes import (
    ANONYMOUS_LABEL,
    COMPOSE_PROJECT_LABEL,
    USED_BY_WIDTH,
    VOLUME_SIZE_WIDTH,
    OrphanSummary,
    is_anonymous,
    known_sizes,
    orphan_summary,
    orphan_total_text,
    safe_delete_body,
    size_cell,
    used_by_cell,
    volume_group_cells,
    volume_name_cell,
    volume_order_key,
    volume_project,
    volume_row_cells,
    volumes_footer,
    volumes_toolbar_line,
)
from docksurf_py.core.state.tables import (
    ResourceKind,
    RowGroup,
    TablesState,
    matches_volume,
    volume_group_specs,
)
from docksurf_py.core.types import TabID

HASH = "5c1339b0" + "a" * 42 + "d68cc6" + "e" * 8  # 64 hex characters


def _vol(name: str, labels: dict[str, str] | None = None) -> Volume:
    return Volume(
        name=name, driver="local", mountpoint="/m", used_by=[], labels=labels or {}
    )


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


class IsAnonymousTests(unittest.TestCase):
    def test_the_hash_fixture_is_64_hex_characters(self) -> None:
        self.assertEqual(len(HASH), 64)
        int(HASH, 16)

    def test_a_64_hex_name_is_anonymous(self) -> None:
        self.assertTrue(is_anonymous(_vol(HASH)))

    def test_the_anonymous_label_is_anonymous_whatever_its_value(self) -> None:
        self.assertTrue(is_anonymous(_vol("scratch", {ANONYMOUS_LABEL: ""})))
        self.assertTrue(is_anonymous(_vol("scratch", {ANONYMOUS_LABEL: "true"})))

    def test_a_named_volume_is_not(self) -> None:
        self.assertFalse(is_anonymous(_vol("pgdata", {"env": "dev"})))

    def test_near_misses_are_named(self) -> None:
        self.assertFalse(is_anonymous(_vol(HASH[:-1])))  # 63 hex
        self.assertFalse(is_anonymous(_vol(HASH + "a")))  # 65 hex
        self.assertFalse(is_anonymous(_vol("g" * 64)))  # 64, not hex
        self.assertFalse(is_anonymous(_vol(HASH.upper())))  # Docker hashes are lower


class VolumeProjectTests(unittest.TestCase):
    def test_reads_the_compose_project_label(self) -> None:
        v = _vol("db", {COMPOSE_PROJECT_LABEL: "shop"})
        self.assertEqual(volume_project(v), "shop")

    def test_dash_without_a_label_or_with_an_empty_one(self) -> None:
        self.assertEqual(volume_project(_vol("db")), "—")
        self.assertEqual(volume_project(_vol("db", {COMPOSE_PROJECT_LABEL: ""})), "—")


class VolumeNameCellTests(unittest.TestCase):
    def test_named_volume_is_bold_strong_text(self) -> None:
        cell = volume_name_cell(_vol("pgdata"), P, 30)
        self.assertEqual(_plain(cell), "pgdata")
        self.assertIn(f"bold {P.strong}", cell)

    def test_a_long_named_volume_is_middle_truncated_to_the_width(self) -> None:
        cell = volume_name_cell(_vol("a" * 20 + "b" * 20), P, 11)
        text = _plain(cell)
        self.assertEqual(len(text), 11)
        self.assertEqual((text[:5], text[5], text[6:]), ("aaaaa", "…", "bbbbb"))

    def test_anonymous_shows_glyph_head_ellipsis_and_tail(self) -> None:
        cell = volume_name_cell(_vol(HASH), P, 40)
        self.assertEqual(_plain(cell), f"◇ {HASH[:8]}…{HASH[-14:]}")

    def test_anonymous_colours_by_part(self) -> None:
        cell = volume_name_cell(_vol(HASH), P, 40)
        self.assertIn(f"[{P.secondary}]◇ [/]", cell)
        self.assertIn(f"[{P.muted}]{HASH[:8]}[/]", cell)
        self.assertIn(f"[{P.faint}]…[/]", cell)
        self.assertIn(f"[{P.muted}]{HASH[-14:]}[/]", cell)

    def test_anonymous_by_label_with_a_short_name_is_shown_whole(self) -> None:
        cell = volume_name_cell(_vol("tmpdata", {ANONYMOUS_LABEL: ""}), P, 40)
        self.assertEqual(_plain(cell), "◇ tmpdata")
        self.assertNotIn(P.faint, cell)

    def test_anonymous_fits_the_width_by_trimming_the_tail_then_the_head(self) -> None:
        for width in (25, 20, 11, 6, 3):
            text = _plain(volume_name_cell(_vol(HASH), P, width))
            self.assertLessEqual(len(text), width, width)
        self.assertEqual(
            _plain(volume_name_cell(_vol(HASH), P, 20)), f"◇ {HASH[:8]}…{HASH[-9:]}"
        )
        self.assertEqual(_plain(volume_name_cell(_vol(HASH), P, 11)), f"◇ {HASH[:8]}…")

    def test_markup_in_a_name_is_escaped(self) -> None:
        self.assertEqual(_plain(volume_name_cell(_vol("[red]x"), P, 30)), "[red]x")
        anon = _vol("[bold]", {ANONYMOUS_LABEL: ""})
        self.assertEqual(_plain(volume_name_cell(anon, P, 30)), "◇ [bold]")

    def test_zero_width_does_not_crash(self) -> None:
        for v in (_vol("pgdata"), _vol(HASH)):
            volume_name_cell(v, P, 0)


class VolumeOrderTests(unittest.TestCase):
    def _order(self, volumes: list[Volume], sizes: dict[str, int]) -> list[str]:
        return [
            v.name for v in sorted(volumes, key=lambda v: volume_order_key(v, sizes))
        ]

    def test_named_alphabetical_case_insensitive_then_anonymous(self) -> None:
        anon = "f" * 64
        order = self._order([_vol(anon), _vol("beta"), _vol("Alpha")], {})
        self.assertEqual(order, ["Alpha", "beta", anon])

    def test_anonymous_by_size_descending_unknown_last(self) -> None:
        a, b, c = "a" * 64, "b" * 64, "c" * 64
        order = self._order([_vol(a), _vol(b), _vol(c)], {a: 10, c: 500})
        self.assertEqual(order, [c, a, b])

    def test_a_known_zero_size_still_ranks_above_unknown(self) -> None:
        a, b = "a" * 64, "b" * 64
        self.assertEqual(self._order([_vol(a), _vol(b)], {b: 0}), [b, a])

    def test_sizes_never_reorder_named_volumes(self) -> None:
        order = self._order([_vol("b"), _vol("a")], {"a": 1, "b": 999})
        self.assertEqual(order, ["a", "b"])

    def test_equal_anonymous_sizes_fall_back_to_name(self) -> None:
        a, b = "a" * 64, "b" * 64
        self.assertEqual(self._order([_vol(b), _vol(a)], {a: 5, b: 5}), [a, b])


def _used(name: str, by: list[str]) -> Volume:
    v = _vol(name)
    v.used_by = by
    return v


class SizeCellTests(unittest.TestCase):
    def test_known_size_is_right_aligned_to_the_column(self) -> None:
        text = _plain(size_cell(2048, P))
        self.assertEqual(len(text), VOLUME_SIZE_WIDTH)
        self.assertEqual(text.strip(), "2.00KB")
        self.assertTrue(text.startswith(" "))

    def test_pending_size_is_a_muted_placeholder(self) -> None:
        cell = size_cell(None, P)
        self.assertEqual(_plain(cell).strip(), "· · ·")
        self.assertEqual(len(_plain(cell)), VOLUME_SIZE_WIDTH)
        self.assertIn(P.muted, cell)

    def test_zero_is_a_size_not_pending(self) -> None:
        self.assertNotIn("·", _plain(size_cell(0, P)))


class UsedByCellTests(unittest.TestCase):
    def test_unused_is_a_muted_dash(self) -> None:
        cell = used_by_cell(_vol("a"), P)
        self.assertEqual(_plain(cell), "—")
        self.assertIn(P.muted, cell)

    def test_one_container_shows_its_name(self) -> None:
        self.assertEqual(_plain(used_by_cell(_used("a", ["web"]), P)), "web")

    def test_more_containers_add_a_count(self) -> None:
        cell = used_by_cell(_used("a", ["web", "db", "cache"]), P)
        self.assertEqual(_plain(cell), "web +2")

    def test_a_long_name_is_cut_so_the_count_still_fits(self) -> None:
        cell = used_by_cell(_used("a", ["n" * 40, "x"]), P)
        text = _plain(cell)
        self.assertEqual(len(text), USED_BY_WIDTH)
        self.assertTrue(text.endswith("… +1"))


class VolumeRowCellsTests(unittest.TestCase):
    def test_five_cells_in_column_order(self) -> None:
        v = _used("db", ["web"])
        v.labels[COMPOSE_PROJECT_LABEL] = "shop"
        cells = volume_row_cells(v, 4096, P)
        self.assertEqual(
            [_plain(c).strip() for c in cells],
            ["db", "shop", "local", "4.00KB", "web"],
        )

    def test_no_project_is_muted_and_pending_size_shows_dots(self) -> None:
        cells = volume_row_cells(_vol("db"), None, P)
        self.assertEqual(_plain(cells[1]), "—")
        self.assertIn(P.muted, cells[1])
        self.assertEqual(_plain(cells[3]).strip(), "· · ·")


class GroupNameCellTests(unittest.TestCase):
    def _group(self, note: str = "", n: int = 2) -> RowGroup:
        items = tuple(_vol(f"v{i}") for i in range(n))
        return RowGroup(
            TabID.VOLUMES, "orphaned", "ORPHANED", note, "warning", items, "○"
        )

    def test_glyph_label_and_count_in_the_role_colour(self) -> None:
        cell = group_name_cell(self._group(), P, 40)
        self.assertEqual(_plain(cell), "○ ORPHANED · 2")
        self.assertIn(P.warning, cell)

    def test_without_a_glyph_there_is_no_leading_space(self) -> None:
        g = RowGroup(TabID.VOLUMES, "x", "OTHER", items=(_vol("a"),))
        self.assertEqual(_plain(group_name_cell(g, P, 40)), "OTHER · 1")

    def test_note_follows_when_all_of_it_fits(self) -> None:
        cell = group_name_cell(self._group("not mounted"), P, 40)
        self.assertEqual(_plain(cell), "○ ORPHANED · 2 · not mounted")

    def test_note_is_dropped_whole_rather_than_cut(self) -> None:
        cell = group_name_cell(self._group("not mounted by any container"), P, 30)
        self.assertEqual(_plain(cell), "○ ORPHANED · 2")

    def test_a_narrow_width_truncates_the_head(self) -> None:
        self.assertLessEqual(len(_plain(group_name_cell(self._group(), P, 8))), 8)


class VolumeGroupCellsTests(unittest.TestCase):
    def _orphans(self) -> RowGroup:
        return RowGroup(
            TabID.VOLUMES,
            "orphaned",
            "ORPHANED",
            "n",
            "warning",
            (_vol("a"), _vol("b"), _vol("c")),
            "○",
        )

    def test_orphaned_header_sums_known_sizes_in_warning_with_prune_chip(self) -> None:
        cells = volume_group_cells(self._orphans(), {"a": 1024, "b": 1024}, P)
        self.assertEqual(_plain(cells[3]).strip(), "2.00KB")
        self.assertIn(P.warning, cells[3])
        self.assertEqual(_plain(cells[4]).strip(), "P  prune orphans")  # chip + label
        self.assertEqual((cells[1], cells[2]), ("", ""))

    def test_no_known_size_shows_the_placeholder(self) -> None:
        cells = volume_group_cells(self._orphans(), {"elsewhere": 5}, P)
        self.assertEqual(_plain(cells[3]).strip(), "· · ·")

    def test_in_use_header_is_muted_and_has_no_action(self) -> None:
        g = RowGroup(
            TabID.VOLUMES, "in_use", "IN USE", "", "success", (_vol("a"),), "●"
        )
        cells = volume_group_cells(g, {"a": 1024}, P)
        self.assertIn(P.muted, cells[3])
        self.assertNotIn(P.warning, cells[3])
        self.assertEqual(cells[4], "")


class KnownSizesTests(unittest.TestCase):
    def test_counts_only_volumes_with_a_computed_size(self) -> None:
        vols = [_vol("a"), _vol("b"), _vol("c")]
        self.assertEqual(known_sizes(vols, {"a": 1, "c": 0, "gone": 9}), [1, 0])


class ToolbarLineTests(unittest.TestCase):
    def test_note_left_and_sizes_right_aligned(self) -> None:
        text = _plain(volumes_toolbar_line(14, 29, 80, P))
        self.assertEqual(len(text), 80)
        self.assertTrue(text.startswith("named first · anonymous after"))
        self.assertTrue(text.endswith("sizes 14/29  b size all"))

    def test_the_note_goes_first_when_it_does_not_fit(self) -> None:
        text = _plain(volumes_toolbar_line(0, 5, 30, P))
        self.assertEqual(text.strip(), "sizes 0/5  b size all")
        self.assertEqual(len(text), 30)

    def test_a_tiny_width_does_not_crash(self) -> None:
        volumes_toolbar_line(1, 2, 3, P)


class FooterTests(unittest.TestCase):
    def test_legend_explains_the_cell_styles(self) -> None:
        text = _plain(volumes_footer(P))
        self.assertIn("◇ anonymous volume (hash name)", text)
        self.assertIn("bold named volume", text)
        self.assertIn("· · · size not computed yet", text)


def _state() -> TablesState:
    sizes: dict[str, int] = {}
    state = TablesState(
        {
            TabID.VOLUMES: ResourceKind(
                label="volume",
                snapshot_items=lambda snap: snap.volumes,
                matches=matches_volume,
                groups=volume_group_specs(lambda: sizes),
            )
        }
    )
    state.sizes = sizes  # type: ignore[attr-defined]
    return state


class VolumeGroupSpecTests(unittest.TestCase):
    def _rows(self, state: TablesState, *volumes: Volume) -> list:
        return state.build_rows(
            TabID.VOLUMES, DockerSnapshot([], [], list(volumes), [])
        )

    def test_in_use_comes_before_orphaned_with_glyphs_and_roles(self) -> None:
        rows = self._rows(_state(), _vol("loose"), _used("db", ["web"]))
        headers = [r for r in rows if isinstance(r, RowGroup)]
        self.assertEqual(
            [(h.key, h.glyph, h.role) for h in headers],
            [("in_use", "●", "success"), ("orphaned", "○", "warning")],
        )
        self.assertEqual(headers[1].note, "not mounted by any container")

    def test_orphans_list_named_before_anonymous_by_size(self) -> None:
        state = _state()
        a, b = "a" * 64, "b" * 64
        state.sizes.update({a: 10, b: 900})  # type: ignore[attr-defined]
        rows = self._rows(state, _vol(a), _vol("zeta"), _vol(b), _vol("alpha"))
        self.assertEqual(
            [r.name for r in rows if isinstance(r, Volume)], ["alpha", "zeta", b, a]
        )

    def test_sizes_are_read_when_rows_are_built(self) -> None:
        state = _state()
        a, b = "a" * 64, "b" * 64
        before = self._rows(state, _vol(a), _vol(b))
        state.sizes[b] = 5  # type: ignore[attr-defined]
        after = self._rows(state, _vol(a), _vol(b))
        self.assertEqual([r.name for r in before[1:]], [a, b])
        self.assertEqual([r.name for r in after[1:]], [b, a])


class OrphanSummaryTests(unittest.TestCase):
    def test_counts_orphans_and_sums_the_known_sizes(self) -> None:
        vols = [_vol("a"), _vol("b"), _used("busy", ["web"])]
        got = orphan_summary(vols, {"a": 100, "b": 50, "busy": 999})
        self.assertEqual(tuple(got), (2, 150, True))

    def test_pending_sizes_make_the_total_partial(self) -> None:
        got = orphan_summary([_vol("a"), _vol("b")], {"a": 100})
        self.assertEqual(tuple(got), (2, 100, False))

    def test_no_orphans(self) -> None:
        self.assertEqual(tuple(orphan_summary([_used("a", ["w"])], {})), (0, 0, True))


class SafeDeleteBodyTests(unittest.TestCase):
    def _body(self, size, summary) -> str:
        return _plain(safe_delete_body(_vol("a"), size, summary, P))

    def test_full_text_with_known_sizes(self) -> None:
        body = self._body(2048, OrphanSummary(21, 4096, True))
        self.assertEqual(
            body.splitlines(),
            [
                "No container mounts this volume, running or stopped.",
                "Deleting removes its 2.00KB of data for good.",
                "d delete this   P prune all 21 orphans · 4.00KB",
            ],
        )

    def test_unknown_size_says_just_its_data(self) -> None:
        body = self._body(None, OrphanSummary(3, 0, False))
        self.assertIn("Deleting removes its data for good.", body)
        self.assertTrue(body.endswith("P prune all 3 orphans"))

    def test_a_partial_total_is_marked_as_a_lower_bound(self) -> None:
        body = self._body(None, OrphanSummary(3, 4096, False))
        self.assertTrue(body.endswith("P prune all 3 orphans · ≥ 4.00KB"))

    def test_markup_free_text_survives_the_markup_round_trip(self) -> None:
        body = self._body(1, OrphanSummary(1, 1, True))
        self.assertNotIn("[", body)


class OrphanTotalTextTests(unittest.TestCase):
    def test_complete_partial_and_unknown(self) -> None:
        self.assertEqual(orphan_total_text(OrphanSummary(2, 4096, True)), "4.00KB")
        self.assertEqual(orphan_total_text(OrphanSummary(2, 4096, False)), "≥ 4.00KB")
        self.assertEqual(orphan_total_text(OrphanSummary(2, 0, False)), "")

    def test_no_orphans_is_a_complete_zero(self) -> None:
        self.assertEqual(orphan_total_text(OrphanSummary(0, 0, True)), "0B")
