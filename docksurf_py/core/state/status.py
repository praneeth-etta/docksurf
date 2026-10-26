"""Container run-state classification — the one place that decides whether a
container counts as running, stopped or failed (rows, rollups, chips, bars)."""

from collections.abc import Iterable
from enum import StrEnum

from docksurf_py.core.models import Container


class RunState(StrEnum):
    RUNNING = "running"  # running, health ok or none
    STARTING = "starting"  # running + health "starting", or restarting
    PAUSED = "paused"
    UNHEALTHY = "unhealthy"  # running + health "unhealthy"; failed in rollups
    STOPPED = "stopped"  # exited 0/130/143, or created
    FAILED = "failed"  # exited with anything else (incl. 137), dead


# 0 = clean, 130 = SIGINT, 143 = SIGTERM. 137 (SIGKILL) counts as failed even
# though a `docker stop` that times out also produces it.
CLEAN_EXIT_CODES = frozenset({0, 130, 143})

GLYPH: dict[RunState, str] = {
    RunState.RUNNING: "●",
    RunState.STARTING: "◐",
    RunState.PAUSED: "◐",
    RunState.UNHEALTHY: "✕",
    RunState.STOPPED: "○",
    RunState.FAILED: "✕",
}

# Palette role per state (see `core.render.palette.Palette`).
ROLE: dict[RunState, str] = {
    RunState.RUNNING: "success",
    RunState.STARTING: "warning",
    RunState.PAUSED: "warning",
    RunState.UNHEALTHY: "error",
    RunState.STOPPED: "muted",
    RunState.FAILED: "error",
}


def classify(c: Container) -> RunState:
    if c.state == "paused":
        return RunState.PAUSED
    if c.state == "restarting":
        return RunState.STARTING
    if c.running:
        if c.health == "unhealthy":
            return RunState.UNHEALTHY
        if c.health == "starting":
            return RunState.STARTING
        return RunState.RUNNING
    if c.state == "dead":
        return RunState.FAILED
    if c.state == "exited":
        return RunState.STOPPED if c.exit_code in CLEAN_EXIT_CODES else RunState.FAILED
    return RunState.STOPPED  # created (never started), unknown


def is_failed(state: RunState) -> bool:
    return state in (RunState.FAILED, RunState.UNHEALTHY)


def is_up(state: RunState) -> bool:
    """Counts toward `N/M up`: the container process is alive."""
    return state in (
        RunState.RUNNING,
        RunState.STARTING,
        RunState.PAUSED,
        RunState.UNHEALTHY,
    )


def project_state_counts(containers: Iterable[Container]) -> tuple[int, int, int]:
    """`(up, down, failed)`. A failed container is also down, not up, unless it
    is an unhealthy-but-running one (up *and* failed) — so `up + down` is
    always the total and `failed` is a subset view."""
    up = down = failed = 0
    for c in containers:
        state = classify(c)
        if is_up(state):
            up += 1
        else:
            down += 1
        if is_failed(state):
            failed += 1
    return up, down, failed


def partition_counts(containers: Iterable[Container]) -> tuple[int, int, int]:
    """`(up, down, failed)` as a true partition (they sum to the total), for
    the global bar and filter chips: failed containers (including unhealthy
    ones) are counted only under `failed`, never also under up/down."""
    up = down = failed = 0
    for c in containers:
        state = classify(c)
        if is_failed(state):
            failed += 1
        elif is_up(state):
            up += 1
        else:
            down += 1
    return up, down, failed
