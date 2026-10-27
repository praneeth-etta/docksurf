"""Live-stats history: a ring buffer of samples for one container, with
per-second rates derived from the cumulative network and block-I/O counters."""

from collections import deque

from docksurf_py.core.models import ContainerStats


class StatsHistory:
    """Samples for the container the live panel follows.

    CPU is stored as sampled. The network and block counters are cumulative,
    so each rate is `Δcounter / Δt` between consecutive samples; a counter
    that went backwards (the container restarted) counts as 0. The first
    sample has nothing to diff against, so the rate series start one sample
    later than `cpu`. `now` is injected (monotonic seconds) for testability.
    """

    def __init__(self, maxlen: int = 120) -> None:
        self.maxlen = maxlen
        self.cpu: deque[float] = deque(maxlen=maxlen)
        self.net_rx_rate: deque[float] = deque(maxlen=maxlen)
        self.net_tx_rate: deque[float] = deque(maxlen=maxlen)
        self.blk_r_rate: deque[float] = deque(maxlen=maxlen)
        self.blk_w_rate: deque[float] = deque(maxlen=maxlen)
        self.latest: ContainerStats | None = None
        self._prev_at: float | None = None

    def push(self, sample: ContainerStats, now: float) -> None:
        self.cpu.append(sample.cpu_percent)
        prev = self.latest
        if prev is not None and self._prev_at is not None and now > self._prev_at:
            dt = now - self._prev_at
            self.net_rx_rate.append(max(0, sample.net_rx - prev.net_rx) / dt)
            self.net_tx_rate.append(max(0, sample.net_tx - prev.net_tx) / dt)
            self.blk_r_rate.append(max(0, sample.blk_read - prev.blk_read) / dt)
            self.blk_w_rate.append(max(0, sample.blk_write - prev.blk_write) / dt)
        self.latest = sample
        self._prev_at = now

    def reset(self) -> None:
        for series in (
            self.cpu,
            self.net_rx_rate,
            self.net_tx_rate,
            self.blk_r_rate,
            self.blk_w_rate,
        ):
            series.clear()
        self.latest = None
        self._prev_at = None

    def net_rate(self) -> list[float]:
        """Combined rx+tx rate per sample, for the NET sparkline."""
        return [rx + tx for rx, tx in zip(self.net_rx_rate, self.net_tx_rate)]

    def blk_rate(self) -> list[float]:
        """Combined read+write rate per sample, for the I/O sparkline."""
        return [r + w for r, w in zip(self.blk_r_rate, self.blk_w_rate)]
