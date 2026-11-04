"""Unit tests for ViewState — layout thresholds and panel toggles."""

import unittest

from docksurf_py.core.render.palette import TIDE_PALETTE
from docksurf_py.core.state.view import LayoutMode, ViewState


class LayoutForTests(unittest.TestCase):
    def test_threshold_boundaries(self) -> None:
        v = ViewState()
        self.assertEqual(v.layout_for(120, 30), LayoutMode.WIDE)
        self.assertEqual(v.layout_for(119, 30), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(120, 29), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(80, 24), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(200, 60), LayoutMode.WIDE)


class HysteresisTests(unittest.TestCase):
    def test_wide_drops_to_compact_exactly_at_the_threshold(self) -> None:
        v = ViewState(layout=LayoutMode.WIDE)
        self.assertEqual(v.layout_for(120, 30), LayoutMode.WIDE)
        self.assertEqual(v.layout_for(119, 40), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(160, 29), LayoutMode.COMPACT)

    def test_compact_needs_extra_room_to_go_wide(self) -> None:
        v = ViewState(layout=LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(120, 30), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(121, 40), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(160, 31), LayoutMode.COMPACT)
        self.assertEqual(v.layout_for(122, 32), LayoutMode.WIDE)

    def test_dragging_across_the_edge_does_not_flap(self) -> None:
        v = ViewState()
        modes = []
        for w in (124, 121, 119, 120, 121, 122, 121, 120, 119):
            v.resize(w, 40)
            modes.append(v.layout)
        W, C = LayoutMode.WIDE, LayoutMode.COMPACT
        # widths:  124 121 119 120 121 122 121 120 119
        self.assertEqual(modes, [W, W, C, C, C, W, W, W, C])


class ToggleTests(unittest.TestCase):
    def test_defaults(self) -> None:
        v = ViewState()
        self.assertIs(v.palette, TIDE_PALETTE)
        self.assertEqual(v.layout, LayoutMode.WIDE)
        self.assertFalse(v.side_hidden or v.side_overlay or v.logs_full)

    def test_toggle_side_in_wide_hides_the_column(self) -> None:
        v = ViewState()
        self.assertTrue(v.toggle_side())
        self.assertTrue(v.side_hidden)
        self.assertFalse(v.side_overlay)
        self.assertFalse(v.toggle_side())

    def test_toggle_side_in_compact_shows_the_overlay(self) -> None:
        v = ViewState(layout=LayoutMode.COMPACT)
        self.assertTrue(v.toggle_side())
        self.assertTrue(v.side_overlay)
        self.assertFalse(v.side_hidden)

    def test_toggle_logs_full(self) -> None:
        v = ViewState()
        self.assertTrue(v.toggle_logs_full())
        self.assertFalse(v.toggle_logs_full())


class SideVisibleTests(unittest.TestCase):
    def test_wide_shows_the_side_column_unless_hidden(self) -> None:
        v = ViewState()
        self.assertTrue(v.side_visible)
        v.toggle_side()
        self.assertFalse(v.side_visible)

    def test_compact_shows_the_side_column_only_as_the_overlay(self) -> None:
        v = ViewState(layout=LayoutMode.COMPACT)
        self.assertFalse(v.side_visible)
        v.toggle_side()
        self.assertTrue(v.side_visible)
        v.toggle_side()
        self.assertFalse(v.side_visible)

    def test_live_panel_is_wide_only(self) -> None:
        wide = ViewState()
        self.assertTrue(wide.live_enabled)
        wide.toggle_side()
        self.assertFalse(wide.live_enabled)
        compact = ViewState(layout=LayoutMode.COMPACT)
        compact.toggle_side()  # overlay on, but it has no live panel
        self.assertTrue(compact.side_visible)
        self.assertFalse(compact.live_enabled)


class ResizeTests(unittest.TestCase):
    def test_no_change_reports_false_and_keeps_flags(self) -> None:
        v = ViewState()
        v.toggle_side()
        self.assertFalse(v.resize(160, 40))
        self.assertTrue(v.side_hidden)

    def test_mode_change_resets_side_flags(self) -> None:
        v = ViewState()
        v.toggle_side()
        self.assertTrue(v.resize(80, 24))
        self.assertEqual(v.layout, LayoutMode.COMPACT)
        self.assertFalse(v.side_hidden)
        v.toggle_side()
        self.assertTrue(v.resize(160, 40))
        self.assertFalse(v.side_overlay)
