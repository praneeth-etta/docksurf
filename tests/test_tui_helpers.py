"""Pure tests for tui helpers: network summaries, log selection, opening a port."""

import inspect
import unittest
from unittest.mock import patch

from docksurf_py.core.models import (
    Container,
    Network,
    NetworkEndpoint,
    PortBinding,
)
from docksurf_py.core.render.topology import (
    _network_summary,
    network_members,
)
from docksurf_py.tui.actions import (
    _open_in_browser,
)
from docksurf_py.tui.actions.clipboard import _yank_fields
from docksurf_py.tui.widgets import (
    SelectableRichLog,
)
from tests.test_compose import make_container


def _network(name="net1", endpoints=None):
    return Network(
        id="n1",
        name=name,
        driver="bridge",
        subnet="172.18.0.0/16",
        gateway="172.18.0.1",
        scope="local",
        used_by=[],
        endpoints=endpoints or [],
    )


class NetworkTopologyHelperTests(unittest.TestCase):
    """`network_members` / `_network_summary` / yank."""

    def _net_and_containers(self) -> tuple[Network, list[Container]]:
        web = make_container("web")
        web.networks = ["net1", "proxy"]
        web.ports = [PortBinding("80/tcp", "0.0.0.0", "8080")]
        db = make_container("db", running=False)
        db.networks = ["net1"]
        net = _network(
            endpoints=[NetworkEndpoint(container_name="web", ipv4="172.18.0.2")]
        )
        return net, [web, db]

    def test_members_union_endpoints_and_stopped_containers(self) -> None:
        net, containers = self._net_and_containers()
        db, web = network_members(net, containers)
        self.assertEqual((db.name, web.name), ("db", "web"))
        self.assertFalse(db.running)
        self.assertEqual(db.ipv4, "")  # stopped: no endpoint, hence no IP
        self.assertTrue(web.running)
        self.assertEqual(web.ipv4, "172.18.0.2")
        self.assertEqual(web.ports, "0.0.0.0:8080->80/tcp")
        self.assertEqual(web.other_networks, ["proxy"])

    def test_endpoint_without_snapshot_container_still_listed(self) -> None:
        net = _network(
            endpoints=[NetworkEndpoint(container_name="ghost", ipv4="172.18.0.9")]
        )
        (member,) = network_members(net, [])
        self.assertTrue(member.running)
        self.assertEqual(member.ipv4, "172.18.0.9")

    def test_summary_has_header_and_one_line_per_member(self) -> None:
        net, containers = self._net_and_containers()
        lines = _network_summary(net, containers).splitlines()
        self.assertIn("net1", lines[0])
        self.assertIn("172.18.0.0/16", lines[0])
        self.assertIn("gateway 172.18.0.1", lines[0])
        self.assertTrue(
            any(
                "web" in ln and "running" in ln and "also on: proxy" in ln
                for ln in lines[1:]
            )
        )
        self.assertTrue(any("db" in ln and "stopped" in ln for ln in lines[1:]))

    def test_summary_for_empty_network(self) -> None:
        self.assertIn("(no containers attached)", _network_summary(_network(), []))

    def test_yank_fields_for_network_is_single_summary(self) -> None:
        # A single field means action_yank copies it directly, no picker.
        net, containers = self._net_and_containers()
        fields = _yank_fields(net, containers)
        self.assertEqual([label for label, _ in fields], ["Network summary"])
        self.assertIn("web", fields[0][1])


