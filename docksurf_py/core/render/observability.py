"""Rich renderables for live stats, container top, and `docker system df`."""

from collections.abc import Sequence

from rich import box
from rich.console import RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from docksurf_py.core.docker import format_size
from docksurf_py.core.models import ContainerTop, Image, SystemDf
from docksurf_py.core.render.containers import gauge
from docksurf_py.core.render.palette import Palette, fg
from docksurf_py.core.state.stats import StatsHistory
from docksurf_py.core.types import SafeMarkup

_SPARK = "▁▂▃▄▅▆▇█"
# Idle CPU noise below this reads as flat instead of as spikes.
CPU_FLOOR = 5.0
_LABEL_WIDTH = 4
_VALUE_WIDTH = 24


def sparkline(
    values: Sequence[float],
    width: int,
    p: Palette,
    role: str,
    floor: float = 0.0,
) -> SafeMarkup:
    """The last `width` values as `▁▂▃▄▅▆▇█`, scaled against `max(values,
    floor)`. A history shorter than `width` is left-padded with faint `▁`."""
    if width <= 0:
        return SafeMarkup("")
    shown = list(values)[-width:]
    scale = max([floor, *shown])
    top = len(_SPARK) - 1
    glyphs = "".join(
        _SPARK[min(top, round(v / scale * top))] if scale > 0 and v > 0 else _SPARK[0]
        for v in shown
    )
    pad = fg(p, "faint", _SPARK[0] * (width - len(shown))) if len(shown) < width else ""
    return SafeMarkup(pad + (fg(p, role, glyphs) if glyphs else ""))


def _rate(per_second: float) -> str:
    """`642B/s`, `1.8kB/s`, `12MB/s`."""
    value = per_second
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            digits = 0 if value >= 100 or unit == "B" else 1
            return f"{value:.{digits}f}{unit}/s"
        value /= 1024
    raise AssertionError("unreachable")


def _last(series: Sequence[float]) -> float:
    return series[-1] if series else 0.0


def render_live(history: StatsHistory, p: Palette, width: int) -> RenderableType:
    """The live panel body: CPU and NET/IO sparklines (rates, not totals), a
    MEM gauge, each with a right-aligned value. `width` is the panel's inner
    width in cells."""
    stats = history.latest
    graph = max(width - _LABEL_WIDTH - _VALUE_WIDTH - 2, 4)
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style=p.muted, no_wrap=True, width=_LABEL_WIDTH)
    grid.add_column(no_wrap=True, width=graph)
    grid.add_column(no_wrap=True, justify="right", width=_VALUE_WIDTH)

    def row(label: str, bar: str, value: str) -> None:
        grid.add_row(
            label, Text.from_markup(bar), Text.from_markup(fg(p, "foreground", value))
        )

    cpu_now = stats.cpu_percent if stats else 0.0
    row(
        "CPU", sparkline(history.cpu, graph, p, "success", CPU_FLOOR), f"{cpu_now:.1f}%"
    )
    mem_percent = stats.mem_percent if stats else 0.0
    mem_text = (
        f"{format_size(stats.mem_used)} / {format_size(stats.mem_limit)}"
        if stats
        else "—"
    )
    row("MEM", gauge(mem_percent, graph, p, "primary", ""), mem_text)
    row(
        "NET",
        sparkline(history.net_rate(), graph, p, "accent"),
        f"↓ {_rate(_last(history.net_rx_rate))}  ↑ {_rate(_last(history.net_tx_rate))}",
    )
    row(
        "I/O",
        sparkline(history.blk_rate(), graph, p, "secondary"),
        f"r {_rate(_last(history.blk_r_rate))}  w {_rate(_last(history.blk_w_rate))}",
    )
    return grid


def render_top(top: ContainerTop, name: str) -> Panel:
    """Build the running-processes renderable for the detail pane.

    Every column is single-line and ellipsized (`no_wrap=True`) — wrapping
    the last column (`fold`) was tried first, but in the pane's realistic
    (narrow) width a long `CMD` value wraps into a tall column of a couple of
    characters per line, blowing out the panel's height. Non-last columns are
    additionally capped to a small `max_width` so Rich's column-width solver
    gives the remaining space to `CMD` (typically last, and the most useful
    field) instead of splitting it evenly.
    """
    table = Table(box=None, expand=True)
    for i, title in enumerate(top.titles):
        is_last = i == len(top.titles) - 1
        table.add_column(
            title,
            overflow="ellipsis",
            no_wrap=True,
            max_width=None if is_last else max(len(title), 5),
        )
    for process in top.processes:
        table.add_row(*process)
    return Panel(
        table,
        title=f"[b magenta]Processes: {escape(name)}[/b magenta]",
        border_style="dim",
        box=box.MINIMAL,
    )


