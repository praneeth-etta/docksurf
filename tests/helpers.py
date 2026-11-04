"""Shared test doubles: a scriptable `MockDockerService`, fake streams, `wait_until`."""

import asyncio
from typing import Callable

from docksurf_py.core.connection import ConnectionState, ConnectionStatus
from docksurf_py.core.docker import EventStream, StatsStream
from docksurf_py.core.models import (
    CommandResult,
    ContainerDetail,
    ContainerTop,
    ContextInfo,
    DockerSnapshot,
    ImageLayer,
    ImagePlatform,
    SystemDf,
)
from docksurf_py.core.types import LogLine

EMPTY_SNAPSHOT = DockerSnapshot([], [], [], [])

_CONNECTED_STATE = ConnectionState(
    status=ConnectionStatus.CONNECTED,
    message="Connected",
    hint="",
    context="default",
    host="unix:///var/run/docker.sock",
)


class FakeLogStream:
    """A finite `LogSource` yielding fixed `LogLine`s, for driving `LogPane`."""

    def __init__(self, lines: list[LogLine]) -> None:
        self._lines = list(lines)
        self._active = True

    def __iter__(self):
        for line in self._lines:
            if not self._active:
                break
            yield line

    def stop(self) -> None:
        self._active = False


class FakePullStream:
    """A finite pull stream yielding fixed progress dicts, for driving pull UI."""

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = list(chunks)
        self._active = True

    def __iter__(self):
        for chunk in self._chunks:
            if not self._active:
                break
            yield chunk

    def stop(self) -> None:
        self._active = False


class FakeComposeBuildStream:
    """A finite build stream yielding fixed lines then a fixed returncode."""

    def __init__(self, lines: list[str], returncode: int) -> None:
        self._lines = list(lines)
        self._returncode = returncode
        self.returncode: int | None = None
        self._active = True

    def __iter__(self):
        for line in self._lines:
            if not self._active:
                break
            yield line
        self.returncode = self._returncode

    def stop(self) -> None:
        self._active = False


