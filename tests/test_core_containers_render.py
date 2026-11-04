"""Unit tests for the Containers tab cell builders (`core/render/containers.py`)."""

import unittest

from docksurf_py.core.models import ComposeProject, PortBinding
from docksurf_py.core.render.containers import (
    age_seconds,
    chevron,
    compact_age,
    compact_ports,
    container_row_cells,
    gauge,
    parse_age,
    project_location,
    project_row_cells,
)
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from tests.test_compose import make_container


class CompactAgeTests(unittest.TestCase):
    def test_table(self) -> None:
        cases = {
            "Up 3 hours (healthy)": "3h",
            "Up About an hour": "1h",
            "Up 5 minutes": "5m",
            "Up Less than a second": "<1s",
            "Up 2 days (Paused)": "2d",
            "Up 3 weeks": "3w",
            "Exited (137) 14 minutes ago": "14m ago",
            "Exited (0) About a minute ago": "1m ago",
            "Created": "—",
            "": "—",
        }
        for status, expected in cases.items():
            self.assertEqual(compact_age(status), expected, status)

    def test_compact_drops_the_ago_suffix(self) -> None:
        self.assertEqual(compact_age("Exited (137) 14 minutes ago", "", True), "14m")
        self.assertEqual(compact_age("Up 3 hours", "", True), "3h")
        self.assertEqual(compact_age("Created", "", True), "—")

    def test_falls_back_to_uptime_hint(self) -> None:
        self.assertEqual(compact_age("running", "5 minutes"), "5m")
        self.assertEqual(compact_age("running", ""), "—")

    def test_age_seconds_orders_running_above_stopped(self) -> None:
        up = make_container("up")
        up.status = "Up 3 hours"
        old_exit = make_container("down", running=False)
        old_exit.status = "Exited (1) 2 days ago"
        new_exit = make_container("new", running=False)
        new_exit.status = "Exited (1) 5 minutes ago"
        self.assertEqual(parse_age("Up 3 hours"), (10800, "3h"))
        self.assertGreater(age_seconds(up), age_seconds(new_exit))
        self.assertGreater(age_seconds(new_exit), age_seconds(old_exit) - 1)


class CompactPortsTests(unittest.TestCase):
    def test_none_published(self) -> None:
        self.assertEqual(compact_ports([]), "—")
        self.assertEqual(compact_ports([PortBinding("80/tcp")]), "—")

    def test_first_binding_and_extra_count(self) -> None:
        ports = [
            PortBinding("5672/tcp", "0.0.0.0", "5672"),
            PortBinding("15672/tcp", "0.0.0.0", "8080"),
        ]
        self.assertEqual(compact_ports(ports), "5672→5672 +1")

    def test_dedupes_ipv4_and_ipv6(self) -> None:
        ports = [
            PortBinding("80/tcp", "0.0.0.0", "80"),
            PortBinding("80/tcp", "::", "80"),
        ]
        self.assertEqual(compact_ports(ports), "80→80")

    def test_keeps_specific_ip_and_udp(self) -> None:
        self.assertEqual(
            compact_ports([PortBinding("53/udp", "127.0.0.1", "53")]),
            "127.0.0.1:53→53/udp",
        )


class ProjectLocationTests(unittest.TestCase):
    def _project(self, working_dir: str, config: str) -> ComposeProject:
        return ComposeProject("p", [], config, working_dir)

    def test_home_shortened_and_default_config_hidden(self) -> None:
        p = self._project("/home/u/dev/app", "/home/u/dev/app/compose.yml")
        self.assertEqual(project_location(p, home="/home/u"), "~/dev/app")

    def test_non_default_config_appended(self) -> None:
        p = self._project("/srv/app", "/srv/app/compose.dev.yml,/srv/app/x.yml")
        self.assertEqual(
            project_location(p, home="/home/u"), "/srv/app · compose.dev.yml"
        )

    def test_prefix_match_is_path_aware(self) -> None:
        p = self._project("/home/user2/app", "")
        self.assertEqual(project_location(p, home="/home/user"), "/home/user2/app")


class GaugeTests(unittest.TestCase):
    def test_width_and_label(self) -> None:
        out = gauge(50, 15, P, "accent", "50%")
        self.assertIn("50%", out)
        self.assertEqual(out.count("━"), 15 - len("50%") - 1)

    def test_tiny_value_uses_half_cell(self) -> None:
        out = gauge(1, 15, P, "accent", "1%")
        self.assertIn("╸", out)
        self.assertEqual(out.count("━") + 1, 15 - len("1%") - 1)

    def test_zero_has_no_filled_cells(self) -> None:
        self.assertNotIn("╸", gauge(0, 15, P, "accent", "0%"))

    def test_clamps_over_100(self) -> None:
        out = gauge(450, 15, P, "accent", "450%")
        self.assertNotIn(P.faint, out)

    def test_too_narrow_for_a_bar_is_label_only(self) -> None:
        self.assertEqual(str(gauge(50, 3, P, "accent", "50%")), "50%")


class RowCellsTests(unittest.TestCase):
    def test_seven_cells_and_service_name(self) -> None:
        c = make_container("proj-web-1", project="proj", service="web")
        c.status = "Up 3 hours"
        cells = container_row_cells(c, branch="├─", p=P)
        self.assertEqual(len(cells), 5)
        self.assertIn("web", cells[0])
        self.assertNotIn("proj-web-1", cells[0])
        self.assertIn("├─", cells[0])
        self.assertIn("3h", cells[2])

    def test_standalone_uses_container_name(self) -> None:
        cells = container_row_cells(make_container("solo"), branch=None, p=P)
        self.assertIn("solo", cells[0])

    def test_markup_in_names_is_escaped(self) -> None:
        cells = container_row_cells(make_container("[red]x"), branch=None, p=P)
        self.assertIn("\\[red]x", cells[0])

    def test_project_row(self) -> None:
        a = make_container("a", project="p", service="a", working_dir="/srv/p")
        b = make_container("b", running=False, project="p", service="b")
        cells = project_row_cells(ComposeProject("p", [a, b], "", "/srv/p"), P)
        self.assertEqual(len(cells), 5)
        self.assertIn("p", cells[0])
        self.assertIn("1/2 up", cells[1])
        self.assertIn("/srv/p", cells[4])

    def test_compact_project_row_drops_the_count_text(self) -> None:
        a = make_container("a", project="p", service="a")
        b = make_container("b", running=False, project="p", service="b")
        project = ComposeProject("p", [a, b], "", "")
        self.assertIn("1/2 up", project_row_cells(project, P)[1])
        compact = project_row_cells(project, P, compact=True)
        self.assertEqual(len(compact), 5)
        self.assertNotIn("up", compact[1])
        self.assertIn("●", compact[1])

    def test_compact_container_row_shortens_the_age(self) -> None:
        c = make_container("x", running=False)
        c.status = "Exited (0) 14 minutes ago"
        self.assertIn("14m ago", container_row_cells(c, branch=None, p=P)[2])
        cells = container_row_cells(c, branch=None, p=P, compact=True)
        self.assertIn("14m", cells[2])
        self.assertNotIn("ago", cells[2])

    def test_chevron(self) -> None:
        self.assertIn("▸", chevron(True, P))
        self.assertIn("▾", chevron(False, P))
