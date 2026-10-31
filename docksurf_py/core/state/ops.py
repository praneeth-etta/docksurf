"""Decisions for write actions: delete plans and refusals, bulk runs and their
summaries, container verb guards, and exec/cp argument building.

Nothing here notifies or prompts — callers show the messages and dialogs.
Messages are Rich markup with user-supplied names already escaped.
"""

import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping

from rich.markup import escape

from docksurf_py.core.models import (
    CommandResult,
    Container,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.render.volumes import orphan_summary, orphan_total_text
from docksurf_py.core.service import DockerService

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


@dataclass(frozen=True)
class Refusal:
    """Why a resource can't be deleted — shown as a warning, no dialog."""

    message: str


def plan_container_delete(docker: DockerService, c: Any) -> DeletePlan | Refusal | None:
    if not isinstance(c, Container):
        return None
    msg = (
        f"Force-remove RUNNING container '{escape(c.name)}'?"
        if c.running
        else f"Remove container '{escape(c.name)}'?"
    )
    return DeletePlan(
        confirm_message=msg,
        command=lambda force: docker.remove_container(c.id, force=force),
        success_message=f"Removed container: {escape(c.name)}",
        force_default=c.running,
    )


def plan_image_delete(docker: DockerService, img: Image) -> DeletePlan:
    in_use = bool(img.used_by)
    img_label = f"{escape(img.repository)}:{escape(img.tag)}"
    remove_ref = img.id if img.is_dangling else f"{img.repository}:{img.tag}"
    msg = (
        f"Force-remove IN-USE image '{img_label}'?"
        if in_use
        else f"Remove image '{img_label}'?"
    )
    if not img.is_dangling:
        msg += " Untags only — the image is removed once its last tag is gone."
    return DeletePlan(
        confirm_message=msg,
        command=lambda force: docker.remove_image(remove_ref, force=force),
        success_message=f"Removed image {img_label}",
        force_default=in_use,
    )


def plan_volume_delete(docker: DockerService, vol: Volume) -> DeletePlan | Refusal:
    if vol.used_by:
        # Docker's volume-remove `force` only suppresses a "not found"
        # error — it does not override an in-use guard, so there's no
        # honest "Force" checkbox to offer here. Name the blockers instead.
        blockers = ", ".join(escape(name) for name in vol.used_by)
        return Refusal(
            f"Volume '{escape(vol.name)}' is in use by {blockers} — stop them first"
        )
    return DeletePlan(
        confirm_message=f"Remove volume '{escape(vol.name)}'?",
        command=lambda force: docker.remove_volume(vol.name),
        success_message=f"Removed volume {escape(vol.name)}",
    )


@dataclass(frozen=True)
class OrphanPrunePlan:
    """Removing a set of orphaned volumes: one delete job per volume (built
    from `plan_volume_delete`) and the confirm text naming the count and size."""

    jobs: list["BulkJob"]
    confirm_message: str


def plan_orphan_prune(
    docker: DockerService, volumes: Iterable[Volume], sizes: Mapping[str, int]
) -> OrphanPrunePlan | Refusal:
    """Plan deleting exactly `volumes` (the orphans on screen), skipping any a
    container has meanwhile mounted. Used instead of `sdk.volumes.prune()`,
    which on Docker 23+ only removes anonymous volumes and so would leave the
    named orphans the user was told would go."""
    jobs: list[BulkJob] = []
    planned: list[Volume] = []
    for v in volumes:
        plan = plan_volume_delete(docker, v)
        if isinstance(plan, Refusal):
            continue
        # Same identity as `tables.row_key` (which imports this module).
        jobs.append((("volume", v.name), v.name, bind_delete_command(plan, False)))
        planned.append(v)
    if not jobs:
        return Refusal("No orphaned volumes to prune")
    total = orphan_total_text(orphan_summary(planned, sizes))
    size = f" ({total})" if total else ""
    return OrphanPrunePlan(
        jobs,
        f"Delete {len(jobs)} orphaned volume(s){size}? Their data is removed for good.",
    )


BUILTIN_NETWORKS = ("bridge", "host", "none")


def plan_network_delete(docker: DockerService, net: Network) -> DeletePlan | Refusal:
    if net.name in BUILTIN_NETWORKS:
        return Refusal(f"Cannot remove built-in network '{escape(net.name)}'")

    def _remove(force: bool) -> CommandResult:
        # The Docker API has no real "force remove" for networks — every
        # attached endpoint must be disconnected first.
        if force:
            for endpoint in net.endpoints:
                docker.disconnect_container(net.name, endpoint.container_name)
        return docker.remove_network(net.name)

    has_endpoints = bool(net.endpoints)
    msg = (
        f"Force-remove network '{escape(net.name)}'? This disconnects "
        f"{len(net.endpoints)} attached container(s) first."
        if has_endpoints
        else f"Remove network '{escape(net.name)}'?"
    )
    return DeletePlan(
        confirm_message=msg,
        command=_remove,
        success_message=f"Removed network {escape(net.name)}",
        force_default=True if has_endpoints else None,
    )


def bind_delete_command(plan: DeletePlan, force: bool) -> Callable[[], CommandResult]:
    """Bind a `DeletePlan.command` to a fixed `force`, for non-interactive
    (bulk) delete jobs that can't show a per-row checkbox."""
    return lambda: plan.command(force)


# --- Bulk runs ---------------------------------------------------------------

# (row key, display name, command) — one marked resource's action.
BulkJob = tuple[tuple[str, str], str, Callable[[], CommandResult]]


@dataclass
class BulkResult:
    total: int
    ok_count: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (name, message)
    executed_keys: set[tuple[str, str]] = field(default_factory=set)

    def summary(self, verb: str) -> tuple[str, bool]:
        """The notice for this run and whether it is an error. Lists at most
        three failures by name."""
        if not self.failures:
            return f"{verb} {self.ok_count}/{self.total}", False
        shown = ", ".join(f"{escape(n)} ({m})" for n, m in self.failures[:3])
        extra = len(self.failures) - 3
        more = f", +{extra} more" if extra > 0 else ""
        return f"{verb} {self.ok_count}/{self.total} — failed: {shown}{more}", True


def run_bulk(jobs: list[BulkJob]) -> BulkResult:
    """Run each job in order and tally the outcome.

    Sequential, not parallel — these are direct Docker API/CLI calls, and
    running them one at a time keeps error attribution unambiguous (which
    job failed) without a thread pool for what's normally a handful of items.
    Blocking: call from a worker thread.
    """
    result = BulkResult(total=len(jobs))
    for key, name, command in jobs:
        outcome = command()
        result.executed_keys.add(key)
        if outcome.ok:
            result.ok_count += 1
        else:
            result.failures.append((name, outcome.message))
    return result


# --- Container verb guards -----------------------------------------------------


def not_running_reason(c: Container) -> str | None:
    return f"{escape(c.name)} is not running" if not c.running else None


def already_running_reason(c: Container) -> str | None:
    return f"{escape(c.name)} is already running" if c.running else None


def pause_verb(c: Container) -> Literal["pause", "unpause"] | None:
    """Toggle target for `p`: paused -> unpause, running -> pause, else None."""
    if c.state == "paused":
        return "unpause"
    if c.running:
        return "pause"
    return None


def rebuild_skip_reason(c: Container, buildable: set[str] | None) -> str | None:
    """Why `B` won't rebuild `c`'s service, or None to go ahead. `buildable`
    None means it couldn't be determined — proceed and let the build itself
    surface any real failure."""
    if buildable is not None and c.compose_service not in buildable:
        return (
            f"No build defined for service '{escape(c.compose_service)}' — "
            "nothing to rebuild"
        )
    return None


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
