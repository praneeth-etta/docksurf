"""Key bindings stay coherent: no key bound twice with different meanings in the
same scope, every binding is described (so `?` and `^p` list it) and resolves to
a real action. App-level and table-level bindings are separate scopes; a table
may mirror an app binding only with the same action."""

import unittest
from collections import Counter

from docksurf_py.tui.app import DockSurfApp, binding_triples
from docksurf_py.tui.widgets.tables import (
    ContainerTable,
    ImageTable,
    NetworkTable,
    ResourceTable,
    VolumeTable,
)

TABLES = (ResourceTable, ContainerTable, ImageTable, VolumeTable, NetworkTable)


def _keys(key: str) -> list[str]:
    """A binding's keys (`"a,b"` binds two)."""
    return [k.strip() for k in key.split(",")]


def _pairs(bindings) -> list[tuple[str, str]]:
    return [
        (k, action) for key, action, _ in binding_triples(bindings) for k in _keys(key)
    ]


class AppBindingTests(unittest.TestCase):
    def test_no_key_is_bound_twice(self) -> None:
        counts = Counter(k for k, _ in _pairs(DockSurfApp.BINDINGS))
        self.assertEqual({k: n for k, n in counts.items() if n > 1}, {})

    def test_no_action_is_bound_to_two_keys_with_different_descriptions(self) -> None:
        seen: dict[str, set[str]] = {}
        for _key, action, description in binding_triples(DockSurfApp.BINDINGS):
            seen.setdefault(action, set()).add(description)
        self.assertEqual({a: d for a, d in seen.items() if len(d) > 1}, {})

    def test_every_binding_is_described(self) -> None:
        """No description means it is missing from `?` and `^p`."""
        missing = [
            (key, action)
            for key, action, description in binding_triples(DockSurfApp.BINDINGS)
            if not description
        ]
        self.assertEqual(missing, [])

    def test_every_binding_resolves_to_an_action(self) -> None:
        """The palette skips an action with no `action_*` method, silently."""
        missing = [
            action
            for _key, action, _ in binding_triples(DockSurfApp.BINDINGS)
            if not callable(getattr(DockSurfApp, f"action_{action}", None))
        ]
        self.assertEqual(missing, [])


class TableBindingTests(unittest.TestCase):
    app_actions = dict(_pairs(DockSurfApp.BINDINGS))

    def test_no_key_is_bound_twice_on_a_table(self) -> None:
        for table in TABLES:
            with self.subTest(table=table.__name__):
                counts = Counter(k for k, _ in _pairs(table.BINDINGS))
                self.assertEqual({k: n for k, n in counts.items() if n > 1}, {})

    def test_a_table_key_that_is_also_an_app_key_means_the_same_action(self) -> None:
        for table in TABLES:
            for key, action in _pairs(table.BINDINGS):
                if key in self.app_actions:
                    with self.subTest(table=table.__name__, key=key):
                        self.assertEqual(action, self.app_actions[key])

    def test_table_actions_resolve_on_the_table_or_the_app(self) -> None:
        for table in TABLES:
            for _key, action in _pairs(table.BINDINGS):
                with self.subTest(table=table.__name__, action=action):
                    self.assertTrue(
                        callable(getattr(table, f"action_{action}", None))
                        or callable(getattr(DockSurfApp, f"action_{action}", None))
                    )

    def test_keys_that_only_one_table_binds_do_not_shadow_app_keys(self) -> None:
        """A tab-only key (`h`, `b`, `v`, `m`) may be an app key too, but only
        with the same action; this guards the per-tab handlers specifically."""
        for table in (ImageTable, VolumeTable, NetworkTable):
            for key, action in _pairs(table.BINDINGS):
                with self.subTest(table=table.__name__, key=key):
                    self.assertEqual(self.app_actions.get(key), action)


if __name__ == "__main__":
    unittest.main()