class FakeEventStream:
    """A finite `EventStream`-shaped fake with a settable `.error`.

    Yields nothing and ends immediately; `.error` lets a test simulate the
    daemon dropping mid-stream (`start_event_listener` reads it to decide
    whether to call `mark_disconnected`).
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def __iter__(self):
        return iter(())

    def stop(self) -> None:
        pass


class MockDockerService:
    def __init__(self, fetch_fn: Callable[[], DockerSnapshot]) -> None:
        self._fetch_fn = fetch_fn
        self.connection = _CONNECTED_STATE
        # Tests can flip this (and `connection`) to simulate a daemon drop —
        # `ensure_connected()` below just returns whatever was set here.
        self._connected = True
        # Categories that failed on the last fetch_snapshot() call; tests can
        # override to exercise SnapshotManager's partial-failure notice.
        self.last_fetch_errors: list[str] = []
        # Recorded (method_name, *args) tuples for every write call — lets
        # bulk/prune tests assert who was actually invoked.
        self.calls: list[tuple] = []
        # Contexts returned by list_contexts(); tests can override.
        self.contexts: list[ContextInfo] = [
            ContextInfo(
                name="default", host="unix:///var/run/docker.sock", is_current=True
            )
        ]
        # Result returned by switch_context(); tests can override.
        self.switch_result: CommandResult = CommandResult.success("Switched")
        # Lines a log stream yields; tests can override before opening logs.
        self.log_lines: list[LogLine] = [
            LogLine(text="starting up", ts="2024-01-01T00:00:00Z"),
            LogLine(text="request handled", ts="2024-01-01T00:00:01Z"),
            LogLine(text="boom", ts="2024-01-01T00:00:02Z", stream="stderr"),
        ]
        # Progress dicts a pull stream yields; tests can override.
        self.pull_chunks: list[dict] = [
            {"status": "Pulling from library/alpine"},
            {"status": "Pull complete", "id": "abc123"},
            {"status": "Status: Downloaded newer image for alpine:latest"},
        ]
        # Per-volume sizes returned by volume_sizes(); tests can override.
        self.volume_size_map: dict[str, int] = {}
        # Layer history returned by image_history(); tests can override.
        self.history_layers: list[ImageLayer] = [
            ImageLayer(created_by="/bin/sh -c #(nop) CMD", size_bytes=0, created="0"),
            ImageLayer(
                created_by="/bin/sh -c apk add curl", size_bytes=1024, created="0"
            ),
        ]
        # Per-image platform returned by image_platform(); tests can override.
        # Keyed by image id, default linux/amd64 for anything unlisted.
        self.platform_map: dict[str, ImagePlatform] = {}
        # Per-container detail-pane-only fields returned by container_detail();
        # tests can override. Keyed by container id, default an "empty" detail
        # (no env, no health log, no start time, no restarts) for anything
        # unlisted.
        self.container_detail_map: dict[str, ContainerDetail] = {}
        # Rebuild controls: which services report a build section (None =
        # "couldn't determine"), and the lines/exit code a rebuild stream
        # yields. Tests can override before pressing `B`.
        self.buildable_services: set[str] | None = None
        self.rebuild_lines: list[str] = ["#1 building", "#2 exporting"]
        self.rebuild_returncode: int = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def ensure_connected(self) -> ConnectionState:
        self.calls.append(("ensure_connected",))
        return self.connection

    def mark_disconnected(self, exc: Exception) -> None:
        self.calls.append(("mark_disconnected", str(exc)))
        self._connected = False
        self.connection = ConnectionState(
            status=ConnectionStatus.DAEMON_UNAVAILABLE,
            message="Docker daemon is not running",
            hint="Start Docker Desktop, or run: sudo systemctl start docker",
            context=self.connection.context,
            host=self.connection.host,
        )

    def fetch_snapshot(self) -> DockerSnapshot:
        return self._fetch_fn()

    def list_contexts(self) -> list[ContextInfo]:
        self.calls.append(("list_contexts",))
        return self.contexts

    def switch_context(self, name: str) -> CommandResult:
        self.calls.append(("switch_context", name))
        return self.switch_result

    def stream_logs(self, container_id, options=None):
        self.calls.append(("stream_logs", container_id, options))
        return FakeLogStream(self.log_lines)

    def stream_project_logs(self, specs, options=None):
        self.calls.append(("stream_project_logs", tuple(specs), options))
        return FakeLogStream(self.log_lines)

    def stream_stats(self, container_id):
        return StatsStream(container_id, None)

    def stream_events(self):
        return EventStream(None)

    def stream_pull(self, repository: str, tag: str = "latest"):
        self.calls.append(("stream_pull", repository, tag))
        return FakePullStream(self.pull_chunks)

    def system_df(self) -> SystemDf:
        return SystemDf(entries=[], total_size=0, total_reclaimable=0)

    def image_history(self, image_id: str):
        self.calls.append(("image_history", image_id))
        return self.history_layers

    def image_platform(self, image_id: str) -> ImagePlatform | None:
        self.calls.append(("image_platform", image_id))
        return self.platform_map.get(image_id, ImagePlatform("amd64", "linux"))

    def container_detail(self, container_id: str) -> ContainerDetail | None:
        self.calls.append(("container_detail", container_id))
        return self.container_detail_map.get(
            container_id,
            ContainerDetail(env=[], health_log=[], started_at="", restart_count=0),
        )

    def volume_sizes(self) -> dict[str, int]:
        self.calls.append(("volume_sizes",))
        return self.volume_size_map

    def compose_action(
        self, project, verb, config_files="", working_dir=""
    ) -> CommandResult:
        self.calls.append(("compose_action", project, verb))
        return CommandResult.success()

    def compose_buildable_services(
        self, project, config_files="", working_dir=""
    ) -> set[str] | None:
        self.calls.append(("compose_buildable_services", project))
        return self.buildable_services

    def stream_compose_rebuild(self, project, service, config_files="", working_dir=""):
        self.calls.append(("stream_compose_rebuild", project, service))
        return FakeComposeBuildStream(self.rebuild_lines, self.rebuild_returncode)

    def stop_container(self, container_id: str) -> CommandResult:
        self.calls.append(("stop_container", container_id))
        return CommandResult.success()

    def start_container(self, container_id: str) -> CommandResult:
        self.calls.append(("start_container", container_id))
        return CommandResult.success()

    def restart_container(self, container_id: str) -> CommandResult:
        self.calls.append(("restart_container", container_id))
        return CommandResult.success()

    def remove_container(self, container_id: str, force: bool = False) -> CommandResult:
        self.calls.append(("remove_container", container_id, force))
        return CommandResult.success()

    def remove_image(self, image_id: str, force: bool = False) -> CommandResult:
        self.calls.append(("remove_image", image_id, force))
        return CommandResult.success()

    def remove_volume(self, volume_name: str) -> CommandResult:
        self.calls.append(("remove_volume", volume_name))
        return CommandResult.success()

    def remove_network(self, network_name: str) -> CommandResult:
        self.calls.append(("remove_network", network_name))
        return CommandResult.success()

    def tag_image(
        self, image_id: str, repository: str, tag: str = "latest"
    ) -> CommandResult:
        self.calls.append(("tag_image", image_id, repository, tag))
        return CommandResult.success(f"Tagged {repository}:{tag}")

    def create_volume(
        self,
        name: str,
        driver: str = "local",
        labels: dict[str, str] | None = None,
    ) -> CommandResult:
        self.calls.append(("create_volume", name, driver, labels or {}))
        return CommandResult.success(f"Created volume {name}")

    def create_network(
        self, name: str, driver: str = "bridge", subnet: str = ""
    ) -> CommandResult:
        self.calls.append(("create_network", name, driver, subnet))
        return CommandResult.success(f"Created network {name}")

    def connect_container(self, network_name: str, container_id: str) -> CommandResult:
        self.calls.append(("connect_container", network_name, container_id))
        return CommandResult.success("Connected")

    def disconnect_container(
        self, network_name: str, container_id: str, force: bool = True
    ) -> CommandResult:
        self.calls.append(("disconnect_container", network_name, container_id))
        return CommandResult.success("Disconnected")

    def pause_container(self, container_id: str) -> CommandResult:
        self.calls.append(("pause_container", container_id))
        return CommandResult.success()

    def unpause_container(self, container_id: str) -> CommandResult:
        self.calls.append(("unpause_container", container_id))
        return CommandResult.success()

    def kill_container(self, container_id: str) -> CommandResult:
        self.calls.append(("kill_container", container_id))
        return CommandResult.success()

    def prune_containers(self) -> CommandResult:
        self.calls.append(("prune_containers",))
        return CommandResult.success("Pruned 0 container(s) — reclaimed 0B")

    def prune_images(self) -> CommandResult:
        self.calls.append(("prune_images",))
        return CommandResult.success("Pruned 0 image(s) — reclaimed 0B")

    def prune_volumes(self) -> CommandResult:
        self.calls.append(("prune_volumes",))
        return CommandResult.success("Pruned 0 volume(s) — reclaimed 0B")

    def prune_networks(self) -> CommandResult:
        self.calls.append(("prune_networks",))
        return CommandResult.success("Pruned 0 network(s)")

    def prune_system(self) -> CommandResult:
        self.calls.append(("prune_system",))
        return CommandResult.success("System prune: 0 item(s) removed — reclaimed 0B")

    def inspect_resource(self, kind: str, ref: str) -> dict | None:
        self.calls.append(("inspect_resource", kind, ref))
        return {}

    def container_top(self, container_id: str) -> ContainerTop | None:
        self.calls.append(("container_top", container_id))
        return ContainerTop(titles=[], processes=[])

    def container_cp(self, src: str, dst: str) -> CommandResult:
        self.calls.append(("container_cp", src, dst))
        return CommandResult.success(f"Copied {src} → {dst}")


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
