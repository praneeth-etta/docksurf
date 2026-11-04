"""Theme-aware colours for core renderers.

DataTable cells go through Rich `Text.from_markup`, which can't resolve Textual
`$variables`, so renderers take a `Palette` (role -> Rich colour string) built
by the tui from the active theme instead of hard-coding colour names.
"""

import re
from dataclasses import dataclass, fields

from rich.markup import escape

from docksurf_py.core.types import SafeMarkup

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True, slots=True)
class Palette:
    """Role -> Rich colour string: a `#rrggbb` hex for every role."""

    foreground: str  # body text
    strong: str  # bright text (names, selected row)
    muted: str  # secondary text, headers
    faint: str  # tree connectors, empty gauge track, "—"
    primary: str  # focus, cursor, filter chip on, MEM gauge
    accent: str  # brand, marks, NET sparkline
    secondary: str  # networks, anonymous volumes, registry pill, I/O sparkline
    success: str  # running, in use
    warning: str  # reclaimable, orphaned, partial rollup
    error: str  # failed
    background: str  # app background; text colour on filled pills
    surface: str  # group-row background
    panel: str  # key-chip / pill background
    cursor_bg: str  # selected-row background
    marked_bg: str  # marked-row background


ROLES: frozenset[str] = frozenset(f.name for f in fields(Palette))


def is_hex(color: str) -> bool:
    return bool(_HEX.match(color))


def blend(fg_hex: str, bg_hex: str, alpha: float) -> str:
    """`fg_hex` over `bg_hex` at `alpha` (0 = all bg, 1 = all fg), as `#rrggbb`.

    Returns `fg_hex` unchanged if either side isn't a hex colour (nothing to mix).
    """
    if not (is_hex(fg_hex) and is_hex(bg_hex)):
        return fg_hex
    alpha = max(0.0, min(1.0, alpha))
    out = "#"
    for i in (1, 3, 5):
        f = int(fg_hex[i : i + 2], 16)
        b = int(bg_hex[i : i + 2], 16)
        out += f"{round(b + (f - b) * alpha):02x}"
    return out


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance of a `#rrggbb` colour."""

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio of two `#rrggbb` colours, from 1 (identical) to 21."""
    if not (is_hex(a) and is_hex(b)):
        raise ValueError(f"contrast needs hex colours, got {a!r} and {b!r}")
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _color(p: Palette, role: str) -> str:
    if role not in ROLES:
        raise KeyError(f"unknown palette role: {role!r}")
    return getattr(p, role)


def fg(p: Palette, role: str, text: str) -> SafeMarkup:
    """`text` (escaped) in the colour of `role`."""
    return SafeMarkup(f"[{_color(p, role)}]{escape(text)}[/]")


def pill(p: Palette, label: str, role: str) -> SafeMarkup:
    """` LABEL ` as a filled badge: background-coloured text on `role`."""
    return SafeMarkup(f"[{p.background} on {_color(p, role)}] {escape(label)} [/]")


def key_chip(p: Palette, key: str) -> SafeMarkup:
    """` s ` on the panel colour, for the key-hint bar."""
    return SafeMarkup(f"[{p.strong} on {p.panel}] {escape(key)} [/]")


def _tide() -> Palette:
    background = "#0B0E14"
    primary = "#7AA2F7"
    accent = "#2EE6C8"
    return Palette(
        foreground="#CDD6E4",
        strong="#E8EEF8",
        muted="#7A879D",
        faint="#3A4660",
        primary=primary,
        accent=accent,
        secondary="#BB9AF7",
        success="#9ECE6A",
        warning="#E5B567",
        error="#F7768E",
        background=background,
        surface="#121824",
        panel="#253047",
        cursor_bg=blend(primary, background, 0.20),
        marked_bg=blend(accent, background, 0.12),
    )


TIDE_PALETTE = _tide()
