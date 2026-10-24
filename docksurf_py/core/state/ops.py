"""Pure planning for write actions: delete plans and exec/cp argument building."""

import shlex
from dataclasses import dataclass
from typing import Callable

from docksurf_py.core.models import CommandResult, Container

EXEC_SHELL_CANDIDATES = ("bash", "sh")


@dataclass(frozen=True)
class DeletePlan:
    """What to confirm and run to delete one focused resource.

    `force_default=None` renders a plain Confirm/Cancel dialog and `command`
    is always called with `force=False`. A `bool` shows a "Force" checkbox
    pre-checked to that default; `command` is then called with whatever the
    user leaves the checkbox at. Bulk delete (no interactive checkbox per
    row) always calls `command` with the plan's own `force_default`.
    """

    confirm_message: str
    command: Callable[[bool], CommandResult]
    success_message: str
    force_default: bool | None = None


def bind_delete_command(plan: DeletePlan, force: bool) -> Callable[[], CommandResult]:
    """Bind a `DeletePlan.command` to a fixed `force`, for non-interactive
    (bulk) delete jobs that can't show a per-row checkbox."""
    return lambda: plan.command(force)


def select_exec_shell(
    candidates: tuple[str, ...], probe: Callable[[str], bool]
) -> str | None:
    """Return the first candidate shell for which `probe` succeeds, or None."""
    for shell in candidates:
        if probe(shell):
            return shell
    return None


def build_exec_argv(
    container_id: str, command: str, user: str = ""
) -> list[str] | None:
    """Build the `docker exec` argv for a custom command/user.

    Returns `None` (caller notifies) if `command` is blank or fails to parse
    as shell tokens (e.g. unbalanced quotes).
    """
    command = command.strip()
    if not command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    argv = ["docker", "exec", "-it"]
    if user.strip():
        argv += ["-u", user.strip()]
    argv += [container_id, *tokens]
    return argv


def build_cp_paths(c: Container, src: str, dst: str) -> tuple[str, str] | None:
    """Resolve `PromptScreen` values into a `docker cp` src/dst pair.

    `PromptScreen` is pre-filled with `<name>:` on one side (per the caller);
    exactly one side must carry that prefix — the other is a plain host path.
    The container-name prefix is rewritten to the container id, since that's
    what `docker cp` (and `DockerClient.container_cp`) actually expects.
    Returns `None` (caller notifies) on blank input or if both/neither side
    is container-prefixed.
    """
    src, dst = src.strip(), dst.strip()
    if not src or not dst:
        return None

    prefix = f"{c.name}:"
    src_is_container = src.startswith(prefix)
    dst_is_container = dst.startswith(prefix)
    if src_is_container == dst_is_container:
        return None

    if src_is_container:
        src = f"{c.id}:{src[len(prefix) :]}"
    else:
        dst = f"{c.id}:{dst[len(prefix) :]}"
    return src, dst