class SelectableRichLogSelectionTests(unittest.TestCase):
    """`get_selection` reconstructs the selected text from the line strips —
    upstream `RichLog` leaves it unimplemented. Pure logic, no app needed."""

    def _log(self, texts: list[str]) -> "SelectableRichLog":
        from rich.segment import Segment
        from textual.strip import Strip

        log = SelectableRichLog()
        log.lines = [Strip([Segment(t)]) for t in texts]
        return log

    def test_extracts_partial_single_line(self) -> None:
        from textual.geometry import Offset
        from textual.selection import Selection

        log = self._log(["hello world"])
        text, ending = log.get_selection(Selection(Offset(0, 0), Offset(5, 0)))
        self.assertEqual(text, "hello")
        self.assertEqual(ending, "\n")

    def test_extracts_whole_buffer_when_unbounded(self) -> None:
        from textual.selection import Selection

        log = self._log(["alpha", "beta", "gamma"])
        text, _ = log.get_selection(Selection(None, None))
        self.assertEqual(text, "alpha\nbeta\ngamma")


class OpenInBrowserTests(unittest.TestCase):
    """`_open_in_browser` is plain sync logic — no App/event loop needed.

    Regression: `webbrowser.open()` can return True on Linux/WSL even when
    nothing actually opened (it shells to `gio`/`xdg-open` without checking
    exit status), and WSL has neither of those by default — "Opening ..."
    would show while nothing happened. WSL now shells to `explorer.exe`
    directly instead.
    """

    def test_wsl_with_explorer_shells_to_explorer_exe(self) -> None:
        with (
            patch("docksurf_py.tui.actions.container._is_wsl", return_value=True),
            patch(
                "docksurf_py.tui.actions.container.shutil.which",
                return_value="/mnt/c/Windows/explorer.exe",
            ),
            patch("docksurf_py.tui.actions.container.subprocess.run") as run,
            patch("docksurf_py.tui.actions.container.webbrowser.open") as browser_open,
        ):
            result = _open_in_browser("http://localhost:8080")
        self.assertTrue(result)
        run.assert_called_once_with(
            ["explorer.exe", "http://localhost:8080"], check=False
        )
        browser_open.assert_not_called()

    def test_non_wsl_falls_back_to_webbrowser(self) -> None:
        with (
            patch("docksurf_py.tui.actions.container._is_wsl", return_value=False),
            patch("docksurf_py.tui.actions.container.subprocess.run") as run,
            patch(
                "docksurf_py.tui.actions.container.webbrowser.open", return_value=True
            ) as browser_open,
        ):
            result = _open_in_browser("http://localhost:8080")
        self.assertTrue(result)
        browser_open.assert_called_once_with("http://localhost:8080")
        run.assert_not_called()

    def test_wsl_without_explorer_falls_back_to_webbrowser(self) -> None:
        with (
            patch("docksurf_py.tui.actions.container._is_wsl", return_value=True),
            patch("docksurf_py.tui.actions.container.shutil.which", return_value=None),
            patch(
                "docksurf_py.tui.actions.container.webbrowser.open", return_value=True
            ) as browser_open,
        ):
            result = _open_in_browser("http://localhost:8080")
        self.assertTrue(result)
        browser_open.assert_called_once_with("http://localhost:8080")

    def test_explorer_launch_failure_returns_false(self) -> None:
        with (
            patch("docksurf_py.tui.actions.container._is_wsl", return_value=True),
            patch(
                "docksurf_py.tui.actions.container.shutil.which",
                return_value="/mnt/c/Windows/explorer.exe",
            ),
            patch(
                "docksurf_py.tui.actions.container.subprocess.run",
                side_effect=OSError("no such file"),
            ),
        ):
            result = _open_in_browser("http://localhost:8080")
        self.assertFalse(result)


class ResourceTableHookTests(unittest.TestCase):
    """Row tints rely on a private Textual hook; fail loudly if an upgrade moves it."""

    def test_row_style_hook_still_exists_and_is_overridden(self) -> None:
        from textual.widgets import DataTable

        from docksurf_py.tui.widgets.tables import ResourceTable

        self.assertTrue(callable(getattr(DataTable, "_get_row_style", None)))
        self.assertIn("_get_row_style", vars(ResourceTable))
        params = list(inspect.signature(DataTable._get_row_style).parameters)
        self.assertEqual(params, ["self", "row_index", "base_style"])
