"""palette_from_theme: every cycled theme maps to a complete, valid Palette."""

import dataclasses
import unittest

from rich.style import Style

from docksurf_py.core.render.palette import is_hex
from docksurf_py.tui.themes import (
    CUSTOM_THEMES,
    DEFAULT_THEME_NAME,
    DOCKSURF_TIDE,
    THEME_CYCLE_NAMES,
    palette_from_theme,
)


class PaletteFromThemeTests(unittest.TestCase):
    def test_tide_is_first_and_default(self) -> None:
        self.assertIs(CUSTOM_THEMES[0], DOCKSURF_TIDE)
        self.assertEqual(DEFAULT_THEME_NAME, "docksurf-tide")
        self.assertEqual(THEME_CYCLE_NAMES[0], "docksurf-tide")

    def test_every_custom_theme_gives_valid_hex_colours(self) -> None:
        for theme in CUSTOM_THEMES:
            p = palette_from_theme(theme)
            for f in dataclasses.fields(p):
                value = getattr(p, f.name)
                with self.subTest(theme=theme.name, role=f.name):
                    self.assertTrue(is_hex(value), value)
                    Style.parse(f"{value} on {value}")  # raises if Rich rejects it

    def test_roles_follow_the_theme(self) -> None:
        p = palette_from_theme(DOCKSURF_TIDE)
        self.assertEqual(p.primary, DOCKSURF_TIDE.primary.lower())
        self.assertEqual(p.success, DOCKSURF_TIDE.success.lower())
        self.assertEqual(p.muted, "#7a879d")  # the theme's text-muted variable

    def test_derived_muted_sits_between_foreground_and_background(self) -> None:
        p = palette_from_theme(CUSTOM_THEMES[1])  # forest: no text-muted variable
        self.assertNotIn(p.muted, (p.foreground, p.background))
