"""Unit tests for core/render/palette — blend maths, pills, chips, palettes."""

import dataclasses
import unittest

from docksurf_py.core.render.markup import rollup_dots, state_cell, state_glyph
from docksurf_py.core.render.palette import (
    TIDE_PALETTE,
    blend,
    contrast_ratio,
    fg,
    is_hex,
    key_chip,
    pill,
)
from docksurf_py.core.types import SafeMarkup
from tests.test_compose import make_container


class BlendTests(unittest.TestCase):
    def test_endpoints(self) -> None:
        self.assertEqual(blend("#ff0000", "#000000", 1.0), "#ff0000")
        self.assertEqual(blend("#ff0000", "#0000ff", 0.0), "#0000ff")

    def test_midpoint(self) -> None:
        self.assertEqual(blend("#ffffff", "#000000", 0.5), "#808080")

    def test_alpha_is_clamped(self) -> None:
        self.assertEqual(blend("#ffffff", "#000000", 5), "#ffffff")
        self.assertEqual(blend("#ffffff", "#000000", -1), "#000000")

    def test_non_hex_returns_fg_unchanged(self) -> None:
        self.assertEqual(blend("blue", "#000000", 0.2), "blue")
        self.assertEqual(blend("#ffffff", "default", 0.2), "#ffffff")


class HelperTests(unittest.TestCase):
    def test_fg_escapes_text_and_uses_role_colour(self) -> None:
        out = fg(TIDE_PALETTE, "success", "[bold]x")
        self.assertIsInstance(out, SafeMarkup)
        self.assertEqual(out, f"[{TIDE_PALETTE.success}]\\[bold]x[/]")

    def test_pill_and_chip_escape_their_label(self) -> None:
        self.assertIn("\\[x]", pill(TIDE_PALETTE, "[x]", "error"))
        self.assertIn("\\[x]", key_chip(TIDE_PALETTE, "[x]"))

    def test_pill_is_background_text_on_role(self) -> None:
        p = TIDE_PALETTE
        self.assertEqual(
            pill(p, "RUNNING", "success"),
            f"[{p.background} on {p.success}] RUNNING [/]",
        )

    def test_key_chip_sits_on_panel(self) -> None:
        self.assertIn(f"on {TIDE_PALETTE.panel}", key_chip(TIDE_PALETTE, "s"))

    def test_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            fg(TIDE_PALETTE, "nope", "x")
        with self.assertRaises(KeyError):
            pill(TIDE_PALETTE, "x", "nope")


class PaletteTests(unittest.TestCase):
    def test_tide_is_all_hex(self) -> None:
        for f in dataclasses.fields(TIDE_PALETTE):
            self.assertTrue(is_hex(getattr(TIDE_PALETTE, f.name)), f.name)

    def test_tide_row_backgrounds_are_tints_of_the_ground(self) -> None:
        self.assertNotEqual(TIDE_PALETTE.cursor_bg, TIDE_PALETTE.background)
        self.assertNotEqual(TIDE_PALETTE.marked_bg, TIDE_PALETTE.background)


class RenderersUsePaletteTests(unittest.TestCase):
    def test_state_markup_carries_palette_colours_not_ansi_names(self) -> None:
        p = TIDE_PALETTE
        c = make_container("a")
        for out in (state_glyph(c, p), state_cell(c, p), rollup_dots([c], p)):
            self.assertIn(p.success, out)
            self.assertNotIn("[green]", out)


class ContrastRatioTests(unittest.TestCase):
    def test_black_on_white_is_21(self) -> None:
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0)

    def test_identical_colours_are_1_and_order_does_not_matter(self) -> None:
        self.assertAlmostEqual(contrast_ratio("#336699", "#336699"), 1.0)
        a, b = "#7aa2f7", "#0b0e14"
        self.assertEqual(contrast_ratio(a, b), contrast_ratio(b, a))

    def test_known_midpoint(self) -> None:
        # #767676 on white is the textbook 4.54:1 (just passes AA).
        self.assertAlmostEqual(contrast_ratio("#767676", "#ffffff"), 4.54, places=2)

    def test_non_hex_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            contrast_ratio("red", "#ffffff")