def split_bar(
    used: int, reclaimable: int, width: int, p: Palette, *, approximate: bool = False
) -> SafeMarkup:
    """`━━━━━━━━━━━━` split into an in-use part (`success`) and a reclaimable
    part (`warning`), proportional to the two sizes. A non-zero part always
    gets at least one cell. `approximate` draws the whole bar `faint` (the
    figures are a guess, not `docker system df`)."""
    if width <= 0:
        return SafeMarkup("")
    total = used + reclaimable
    if total <= 0:
        return fg(p, "faint", "━" * width)
    reclaim_cells = round(reclaimable / total * width)
    if reclaimable > 0:
        reclaim_cells = max(1, reclaim_cells)
    if used > 0:
        reclaim_cells = min(reclaim_cells, width - 1)
    used_cells = width - reclaim_cells
    if approximate:
        return fg(p, "faint", "━" * width)
    return SafeMarkup(
        (fg(p, "success", "━" * used_cells) if used_cells else "")
        + (fg(p, "warning", "━" * reclaim_cells) if reclaim_cells else "")
    )


def images_disk_usage(
    df: SystemDf | None, images: Sequence[Image]
) -> tuple[int, int, bool]:
    """`(total, reclaimable, approximate)` bytes for the Images disk line.

    From the cached `docker system df` "Images" entry when known. Otherwise
    the snapshot's per-image sizes are summed — which double-counts shared
    layers, hence `approximate`.
    """
    if df is not None:
        for entry in df.entries:
            if entry.kind == "Images":
                return entry.size_bytes, entry.reclaimable_bytes, False
    total = sum(i.size_bytes for i in images)
    reclaimable = sum(i.size_bytes for i in images if not i.used_by)
    return total, reclaimable, True


_DISK_BAR_MIN = 8
_DISK_BAR_MAX = 60


def images_disk_line(
    df: SystemDf | None, images: Sequence[Image], width: int, p: Palette
) -> SafeMarkup:
    """`62.4 GB on disk  ━━━━━━  ■ in use 20.6 GB  ■ reclaimable 41.8 GB  P prune`.

    The bar takes the width the text leaves over (clamped); when even the
    minimum bar doesn't fit, the bar and then the legend are dropped.
    """
    total, reclaimable, approx = images_disk_usage(df, images)
    approx_mark = "≈" if approx else ""
    head = f"{approx_mark}{format_size(total)} on disk"
    legend_plain = (
        f"■ in use {format_size(total - reclaimable)}"
        f"  ■ reclaimable {format_size(reclaimable)}"
    )
    tail_plain = "P prune"
    gap = 3
    bar_width = width - len(head) - len(legend_plain) - len(tail_plain) - gap * 3
    legend = SafeMarkup(
        fg(p, "success", "■")
        + fg(p, "muted", f" in use {format_size(total - reclaimable)}")
        + "  "
        + fg(p, "warning", "■")
        + fg(p, "muted", f" reclaimable {format_size(reclaimable)}")
    )
    tail = SafeMarkup(fg(p, "strong", "P") + fg(p, "muted", " prune"))
    head_markup = fg(p, "foreground", head)
    sep = " " * gap
    if bar_width < _DISK_BAR_MIN:
        if width >= len(head) + gap + len(legend_plain):
            return SafeMarkup(f"{head_markup}{sep}{legend}")
        return head_markup
    bar = split_bar(
        total - reclaimable,
        reclaimable,
        min(bar_width, _DISK_BAR_MAX),
        p,
        approximate=approx,
    )
    return SafeMarkup(f"{head_markup}{sep}{bar}{sep}{legend}{sep}{tail}")


def render_df(df: SystemDf) -> Table:
    """Build the `docker system df` breakdown table for the modal screen."""
    table = Table(box=None, expand=True)
    table.add_column("Type", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Active", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Reclaimable", justify="right")
    for e in df.entries:
        pct = (
            f" ({e.reclaimable_bytes / e.size_bytes * 100:.0f}%)"
            if e.size_bytes
            else ""
        )
        table.add_row(
            e.kind,
            str(e.total_count),
            str(e.active_count),
            format_size(e.size_bytes),
            f"{format_size(e.reclaimable_bytes)}{pct}",
        )
    table.add_section()
    total_pct = (
        f" ({df.total_reclaimable / df.total_size * 100:.0f}%)" if df.total_size else ""
    )
    table.add_row(
        "[b]Total[/b]",
        "",
        "",
        f"[b]{format_size(df.total_size)}[/b]",
        f"[b]{format_size(df.total_reclaimable)}{total_pct}[/b]",
    )
    return table
