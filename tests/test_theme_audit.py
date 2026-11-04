"""Theme audit: every curated theme keeps its text readable, its focus border
distinguishable from an unfocused one, and its row tints visible. Thresholds are
WCAG ones (4.5:1 text, 3:1 non-text) unless a comment says otherwise."""

import dataclasses
import unittest

from textual.widgets import DataTable

from docksurf_py.core.models import DockerSnapshot
from docksurf_py.core.render.palette import TIDE_PALETTE, Palette, contrast_ratio
from docksurf_py.core.types import TabID
from docksurf_py.tui.app import DockSurfApp
from docksurf_py.tui.ids import DETAIL_PANE_ID, LIST_PANEL_ID, TableID
from docksurf_py.tui.themes import (
    CUSTOM_THEMES,
    DOCKSURF_TIDE,
    THEME_CYCLE_NAMES,
    UNSUPPORTED_BUILTIN_THEMES,
    palette_from_theme,
)
from tests.test_app import MockDockerService, wait_until
from tests.test_compose import make_container

TEXT_ROLES = ("foreground", "strong", "muted")
STATUS_ROLES = ("success", "warning", "error")


def _palettes() -> list[tuple[str, Palette]]:
    return [(t.name, palette_from_theme(t)) for t in CUSTOM_THEMES]


class PaletteAuditTests(unittest.TestCase):
    def test_tide_palette_matches_the_tide_theme(self) -> None:
        """`TIDE_PALETTE` is the design's Tide (and the pre-mount default); the
        running app uses `palette_from_theme(DOCKSURF_TIDE)`. Known drift, kept
        because the golden table file pins the derived values: `strong`
        (#E8EEF8 designed, derived by lightening the foreground) and `faint`
        (#3A4660 designed, a plain fg/bg blend). Every other role must agree."""
        derived = palette_from_theme(DOCKSURF_TIDE)
        for f in dataclasses.fields(Palette):
            if f.name in ("strong", "faint"):
                continue
            with self.subTest(role=f.name):
                self.assertEqual(
                    getattr(TIDE_PALETTE, f.name).lower(), getattr(derived, f.name)
                )

    def test_text_is_readable_on_the_ground_and_on_surface(self) -> None:
        for name, p in _palettes():
            for ground in ("background", "surface"):
                for role in TEXT_ROLES + STATUS_ROLES:
                    with self.subTest(theme=name, role=role, ground=ground):
                        ratio = contrast_ratio(getattr(p, role), getattr(p, ground))
                        self.assertGreaterEqual(ratio, 4.5, f"{ratio:.2f}")

    def test_accents_are_visible_on_the_ground(self) -> None:
        for name, p in _palettes():
            for role in ("primary", "accent", "secondary"):
                with self.subTest(theme=name, role=role):
                    ratio = contrast_ratio(getattr(p, role), p.background)
                    self.assertGreaterEqual(ratio, 3.0, f"{ratio:.2f}")

    def test_text_stays_readable_on_the_cursor_and_marked_rows(self) -> None:
        # Floor of 3.5, not 4.5: tinting a row costs contrast, and muted text
        # and error red on the cursor row are the tightest (about 3.9-4.1).
        for name, p in _palettes():
            for tint in ("cursor_bg", "marked_bg"):
                for role in TEXT_ROLES + STATUS_ROLES:
                    with self.subTest(theme=name, role=role, row=tint):
                        ratio = contrast_ratio(getattr(p, role), getattr(p, tint))
                        self.assertGreaterEqual(ratio, 3.5, f"{ratio:.2f}")

    def test_row_tints_are_visible_against_the_ground(self) -> None:
        for name, p in _palettes():
            for tint, floor in (("cursor_bg", 1.2), ("marked_bg", 1.08)):
                with self.subTest(theme=name, tint=tint):
                    ratio = contrast_ratio(getattr(p, tint), p.background)
                    self.assertGreaterEqual(ratio, floor, f"{ratio:.2f}")

    def test_group_rows_are_visible_but_quiet(self) -> None:
        for name, p in _palettes():
            with self.subTest(theme=name):
                ratio = contrast_ratio(p.surface, p.background)
                self.assertGreaterEqual(ratio, 1.06, f"{ratio:.2f}")
                self.assertLess(ratio, 1.3, f"{ratio:.2f}")

    def test_marked_rows_differ_from_the_cursor_row(self) -> None:
        for name, p in _palettes():
            with self.subTest(theme=name):
                self.assertNotEqual(p.cursor_bg, p.marked_bg)


class ThemeVariableAuditTests(unittest.TestCase):
    def test_border_is_dimmer_than_the_focus_colour(self) -> None:
        """`.panel` uses `$border`, `.panel:focus-within` uses `$primary`. A theme
        that leaves `border` undefined gets the primary colour for both."""
        for theme in CUSTOM_THEMES:
            v = theme.to_color_system().generate()
            ground = v["background"]
            with self.subTest(theme=theme.name):
                self.assertNotEqual(v["border"], v["primary"])
                self.assertGreaterEqual(contrast_ratio(v["primary"], v["border"]), 3.0)
                self.assertGreaterEqual(contrast_ratio(v["border"], ground), 1.4)
                self.assertEqual(v["border"], v["border-blurred"])


class SupportedThemesTests(unittest.IsolatedAsyncioTestCase):
    async def test_ansi_builtins_are_not_offered(self) -> None:
        app = DockSurfApp(
            docker=MockDockerService(lambda: DockerSnapshot([], [], [], []))
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            for name in UNSUPPORTED_BUILTIN_THEMES:
                self.assertNotIn(name, app.available_themes)
            for name in THEME_CYCLE_NAMES:
                self.assertIn(name, app.available_themes)

    async def test_a_focused_panel_has_a_different_border_on_every_theme(self) -> None:
        snapshot = DockerSnapshot([make_container("web")], [], [], [])
        app = DockSurfApp(docker=MockDockerService(lambda: snapshot))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await wait_until(lambda: bool(app._tables.rows.get(TabID.CONTAINERS)))
            app.query_one(f"#{TableID.CONTAINERS}", DataTable).focus()
            for name in THEME_CYCLE_NAMES:
                app.theme = name
                await pilot.pause()
                focused = app.query_one(f"#{LIST_PANEL_ID}").styles.border.top
                plain = app.query_one(f"#{DETAIL_PANE_ID}").styles.border.top
                with self.subTest(theme=name):
                    self.assertNotEqual(focused[1], plain[1])


if __name__ == "__main__":
    unittest.main()
