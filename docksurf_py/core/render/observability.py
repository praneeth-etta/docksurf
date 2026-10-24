"""Rich renderables for live stats, container top, and `docker system df`."""

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from docksurf_py.core.docker import format_size
from docksurf_py.core.models import ContainerStats, ContainerTop, SystemDf


def usage_bar(percent: float, width: int = 40) -> str:
    """A fixed-width colour-coded usage bar as Rich markup."""
    filled = max(0, min(width, round(percent / 100 * width)))
    return f"[green]{'█' * filled}[/][dim]{'░' * (width - filled)}[/]"


def render_stats(stats: ContainerStats, name: str) -> Panel:
    """Build the live-stats renderable for the detail pane."""
    table = Table(show_header=False, expand=True, box=None)
    table.add_column("k", style="cyan", justify="right", width=4)
    table.add_column("v")
    table.add_row("CPU", f"{usage_bar(stats.cpu_percent)} {stats.cpu_percent:.1f}%")
    mem = (
        f"{format_size(stats.mem_used)} / {format_size(stats.mem_limit)} "
        f"({stats.mem_percent:.1f}%)"
    )
    table.add_row("MEM", f"{usage_bar(stats.mem_percent)} {mem}")
    table.add_row(
        "NET", f"↓ {format_size(stats.net_rx)}   ↑ {format_size(stats.net_tx)}"
    )
    table.add_row(
        "BLK", f"↓ {format_size(stats.blk_read)}   ↑ {format_size(stats.blk_write)}"
    )

    return Panel(
        table,
        title=f"[b green]Live stats: {escape(name)}[/b green]",
        border_style="dim",
        box=box.MINIMAL,
    )


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
