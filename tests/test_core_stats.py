"""Unit tests for the live-stats history and its sparkline/panel renderers."""

import unittest

from rich.console import Console
from rich.text import Text

from docksurf_py.core.models import ContainerStats
from docksurf_py.core.render.observability import (
    CPU_FLOOR,
    render_live,
    sparkline,
)
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from docksurf_py.core.state.details import LiveTargets
from docksurf_py.core.state.stats import StatsHistory
from docksurf_py.core.types import SafeMarkup

KB = 1024


def _sample(
    cpu: float = 0.0,
    rx: int = 0,
    tx: int = 0,
    br: int = 0,
    bw: int = 0,
    mem: int = 100,
    limit: int = 200,
) -> ContainerStats:
    return ContainerStats(cpu, mem, limit, mem / limit * 100, rx, tx, br, bw)


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


class StatsHistoryTests(unittest.TestCase):
    def test_first_sample_has_no_rates(self) -> None:
        h = StatsHistory()
        h.push(_sample(cpu=10, rx=500), now=100.0)
        self.assertEqual(list(h.cpu), [10])
        self.assertEqual(len(h.net_rx_rate), 0)
        self.assertEqual(h.latest, _sample(cpu=10, rx=500))

    def test_rates_are_counter_delta_over_elapsed_time(self) -> None:
        h = StatsHistory()
        h.push(_sample(rx=0, tx=0, br=0, bw=0), now=10.0)
        h.push(_sample(rx=2 * KB, tx=KB, br=4 * KB, bw=8 * KB), now=12.0)
        self.assertEqual(list(h.net_rx_rate), [KB])
        self.assertEqual(list(h.net_tx_rate), [KB / 2])
        self.assertEqual(list(h.blk_r_rate), [2 * KB])
        self.assertEqual(list(h.blk_w_rate), [4 * KB])
        self.assertEqual(h.net_rate(), [KB + KB / 2])
        self.assertEqual(h.blk_rate(), [6 * KB])

    def test_counter_reset_clamps_to_zero(self) -> None:
        h = StatsHistory()
        h.push(_sample(rx=10 * KB, bw=10 * KB), now=1.0)
        h.push(_sample(rx=100, bw=0), now=2.0)  # container restarted
        self.assertEqual(list(h.net_rx_rate), [0])
        self.assertEqual(list(h.blk_w_rate), [0])

    def test_non_advancing_clock_adds_no_rate(self) -> None:
        h = StatsHistory()
        h.push(_sample(rx=0), now=5.0)
        h.push(_sample(rx=KB), now=5.0)
        self.assertEqual(len(h.net_rx_rate), 0)
        self.assertEqual(len(h.cpu), 2)

    def test_ring_buffer_is_capped(self) -> None:
        h = StatsHistory(maxlen=3)
        for i in range(10):
            h.push(_sample(cpu=float(i), rx=i * KB), now=float(i))
        self.assertEqual(list(h.cpu), [7.0, 8.0, 9.0])
        self.assertEqual(len(h.net_rx_rate), 3)

    def test_reset_clears_everything(self) -> None:
        h = StatsHistory()
        h.push(_sample(cpu=1, rx=KB), now=1.0)
        h.push(_sample(cpu=2, rx=2 * KB), now=2.0)
        h.reset()
        self.assertEqual(len(h.cpu), 0)
        self.assertEqual(len(h.net_rx_rate), 0)
        self.assertIsNone(h.latest)
        # The next sample after a reset must not diff against the old one.
        h.push(_sample(rx=50 * KB), now=3.0)
        self.assertEqual(len(h.net_rx_rate), 0)

    def test_retargeting_stats_resets_the_history(self) -> None:
        live = LiveTargets()
        live.retarget_stats("web")
        live.history.push(_sample(cpu=5), now=1.0)
        self.assertFalse(live.retarget_stats("web"))  # unchanged: kept
        self.assertEqual(len(live.history.cpu), 1)
        live.retarget_stats("db")
        self.assertEqual(len(live.history.cpu), 0)


class SparklineTests(unittest.TestCase):
    def test_scales_against_the_max(self) -> None:
        self.assertEqual(_plain(sparkline([0, 50, 100], 3, P, "accent")), "▁▅█")

    def test_floor_keeps_idle_noise_flat(self) -> None:
        low = _plain(sparkline([0.0, 0.5, 1.0], 3, P, "success", CPU_FLOOR))
        self.assertNotIn("█", low)
        self.assertTrue(set(low) <= set("▁▂"))
        spike = _plain(sparkline([0.0, 50.0], 2, P, "success", CPU_FLOOR))
        self.assertEqual(spike, "▁█")

    def test_short_history_is_left_padded_in_faint(self) -> None:
        out = sparkline([100], 4, P, "accent")
        self.assertEqual(_plain(out), "▁▁▁█")
        self.assertTrue(out.startswith(f"[{P.faint}]▁▁▁"))

    def test_long_history_shows_the_last_width_values(self) -> None:
        self.assertEqual(_plain(sparkline([100, 0, 0, 100], 2, P, "accent")), "▁█")

    def test_all_zero_is_flat(self) -> None:
        self.assertEqual(_plain(sparkline([0, 0, 0], 3, P, "accent")), "▁▁▁")

    def test_empty_history_is_all_padding(self) -> None:
        self.assertEqual(_plain(sparkline([], 3, P, "accent")), "▁▁▁")

    def test_width_zero_and_one(self) -> None:
        self.assertEqual(sparkline([1, 2, 3], 0, P, "accent"), "")
        self.assertEqual(sparkline([1, 2, 3], -2, P, "accent"), "")
        self.assertEqual(_plain(sparkline([1, 2, 3], 1, P, "accent")), "█")

    def test_returns_safe_markup(self) -> None:
        self.assertIsInstance(sparkline([1], 2, P, "accent"), SafeMarkup)


def _render(history: StatsHistory, width: int = 70) -> str:
    console = Console(width=width, no_color=True, highlight=False)
    with console.capture() as cap:
        console.print(render_live(history, P, width))
    return cap.get()


class RenderLiveTests(unittest.TestCase):
    def test_empty_history_does_not_raise(self) -> None:
        text = _render(StatsHistory())
        for label in ("CPU", "MEM", "NET", "I/O"):
            self.assertIn(label, text)

    def test_shows_rates_not_totals(self) -> None:
        h = StatsHistory()
        h.push(_sample(cpu=12.4, rx=0, tx=0, br=0, bw=0), now=0.0)
        h.push(
            _sample(cpu=12.4, rx=3 * KB, tx=KB, br=0, bw=2 * KB * KB, mem=10 * KB * KB),
            now=1.0,
        )
        text = _render(h)
        self.assertIn("12.4%", text)
        self.assertIn("↓ 3.0kB/s", text)
        self.assertIn("↑ 1.0kB/s", text)
        self.assertIn("w 2.0MB/s", text)
        self.assertIn("10.00MB / 200.00B", text)

    def test_four_rows_at_any_reasonable_width(self) -> None:
        h = StatsHistory()
        h.push(_sample(cpu=1), now=0.0)
        for width in (40, 70, 120):
            lines = [ln for ln in _render(h, width).splitlines() if ln.strip()]
            self.assertEqual(len(lines), 4, width)
            self.assertTrue(all(len(ln) <= width for ln in lines), width)
