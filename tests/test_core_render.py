"""Unit tests for core/render — table/detail markup helpers and log-line
highlighting. Plain pytest, no Textual app."""

import unittest

from docksurf_py.core.models import ComposeProject, HealthProbe
from docksurf_py.core.render.logs import highlight_match
from docksurf_py.core.render.markup import (
    format_health_log,
    health_markup,
    image_status_markup,
    project_status_markup,
    status_color,
    status_markup,
    truncate,
)
from docksurf_py.core.types import STATUS_GREEN, STATUS_RED, STATUS_YELLOW, SafeMarkup
from tests.test_compose import make_container


class TruncateTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(truncate("nginx", 10), "nginx")
        self.assertEqual(truncate("exactly10!", 10), "exactly10!")

    def test_tail_truncation_keeps_limit(self) -> None:
        out = truncate("a-very-long-repository-name", 10)
        self.assertEqual(out, "a-very-lo…")
        self.assertEqual(len(out), 10)

    def test_middle_truncation_keeps_both_ends(self) -> None:
        out = truncate("project_postgres_data_volume", 11, middle=True)
        self.assertEqual(out, "proje…olume")
        self.assertEqual(len(out), 11)


class StatusMarkupTests(unittest.TestCase):
    def test_color_follows_state(self) -> None:
        self.assertEqual(status_color(make_container("a")), STATUS_GREEN)
        self.assertEqual(status_color(make_container("b", running=False)), STATUS_RED)
        paused = make_container("c")
        paused.running, paused.state = False, "paused"
        self.assertEqual(status_color(paused), STATUS_YELLOW)

    def test_running_health_glyphs(self) -> None:
        self.assertIn("✓", status_markup(make_container("a", health="healthy")))
        self.assertIn("✗", status_markup(make_container("a", health="unhealthy")))
        self.assertIn("…", status_markup(make_container("a", health="starting")))

    def test_nonzero_exit_code_is_shown(self) -> None:
        c = make_container("a", running=False)
        c.exit_code = 137
        self.assertIn("(137)", status_markup(c))

    def test_status_text_is_escaped(self) -> None:
        c = make_container("a")
        c.status = "Up [bold]"
        self.assertIn("\\[bold]", status_markup(c))
        self.assertIsInstance(status_markup(c), SafeMarkup)

    def test_health_markup_dash_when_no_healthcheck(self) -> None:
        self.assertEqual(health_markup(make_container("a")), "[dim]—[/]")


class FormatHealthLogTests(unittest.TestCase):
    def test_empty_log_is_none(self) -> None:
        self.assertIsNone(format_health_log([]))

    def test_keeps_only_most_recent_probes(self) -> None:
        log = [HealthProbe(start="", exit_code=i, output=f"p{i}") for i in range(7)]
        out = format_health_log(log, limit=2)
        assert out is not None
        self.assertNotIn("p4", out)
        self.assertIn("p5", out)
        self.assertIn("p6", out)

    def test_pass_fail_markers_and_empty_output(self) -> None:
        log = [
            HealthProbe(start="", exit_code=0, output=""),
            HealthProbe(start="", exit_code=1, output="boom"),
        ]
        out = format_health_log(log)
        assert out is not None
        self.assertIn("[✓ pass]  —\n(no output)", out)
        self.assertIn("[✗ exit 1]  —\nboom", out)


class ProjectAndImageMarkupTests(unittest.TestCase):
    def test_project_summary_counts_running(self) -> None:
        project = ComposeProject(
            name="shop",
            containers=[make_container("a"), make_container("b", running=False)],
            config_files="",
            working_dir="",
        )
        self.assertEqual(
            project_status_markup(project), f"[{STATUS_YELLOW}]1/2 running[/]"
        )

    def test_image_in_use_vs_unused(self) -> None:
        self.assertIn("In Use", image_status_markup(["web"]))
        self.assertIn("Unused", image_status_markup([]))


class HighlightMatchTests(unittest.TestCase):
    def test_highlights_every_case_insensitive_occurrence(self) -> None:
        out = highlight_match("Error: error", "error")
        self.assertEqual(out.count("[bold yellow]"), 2)

    def test_no_term_just_escapes(self) -> None:
        self.assertEqual(highlight_match("[x]", ""), "\\[x]")

    def test_safe_markup_passes_through(self) -> None:
        line = SafeMarkup("[red]already markup[/]")
        self.assertIs(highlight_match(line, "markup"), line)

    def test_regex_metacharacters_in_term_are_literal(self) -> None:
        out = highlight_match("cost $5.00 (approx)", "(approx)")
        self.assertIn("[bold yellow](approx)[/]", out)
