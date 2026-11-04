"""Unit tests for core/render/help — scopes, grouping and the help markup."""

import unittest

from rich.text import Text

from docksurf_py.core.render.help import (
    COMPOSE,
    CONTAINERS,
    EXTRA_ROWS,
    GLOBAL,
    IMAGES,
    JUMP,
    LOG_PANEL_ACTIONS,
    LOGS,
    NETWORKS,
    HelpRow,
    display_key,
    group_rows,
    help_markup,
    scope_for,
)
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from docksurf_py.tui.actions.container import ContainerActionHandler


class DisplayKeyTests(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(display_key("plus"), "+")
        self.assertEqual(display_key("escape"), "esc")
        self.assertEqual(display_key("enter"), "⏎")
        self.assertEqual(display_key("ctrl+u"), "^u")
        self.assertEqual(display_key("ctrl+k"), "^k")

    def test_plain_keys_pass_through(self) -> None:
        for key in ("s", "S", "?", "/", "1", "[", "space", "Tab", "↑ / ↓"):
            self.assertEqual(display_key(key), key)


class ScopeForTests(unittest.TestCase):
    CONTAINER = frozenset({"stop_container", "follow_logs", "next_match"})
    PROJECT = frozenset({"compose_up", "stop_container"})
    TABS = {
        IMAGES: frozenset({"image_history"}),
        NETWORKS: frozenset({"network_connect"}),
    }

    def scope(self, action: str) -> str:
        return scope_for(action, self.CONTAINER, self.PROJECT, self.TABS)

    def test_each_scope(self) -> None:
        self.assertEqual(self.scope("compose_up"), COMPOSE)
        self.assertEqual(self.scope("follow_logs"), LOGS)
        self.assertEqual(self.scope("stop_container"), COMPOSE)  # project wins
        self.assertEqual(self.scope("image_history"), IMAGES)
        self.assertEqual(self.scope("network_connect"), NETWORKS)
        self.assertEqual(self.scope("refresh"), GLOBAL)

    def test_a_log_action_the_container_handler_lacks_is_not_a_log_key(self) -> None:
        self.assertEqual(scope_for("log_top", frozenset(), frozenset(), {}), GLOBAL)

    def test_container_actions_that_are_not_log_keys(self) -> None:
        self.assertEqual(
            scope_for("exec_container", frozenset({"exec_container"}), frozenset(), {}),
            CONTAINERS,
        )

    def test_every_log_panel_action_exists_on_the_container_handler(self) -> None:
        implemented = {
            name.removeprefix("action_")
            for name in vars(ContainerActionHandler)
            if name.startswith("action_")
        }
        self.assertEqual(LOG_PANEL_ACTIONS - implemented, set())


class GroupRowsTests(unittest.TestCase):
    def test_sections_follow_the_scope_order(self) -> None:
        rows = [
            HelpRow("b", "volume", "Volumes tab"),
            HelpRow("r", "refresh", GLOBAL),
            HelpRow("s", "stop", CONTAINERS),
            HelpRow("q", "quit", GLOBAL),
        ]
        grouped = group_rows(rows)
        self.assertEqual([s for s, _ in grouped], [GLOBAL, CONTAINERS, "Volumes tab"])
        self.assertEqual([r.key for r in grouped[0][1]], ["r", "q"])

    def test_unknown_scopes_go_last_in_first_seen_order(self) -> None:
        rows = [
            HelpRow("x", "x", "Zeta"),
            HelpRow("y", "y", GLOBAL),
            HelpRow("z", "z", "Alpha"),
        ]
        self.assertEqual([s for s, _ in group_rows(rows)], [GLOBAL, "Zeta", "Alpha"])

    def test_exact_duplicates_are_dropped(self) -> None:
        row = HelpRow("s", "stop", CONTAINERS)
        self.assertEqual(group_rows([row, row])[0][1], [row])

    def test_empty(self) -> None:
        self.assertEqual(group_rows([]), [])

    def test_the_extra_rows_land_in_known_sections(self) -> None:
        scopes = [s for s, _ in group_rows(EXTRA_ROWS)]
        self.assertEqual(scopes, [GLOBAL, LOGS, JUMP])


class HelpMarkupTests(unittest.TestCase):
    ROWS = [
        (
            GLOBAL,
            [HelpRow("question_mark", "Help", GLOBAL), HelpRow("r", "Refresh", GLOBAL)],
        ),
        (CONTAINERS, [HelpRow("ctrl+u", "Compose Up", CONTAINERS)]),
    ]

    def lines(self) -> list[str]:
        return Text.from_markup(help_markup(self.ROWS, P)).plain.split("\n")

    def test_headings_then_chip_and_description(self) -> None:
        lines = self.lines()
        self.assertEqual(lines[0], GLOBAL)
        self.assertEqual(lines[1].split(), ["?", "Help"])
        self.assertEqual(lines[2].split(), ["r", "Refresh"])
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], CONTAINERS)
        self.assertEqual(lines[5].split(), ["^u", "Compose", "Up"])

    def test_descriptions_line_up(self) -> None:
        lines = self.lines()
        self.assertEqual(lines[1].index("Help"), lines[2].index("Refresh"))
        self.assertEqual(lines[1].index("Help"), lines[5].index("Compose"))

    def test_chips_carry_the_palette(self) -> None:
        out = help_markup(self.ROWS, P)
        self.assertIn(f"{P.strong} on {P.panel}", out)
        self.assertIn(f"[bold {P.accent}]{GLOBAL}[/]", out)

    def test_markup_in_descriptions_is_escaped(self) -> None:
        out = help_markup([(GLOBAL, [HelpRow("x", "[red]boom", GLOBAL)])], P)
        self.assertIn("[red]boom", Text.from_markup(out).plain)

    def test_no_sections(self) -> None:
        self.assertEqual(help_markup([], P), "")


if __name__ == "__main__":
    unittest.main()
