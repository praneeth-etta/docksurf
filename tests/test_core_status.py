"""Unit tests for container state classification and the state render helpers."""

import unittest

from docksurf_py.core.render.markup import rollup_dots, state_cell, state_glyph
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from docksurf_py.core.state.status import (
    CLEAN_EXIT_CODES,
    GLYPH,
    ROLE,
    RunState,
    classify,
    is_failed,
    project_state_counts,
)
from tests.test_compose import make_container


def _c(
    state: str,
    *,
    running: bool | None = None,
    exit_code: int = 0,
    health: str = "",
    name: str = "a",
    service: str = "",
):
    c = make_container(name, project="p" if service else "", service=service)
    c.state = state
    c.status = state
    c.running = (
        state in ("running", "restarting", "paused") if running is None else running
    )
    c.exit_code = exit_code
    c.health = health
    return c


class ClassifyTests(unittest.TestCase):
    def test_table(self) -> None:
        cases = [
            (_c("running"), RunState.RUNNING),
            (_c("running", health="healthy"), RunState.RUNNING),
            (_c("running", health="starting"), RunState.STARTING),
            (_c("running", health="unhealthy"), RunState.UNHEALTHY),
            (_c("restarting"), RunState.STARTING),
            (_c("paused"), RunState.PAUSED),
            (_c("paused", health="unhealthy"), RunState.PAUSED),
            (_c("created"), RunState.STOPPED),
            (_c("dead"), RunState.FAILED),
            (_c("exited", exit_code=0), RunState.STOPPED),
            (_c("exited", exit_code=130), RunState.STOPPED),
            (_c("exited", exit_code=143), RunState.STOPPED),
            (_c("exited", exit_code=1), RunState.FAILED),
            (_c("exited", exit_code=137), RunState.FAILED),
            (_c("exited", exit_code=255), RunState.FAILED),
        ]
        for container, expected in cases:
            with self.subTest(state=container.state, code=container.exit_code):
                self.assertEqual(classify(container), expected)

    def test_clean_exit_codes(self) -> None:
        self.assertEqual(CLEAN_EXIT_CODES, {0, 130, 143})

    def test_is_failed(self) -> None:
        self.assertTrue(is_failed(RunState.FAILED))
        self.assertTrue(is_failed(RunState.UNHEALTHY))
        self.assertFalse(is_failed(RunState.STOPPED))
        self.assertFalse(is_failed(RunState.RUNNING))

    def test_every_state_has_glyph_and_role(self) -> None:
        for state in RunState:
            self.assertIn(state, GLYPH)
            self.assertIn(state, ROLE)

    def test_glyphs_are_distinct_where_colour_cannot_carry_it(self) -> None:
        # 16-colour rule: running / stopped / failed must differ by shape.
        shapes = {
            GLYPH[RunState.RUNNING],
            GLYPH[RunState.STOPPED],
            GLYPH[RunState.FAILED],
        }
        self.assertEqual(len(shapes), 3)


class CellTests(unittest.TestCase):
    def test_state_cell_text(self) -> None:
        cases = [
            (_c("running", health="healthy"), "running ✓"),
            (_c("running"), "running"),
            (_c("running", health="starting"), "starting …"),
            (_c("restarting"), "restarting"),
            (_c("paused"), "paused"),
            (_c("exited", exit_code=137), "exited 137"),
            (_c("exited", exit_code=0), "exited 0"),
            (_c("created"), "created"),
        ]
        for container, text in cases:
            with self.subTest(text=text):
                self.assertIn(text, state_cell(container, P))

    def test_state_glyph_role_colours(self) -> None:
        self.assertEqual(state_glyph(_c("running"), P), f"[{P.success}]●[/]")
        self.assertEqual(state_glyph(_c("exited"), P), f"[{P.muted}]○[/]")
        self.assertEqual(state_glyph(_c("exited", exit_code=1), P), f"[{P.error}]✕[/]")


class RollupTests(unittest.TestCase):
    def _members(self, n: int, down: int = 0, failed: int = 0):
        out = []
        for i in range(n):
            svc = f"s{i:02d}"
            if i < failed:
                out.append(_c("exited", exit_code=1, name=svc, service=svc))
            elif i < failed + down:
                out.append(_c("exited", name=svc, service=svc))
            else:
                out.append(_c("running", name=svc, service=svc))
        return out

    def test_counts(self) -> None:
        members = self._members(5, down=1, failed=1)
        self.assertEqual(project_state_counts(members), (3, 2, 1))

    def test_unhealthy_is_up_and_failed(self) -> None:
        self.assertEqual(
            project_state_counts([_c("running", health="unhealthy")]), (1, 0, 1)
        )

    def test_all_up_is_success(self) -> None:
        out = rollup_dots(self._members(3), P)
        self.assertIn(f"[{P.success}]3/3 up[/]", out)
        self.assertNotIn("failed", out)

    def test_some_up_is_warning(self) -> None:
        out = rollup_dots(self._members(3, down=1), P)
        self.assertIn(f"[{P.warning}]2/3 up[/]", out)

    def test_none_up_is_muted(self) -> None:
        out = rollup_dots(self._members(2, down=2), P)
        self.assertIn(f"[{P.muted}]0/2 up[/]", out)

    def test_failed_appends_error_segment(self) -> None:
        out = rollup_dots(self._members(3, failed=1), P)
        self.assertIn(f"[{P.error}]1 failed[/]", out)

    def test_cap_and_overflow(self) -> None:
        out = rollup_dots(self._members(13), P, max_dots=10)
        self.assertEqual(out.count("●"), 10)
        self.assertIn("+3", out)
        self.assertIn("13/13 up", out)

    def test_dots_follow_service_name_order(self) -> None:
        a = _c("running", name="z-container", service="a")
        b = _c("exited", exit_code=1, name="a-container", service="b")
        out = rollup_dots([b, a], P)
        self.assertLess(out.index("●"), out.index("✕"))

    def test_without_count_is_dots_only(self) -> None:
        out = rollup_dots(self._members(2), P, with_count=False)
        self.assertNotIn("up", out)

    def test_empty(self) -> None:
        self.assertEqual(rollup_dots([], P), "")
