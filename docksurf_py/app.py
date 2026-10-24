"""Compatibility entry point — the app lives in `docksurf_py.tui.app`.

Kept so `python -m docksurf_py.app` keeps working after the core/tui split.
"""

from docksurf_py.tui.app import main

if __name__ == "__main__":
    main()
