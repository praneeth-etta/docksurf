"""themes.py — curated `Theme` palettes."""

from textual.color import Color
from textual.theme import Theme

from docksurf_py.core.render.palette import ANSI_PALETTE, Palette, blend

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

DOCKSURF_NIGHTCITY = Theme(
    name="docksurf-nightcity",
    primary="#2B2D2DFF",
    secondary="#4A4159",
    accent="#3D5B5E",
    foreground="#E4DCF5",
    background="#0D0C0D",
    surface="#3F2C3E",
    panel="#141317",
    success="#639B65",
    warning="#B0916F",
    error="#B35959",
    dark=True,
    variables={
        "scrollbar-background": "#221E24",
        "scrollbar": "#6E6178",
        "scrollbar-hover": "#8A7B94",
        "scrollbar-active": "#B3A3C2",
    },
)

DOCKSURF_LINEN = Theme(
    name="docksurf-linen",
    primary="#A6875D",
    secondary="#6E6A63",
    accent="#8C6239",
    foreground="#2A241C",
    background="#F6F3EC",
    surface="#EDE6D8",
    panel="#E2D9C6",
    success="#3F6B3F",
    warning="#8A5A2E",
    error="#8B3A3A",
    dark=False,
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
)

DOCKSURF_SAKURA = Theme(
    name="docksurf-sakura",
    primary="#E7B6C3",
    secondary="#5B4B8A",
    accent="#A0A7BA",
    foreground="#2B1B22",
    background="#FBCAD7",
    surface="#F3E4E9",
    panel="#ECD8DF",
    success="#1B7A43",
    warning="#C9A27F",
    error="#A31D1D",
    dark=False,
)

DOCKSURF_ABYSS = Theme(
    name="docksurf-abyss",
    primary="#5A8EC4",
    secondary="#8172B8",
    accent="#F03DAE",
    foreground="#C9D9E8",
    background="#03050A",
    surface="#080D16",
    panel="#0D1420",
    success="#3FC7D9",
    warning="#B5C23D",
    error="#C6634A",
    dark=True,
)

CUSTOM_THEMES = [
    DOCKSURF_TIDE,
    DOCKSURF_NIGHTCITY,
    DOCKSURF_LINEN,
    DOCKSURF_FOREST,
    DOCKSURF_SAKURA,
    DOCKSURF_ABYSS,
]

DEFAULT_THEME_NAME = DOCKSURF_TIDE.name

TERMINAL_NATIVE_THEME_NAMES = ["ansi-dark", "ansi-light"]

THEME_CYCLE_NAMES = [
    theme.name for theme in CUSTOM_THEMES
] + TERMINAL_NATIVE_THEME_NAMES


def _h(color: Color) -> str:
    return color.hex6.lower()


def _hex(color: str | None, fallback: str) -> str:
    return _h(Color.parse(color)) if color else fallback


def palette_from_theme(theme: Theme) -> Palette:
    """The core `Palette` for a Textual theme. Terminal-native themes get
    named ANSI colours (the terminal does the theming); the rest get hex
    colours, with `muted`/`faint` derived the way Textual derives
    `$text-muted`."""
    if theme.name in TERMINAL_NATIVE_THEME_NAMES:
        return ANSI_PALETTE
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
