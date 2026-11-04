"""themes.py — curated `Theme` palettes."""

from textual.color import Color
from textual.theme import Theme

from docksurf_py.core.render.palette import Palette, blend

# Close to Textual's built-in `tokyo-night`; the brand accent (#2EE6C8) and the
# darker ground keep it distinct.
DOCKSURF_TIDE = Theme(
    name="docksurf-tide",
    primary="#7AA2F7",
    secondary="#BB9AF7",
    accent="#2EE6C8",
    foreground="#CDD6E4",
    background="#0B0E14",
    surface="#121824",
    panel="#253047",
    success="#9ECE6A",
    warning="#E5B567",
    error="#F7768E",
    dark=True,
    variables={
        "text-muted": "#7A879D",
        "border": "#2A3447",
        "border-blurred": "#2A3447",
        "scrollbar": "#253047",
        "scrollbar-background": "#0B0E14",
    },
)

DOCKSURF_FOREST = Theme(
    name="docksurf-forest",
    primary="#4CAF7D",
    secondary="#C9A66B",
    accent="#5EC8E0",
    foreground="#E6F1E9",
    background="#0A130E",
    surface="#111F17",
    panel="#182B1F",
    success="#7FD858",
    warning="#F2A65A",
    error="#E85D5D",
    dark=True,
    # Without these Textual makes `$border` the primary colour, so a focused
    # panel would look like every other one.
    variables={"border": "#1A3A2A", "border-blurred": "#1A3A2A"},
)

DOCKSURF_ABYSS = Theme(
    name="docksurf-abyss",
    primary="#5A8EC4",
    secondary="#8172B8",
    accent="#F03DAE",
    foreground="#C9D9E8",
    background="#03050A",
    surface="#0B111C",
    panel="#0D1420",
    success="#3FC7D9",
    warning="#B5C23D",
    error="#C6634A",
    dark=True,
    variables={"border": "#1D2E42", "border-blurred": "#1D2E42"},
)

CUSTOM_THEMES = [DOCKSURF_TIDE, DOCKSURF_FOREST, DOCKSURF_ABYSS]

DEFAULT_THEME_NAME = DOCKSURF_TIDE.name

THEME_CYCLE_NAMES = [theme.name for theme in CUSTOM_THEMES]

# Textual built-ins the palette can't be derived from: their colours are
# `ansi_*` names, which parse to black. Unregistered so `^p` doesn't offer them.
UNSUPPORTED_BUILTIN_THEMES = ("ansi-dark", "ansi-light")


def _h(color: Color) -> str:
    return color.hex6.lower()


def _hex(color: str | None, fallback: str) -> str:
    return _h(Color.parse(color)) if color else fallback


def palette_from_theme(theme: Theme) -> Palette:
    """The core `Palette` for a Textual theme: hex colours, with `muted`/`faint`
    derived the way Textual derives `$text-muted` when the theme has none."""
    background = _hex(theme.background, "#0b0e14" if theme.dark else "#ffffff")
    foreground = _hex(theme.foreground, "#cdd6e4" if theme.dark else "#1a1a1a")
    fg_color = Color.parse(foreground)
    bg_color = Color.parse(background)
    muted = theme.variables.get("text-muted")
    primary = _hex(theme.primary, foreground)
    accent = _hex(theme.accent, primary)
    return Palette(
        foreground=foreground,
        strong=_h(fg_color.lighten(0.15) if theme.dark else fg_color.darken(0.15)),
        muted=_hex(muted, _h(fg_color.blend(bg_color, 0.4))),
        faint=_h(fg_color.blend(bg_color, 0.7)),
        primary=primary,
        accent=accent,
        secondary=_hex(theme.secondary, primary),
        success=_hex(theme.success, "#9ece6a"),
        warning=_hex(theme.warning, "#e5b567"),
        error=_hex(theme.error, "#f7768e"),
        background=background,
        surface=_hex(theme.surface, background),
        panel=_hex(theme.panel, background),
        cursor_bg=blend(primary, background, 0.20),
        marked_bg=blend(accent, background, 0.12),
    )
