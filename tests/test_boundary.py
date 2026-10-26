"""Import-boundary guard: `docksurf_py.core` must never load Textual or reach
up into `docksurf_py.tui`.

Core logic stays unit-testable without a running app and approachable without
Textual knowledge only while nothing in it imports `textual` or the UI layer,
directly or transitively. Each check runs in a fresh interpreter, since this
test session already has both loaded via the app tests. Rich is allowed in
core; only Textual and `docksurf_py.tui` are banned.
"""

import pkgutil
import subprocess
import sys
import unittest

import docksurf_py.core

BANNED = ("textual", "docksurf_py.tui")


def _core_modules() -> list[str]:
    # Walked, not listed, so a new core module is covered the moment it exists.
    modules = [docksurf_py.core.__name__]
    for info in pkgutil.walk_packages(
        docksurf_py.core.__path__, prefix=f"{docksurf_py.core.__name__}."
    ):
        if not info.name.endswith(".__main__"):
            modules.append(info.name)
    return modules


def _banned_imports(module: str) -> list[str]:
    """Top-level banned packages that importing `module` loads."""
    code = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        f"banned = {BANNED!r}\n"
        "print(','.join(b for b in banned"
        " if any(m == b or m.startswith(b + '.') for m in sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise AssertionError(f"importing {module} failed:\n{result.stderr}")
    out = result.stdout.strip()
    return out.split(",") if out else []


class CoreBoundaryTests(unittest.TestCase):
    def test_core_modules_do_not_load_textual_or_tui(self) -> None:
        modules = _core_modules()
        # Guard against the walk silently finding nothing.
        self.assertIn("docksurf_py.core.docker.client", modules)
        for module in modules:
            with self.subTest(module=module):
                self.assertEqual(
                    _banned_imports(module), [], f"{module} imports a banned layer"
                )

    def test_guard_detects_textual_and_tui_imports(self) -> None:
        # Canaries: UI modules must trip the check, or the guard is vacuous.
        self.assertEqual(
            _banned_imports("docksurf_py.tui.themes"), ["textual", "docksurf_py.tui"]
        )
        # `tui.ids` loads no Textual, so only the layer rule catches it.
        self.assertEqual(_banned_imports("docksurf_py.tui.ids"), ["docksurf_py.tui"])
