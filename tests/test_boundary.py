"""Import-boundary guard: `docksurf_py.core` must never load Textual.

Core logic stays unit-testable without a running app and approachable without
Textual knowledge only while nothing in it imports `textual`, directly or
transitively. Each check runs in a fresh interpreter, since this test session
already has Textual loaded via the app tests. See local/UI_SEPARATION_PLAN.md
Phase 0. Rich is allowed in core (decision D-2); only Textual is banned.
"""

import pkgutil
import subprocess
import sys
import unittest

import docksurf_py.core


def _core_modules() -> list[str]:
    # Walked, not listed, so a new core module is covered the moment it exists.
    modules = [docksurf_py.core.__name__]
    for info in pkgutil.walk_packages(
        docksurf_py.core.__path__, prefix=f"{docksurf_py.core.__name__}."
    ):
        if not info.name.endswith(".__main__"):
            modules.append(info.name)
    return modules


def _loads_textual(module: str) -> bool:
    code = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        "sys.exit(1 if any(m == 'textual' or m.startswith('textual.')"
        " for m in sys.modules) else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    if result.returncode not in (0, 1):
        raise AssertionError(f"importing {module} failed:\n{result.stderr}")
    return result.returncode == 1


class CoreBoundaryTests(unittest.TestCase):
    def test_core_modules_do_not_load_textual(self) -> None:
        modules = _core_modules()
        # Guard against the walk silently finding nothing.
        self.assertIn("docksurf_py.core.docker.client", modules)
        for module in modules:
            with self.subTest(module=module):
                self.assertFalse(
                    _loads_textual(module), f"{module} transitively imports textual"
                )

    def test_guard_detects_a_textual_import(self) -> None:
        # Canary: a UI module must trip the check, or the guard is vacuous.
        self.assertTrue(_loads_textual("docksurf_py.tui.themes"))
