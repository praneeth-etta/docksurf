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
