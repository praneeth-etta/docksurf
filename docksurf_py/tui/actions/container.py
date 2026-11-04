"""ContainerActionHandler — stop/start/restart/exec/log actions for containers."""

import asyncio
import logging
import platform
import shutil
import subprocess
import webbrowser
from typing import Callable

from rich.markup import escape
from textual import work
from textual.widgets import DataTable, TabbedContent

from docksurf_py.core.models import (
    CommandErrorKind,
    CommandResult,
    ComposeProject,
    Container,
    ContainerDetail,
    PortBinding,
)
from docksurf_py.core.state.logs import write_log_export
from docksurf_py.core.state.ops import (
    EXEC_SHELL_CANDIDATES,
    BulkJob,
    already_running_reason,
    build_cp_paths,
    build_exec_argv,
    not_running_reason,
    pause_verb,
    select_exec_shell,
)
from docksurf_py.core.state.tables import row_key
from docksurf_py.core.types import LogOptions, TabID
from docksurf_py.tui.actions.common import _Base
from docksurf_py.tui.ids import (
    DETAIL_PANE_ID,
    DISK_PANEL_ID,
    ENDPOINTS_PANEL_ID,
    LAYERS_PANEL_ID,
    LIST_PANEL_ID,
    LIVE_PANEL_ID,
    LOG_PANE_ID,
    SAFE_DELETE_PANEL_ID,
    SIDE_COLUMN_ID,
    TOPOLOGY_PANEL_ID,
)
from docksurf_py.tui.widgets import (
    ContainerPickerScreen,
    DetailPane,
    LogOptionsScreen,
    LogPane,
    PromptField,
    PromptScreen,
)

logger = logging.getLogger(__name__)


def _is_wsl() -> bool:
    try:
        return "microsoft" in platform.uname().release.lower()
    except OSError:
        return False


def _open_in_browser(url: str) -> bool:
    """Best-effort browser launch — stdlib `webbrowser`, not a Docker call.

    `webbrowser.open()` can report success even when nothing actually opened
    (e.g. on Linux it shells out to `gio`/`xdg-open` and doesn't check their
    exit status), and WSL in particular has neither by default. On WSL, shell
    to `explorer.exe` (the standard way to reach the Windows host browser)
    first; fall back to `webbrowser.open()` everywhere else.
    """
    if _is_wsl() and shutil.which("explorer.exe"):
        try:
            # explorer.exe exits non-zero even on success when given a URL —
            # only a launch failure (e.g. missing binary) is a real error.
            subprocess.run(["explorer.exe", url], check=False)
            return True
        except OSError:
            return False
    return webbrowser.open(url)


class ContainerActionHandler(_Base):
    """Start, stop, restart, exec, and log actions scoped to containers."""

    _CONTAINER_TAB_HINT = "Switch to the Containers tab and select a container"

    def _sync_container_detail(self) -> None:
        """Lazily fetch or refresh the focused container's detail-only fields
        (env, health log, precise start time, restart count).

        Unlike `ImageActionHandler._sync_image_platform`, this always
        re-fetches because these fields can change while a container remains
        selected. `DetailCache.want_container_detail` only prevents duplicate
        in-flight fetches for the same container.
        """
        c = self._get_focused_container()
        target = c.id if c is not None else None

        if not self._details.want_container_detail(target):
            return
        assert target is not None
        self._fetch_container_detail(target)

    @work(thread=True)
    def _fetch_container_detail(self, container_id: str) -> None:
        detail = self.docker.container_detail(container_id)
        self.call_from_thread(self._apply_container_detail, container_id, detail)

    def _apply_container_detail(
        self, container_id: str, detail: ContainerDetail | None
    ) -> None:
        if not self._details.store_container_detail(container_id, detail):
            return  # nothing new to show
        if not self.is_running:
            return  # a fetch landing during teardown: the widgets are gone
        if self.query_one(TabbedContent).active != TabID.CONTAINERS:
            return
        # The cursor may have moved on while this was in flight; repainting the
        # now-focused row for someone else's detail is a wasted render.
        focused = self._get_focused_container()
        if focused is None or focused.id != container_id:
            return
        entry = self._resource_registry[TabID.CONTAINERS]
        table = self.query_one(f"#{entry.table_id}", DataTable)
        row = table.cursor_row
        if row is None:
            return
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        try:
            entry.show_details(pane, row)
        except IndexError:
            pass

    def _run_on_focused_container(
        self,
        command: Callable[[str], CommandResult],
        success_msg: Callable[[Container], str],
        guard: Callable[[Container], str | None] = lambda _: None,
    ) -> None:
        c = self._get_focused_container()
        if c is None:
            self.notify(self._CONTAINER_TAB_HINT, severity="warning")
            return

        if reason := guard(c):
            self.notify(reason, severity="information")
            return

        self._execute_container_action(c, command, success_msg)

    @work(thread=True)
    def _execute_container_action(
        self,
        c: Container,
        command: Callable[[str], CommandResult],
        success_msg: Callable[[Container], str],
    ) -> None:
        result = command(c.id)

        self.call_from_thread(
            self._handle_container_action_result,
            c,
            result,
            success_msg,
        )

    def _handle_container_action_result(
        self,
        c: Container,
        result: CommandResult,
        success_msg: Callable[[Container], str],
    ) -> None:
        if result.ok:
            msg = success_msg(c)
            logger.info("%s", msg)
            self.notify(msg)
            self.start_refresh()
        else:
            logger.warning("Container action failed on %s: %s", c.name, result.message)
            self.notify(f"Error: {result.message}", severity="error")
            if result.kind is CommandErrorKind.NOT_FOUND:
                # Our snapshot is stale — the container is already gone.
                self.start_refresh()

    def _marked_containers_pending(self) -> bool:
        """True only when there are marked containers AND the Containers tab
        is active. Without the tab guard, pressing a bulk verb while on
        another tab would silently act on marks left over from Containers."""
        return self._tables.has_marks(TabID.CONTAINERS) and (
            self.query_one(TabbedContent).active == TabID.CONTAINERS
        )

    def action_stop_container(self) -> None:
        # Marked containers win over the focused row — bulk stop the set.
        if self._marked_containers_pending():
            self._run_bulk_container_verb(
                verb="Stop",
                command=self.docker.stop_container,
                include=lambda c: c.running,
            )
            return
        # On compose project header -> action on the whole project;
        # on standalone container -> action on the single container.
        if self._focused_is_project_header():
            self.action_compose_stop()
            return
        self._run_on_focused_container(
            command=self.docker.stop_container,
            success_msg=lambda c: f"Stopped {escape(c.name)}",
            guard=not_running_reason,
        )

    def action_start_container(self) -> None:
        if self._marked_containers_pending():
            self._run_bulk_container_verb(
                verb="Start",
                command=self.docker.start_container,
                include=lambda c: not c.running,
            )
            return
        if self._focused_is_project_header():
            self.action_compose_start()
            return
        self._run_on_focused_container(
            command=self.docker.start_container,
            success_msg=lambda c: f"Started {escape(c.name)}",
            guard=already_running_reason,
        )

    def _run_bulk_container_verb(
        self,
        verb: str,
        command: Callable[[str], CommandResult],
        include: Callable[[Container], bool],
    ) -> None:
        """Build one bulk job per marked container that passes `include`,
        then hand off to `SelectionHandler._run_bulk`. Containers excluded
        by `include` (e.g. already stopped) are silently dropped from the
        batch — same tolerance as the single-container guard messages."""

        def bound(container_id: str) -> Callable[[], CommandResult]:
            return lambda: command(container_id)

        jobs: list[BulkJob] = []
        for c in self._marked_items(TabID.CONTAINERS):
            key = row_key(c)
            if include(c) and key is not None:
                jobs.append((key, c.name, bound(c.id)))

        if not jobs:
            self.notify(
                f"No marked containers eligible to {verb.lower()}", severity="warning"
            )
            self._tables.clear_marks(TabID.CONTAINERS)
            self._rerender_active_table()
            return
        self._run_bulk(TabID.CONTAINERS, verb, jobs)

    def action_restart_container(self) -> None:
        if self._marked_containers_pending():
            self._run_bulk_container_verb(
                verb="Restart",
                command=self.docker.restart_container,
                include=lambda c: True,
            )
            return
        if self._focused_is_project_header():
            self.action_compose_restart()
            return
        self._run_on_focused_container(
            command=self.docker.restart_container,
            success_msg=lambda c: f"Restarted {escape(c.name)}",
        )

    def action_pause_container(self) -> None:
        """Toggle pause: paused -> unpause, running -> pause.

        Container-only (no project-header handling, like exec) — pausing a
        whole Compose project isn't a `docker compose` verb.
        """
        c = self._get_focused_container()
        if c is None:
            self.notify(self._CONTAINER_TAB_HINT, severity="warning")
            return
        verb = pause_verb(c)
        if verb == "unpause":
            self._run_on_focused_container(
                command=self.docker.unpause_container,
                success_msg=lambda c: f"Resumed {escape(c.name)}",
            )
        elif verb == "pause":
            self._run_on_focused_container(
                command=self.docker.pause_container,
                success_msg=lambda c: f"Paused {escape(c.name)}",
            )
        else:
            self.notify(f"{escape(c.name)} is not running", severity="information")

    def action_kill_container(self) -> None:
        """SIGKILL the focused container — the escape hatch when `stop` hangs
        on its 10s timeout. No confirmation, matching `docker kill`."""
        self._run_on_focused_container(
            command=self.docker.kill_container,
            success_msg=lambda c: f"Killed {escape(c.name)}",
            guard=not_running_reason,
        )

    def _exec_preflight(self) -> Container | None:
        """Shared guards for `e`/`E`: a focused, running container and the
        `docker` CLI on PATH. Notifies and returns `None` on any failure."""
        c = self._get_focused_container()
        if c is None:
            self.notify(self._CONTAINER_TAB_HINT, severity="warning")
            return None
        if not c.running:
            self.notify(f"{escape(c.name)} is not running", severity="warning")
            return None
        if shutil.which("docker") is None:
            logger.error("Exec aborted — docker CLI not found on PATH")
            self.notify(
                "docker CLI not found on PATH — cannot open exec shell",
                severity="error",
            )
            return None
        return c

    def _run_interactive_exec(self, c: Container, argv: list[str]) -> None:
        """Suspend the TUI and run an interactive `docker exec` session."""
        logger.info("Exec argv=%s in container %s (%s)", argv, c.name, c.id)
        with self.suspend():
            result = subprocess.run(argv)

        if result.returncode != 0:
            logger.warning(
                "Exec session for %s exited with code %d", c.name, result.returncode
            )
            self.notify(
                f"Exec session for {escape(c.name)} exited with code "
                f"{result.returncode}",
                severity="warning",
            )

    @work
    async def action_exec_container(self) -> None:
        c = self._exec_preflight()
        if c is None:
            return

        shell = await asyncio.to_thread(
            select_exec_shell,
            EXEC_SHELL_CANDIDATES,
            lambda sh: self._container_has_shell(c.id, sh),
        )
        if shell is None:
            logger.warning("Exec aborted — no usable shell found in %s", c.id)
            self.notify(
                f"No usable shell ({'/'.join(EXEC_SHELL_CANDIDATES)}) found in "
                f"{escape(c.name)}",
                severity="error",
            )
            return

        self._run_interactive_exec(c, ["docker", "exec", "-it", c.id, shell])

    @work
    async def action_exec_custom(self) -> None:
        """`E` — prompt for a custom command and optional user before exec'ing.

        Pre-fills the command field with the same auto-detected shell `e`
        would use, so Enter-Enter reproduces `e`'s behavior.
        """
        c = self._exec_preflight()
        if c is None:
            return

        default_shell = await asyncio.to_thread(
            select_exec_shell,
            EXEC_SHELL_CANDIDATES,
            lambda sh: self._container_has_shell(c.id, sh),
        )
        values = await self.push_screen_wait(
            PromptScreen(
                f"Exec in {escape(c.name)}",
                [
                    PromptField("Command", value=default_shell or "sh"),
                    PromptField("User (uid or name, optional)"),
                ],
            )
        )
        if values is None:
            logger.debug("Custom exec cancelled by user")
            return

        command, user = values
        argv = build_exec_argv(c.id, command, user)
        if argv is None:
            self.notify("Enter a command to run", severity="warning")
            return
        self._run_interactive_exec(c, argv)

    @staticmethod
    def _container_has_shell(container_id: str, shell: str) -> bool:
        probe = subprocess.run(
            ["docker", "exec", container_id, "which", shell],
            capture_output=True,
        )
        return probe.returncode == 0

    @work
    async def action_copy_files(self) -> None:
        """`C` — `docker cp` in/out of a container via a path prompt.

        Unlike exec, `docker cp` works on a stopped container too, so there's
        no running guard here — just a focused container.
        """
        c = self._get_focused_container()
        if c is None:
            self.notify(self._CONTAINER_TAB_HINT, severity="warning")
            return

        values = await self.push_screen_wait(
            PromptScreen(
                f"Copy files — {escape(c.name)}",
                [
                    PromptField(
                        f"Source (prefix with '{c.name}:' for the container side)",
                        value=f"{c.name}:",
                    ),
                    PromptField(
                        f"Destination (prefix with '{c.name}:' for the container side)",
                        value=".",
                    ),
                ],
            )
        )
        if values is None:
            logger.debug("Copy files cancelled by user")
            return

        resolved = build_cp_paths(c, values[0], values[1])
        if resolved is None:
            self.notify(
                f"Prefix exactly one side with '{escape(c.name)}:' — the "
                "other is a plain host path",
                severity="warning",
            )
            return

        src, dst = resolved
        self.notify(f"Copying {escape(src)} → {escape(dst)}…")
        self._execute_copy(src, dst)

    @work(thread=True)
    def _execute_copy(self, src: str, dst: str) -> None:
        result = self.docker.container_cp(src, dst)
        self.call_from_thread(self._handle_copy_result, result)

    def _handle_copy_result(self, result: CommandResult) -> None:
        if result.ok:
            logger.info("%s", result.message)
            self.notify(result.message)
        else:
            logger.warning("Copy failed: %s", result.message)
            self.notify(f"Error: {result.message}", severity="error")

    # --- Side logs and full view -------------------------------------------------
    #
    # The log pane is a permanent panel in the side column on the Containers tab
    # (when `[ui] side_logs` is on) that follows the focused row. `l` expands it
    # to a full view (`ViewState.logs_full`); with `side_logs` off it is hidden
    # until then.

    def _logs_wanted(self) -> bool:
        """Whether the log panel should be showing on the current tab: always in
        full view, otherwise as the side panel unless `z` hid the side column."""
        if self.query_one(TabbedContent).active != TabID.CONTAINERS:
            return False
        if self._view.logs_full:
            return True
        return self.config.side_logs and self._view.side_visible

    def _sync_log_layout(self) -> None:
        """Show/hide the log panel and the panels around it for the current
        tab and full-view state. Leaving the Containers tab ends full view (the
        tab strip it hides is what you would switch with)."""
        if self.query_one(TabbedContent).active != TabID.CONTAINERS:
            self._view.logs_full = False
        full = self._view.logs_full
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        log_pane.display = self._logs_wanted()
        log_pane.set_expanded(full)
        self.query_one(f"#{SIDE_COLUMN_ID}").set_class(full, "logs-full")
        # The compact overlay replaces the list with the side column.
        self.query_one(f"#{LIST_PANEL_ID}").display = not (
            full or self._view.side_overlay
        )
        # `z` hides the whole side column, but full-view logs live in it.
        self.query_one(f"#{SIDE_COLUMN_ID}").display = full or self._view.side_visible
        self.query_one(f"#{DETAIL_PANE_ID}").display = not full
        active = self.query_one(TabbedContent).active
        self.query_one(f"#{LAYERS_PANEL_ID}").display = active == TabID.IMAGES
        self.query_one(f"#{DISK_PANEL_ID}").display = active == TabID.VOLUMES
        networks = active == TabID.NETWORKS and not full
        self.query_one(f"#{TOPOLOGY_PANEL_ID}").display = networks
        self.query_one(f"#{ENDPOINTS_PANEL_ID}").display = networks
        self.query_one(f"#{SIDE_COLUMN_ID}").set_class(networks, "-networks")
        if active != TabID.VOLUMES:  # on Volumes, the focused row decides
            self.query_one(f"#{SAFE_DELETE_PANEL_ID}").display = False
        # The live panel follows the stats target, except in full view.
        self.query_one(f"#{LIVE_PANEL_ID}").display = (
            not full and self._live.stats_target is not None
        )

    def _sync_side_logs(self) -> None:
        """Point the log panel at the focused row. Idempotent: it only reloads
        when the target changes (called from the debounced `_retarget_side`)."""
        item = None
        if self._logs_wanted():
            item = self._get_focused_resource(TabID.CONTAINERS)
        target = self._live.log_target_for(item)
        if not self._live.retarget_logs(target):
            return
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if isinstance(item, ComposeProject):
            self._load_project_logs(log_pane, item)
        elif isinstance(item, Container):
            logger.info("Following logs for container %s (%s)", item.name, item.id)
            log_pane.load(item.id, item.name, self.docker.stream_logs)
        else:
            log_pane.unload()

    def action_view_logs(self) -> None:
        """`l`: toggle the full-view logs."""
        if self._view.logs_full:
            self._exit_logs_full()
            return
        if self.query_one(TabbedContent).active != TabID.CONTAINERS or not isinstance(
            self._get_focused_resource(TabID.CONTAINERS), (Container, ComposeProject)
        ):
            self.notify(self._CONTAINER_TAB_HINT, severity="warning")
            return
        self._view.toggle_logs_full()
        self._sync_log_layout()
        self._sync_side_logs()  # with side logs off nothing is loaded yet
        self.query_one(f"#{LOG_PANE_ID}", LogPane).focus_view()
        self._repaint_chrome()

    def _exit_logs_full(self) -> None:
        """Leave full view (`l` again, or `Esc`) and hand focus back to the list."""
        if not self._view.logs_full:
            return
        self._view.toggle_logs_full()
        self._sync_log_layout()
        self._sync_side_logs()  # with side logs off this unloads the panel
        entry = self._resource_registry[TabID.CONTAINERS]
        self.query_one(f"#{entry.table_id}", DataTable).focus()
        self._repaint_chrome()

    def _open_container_logs(self, key: str, name: str) -> None:
        """Show one container's logs by `key` (what `stream_logs` is handed).

        The post-rebuild flow passes the Compose container *name*: the id has
        changed, but `docker logs <name>` resolves the recreated container. `name`
        is the label on the panel. With side logs off this opens full view.
        """
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        logger.info("Opening log pane for container %s (%s)", name, key)
        if not log_pane.display:
            self._view.logs_full = True
            self._sync_log_layout()
            self._repaint_chrome()
        self._live.retarget_logs(key)
        log_pane.load(key, name, self.docker.stream_logs)

    def _load_project_logs(self, log_pane: LogPane, project: ComposeProject) -> None:
        specs = [(c.compose_service or c.name, c.id) for c in project.containers]
        logger.info(
            "Following aggregated logs for project %s (%d containers)",
            project.name,
            len(specs),
        )
        log_pane.load(
            project.name,
            f"project: {project.name}",
            lambda _key, opts: self.docker.stream_project_logs(specs, opts),
        )

    def action_follow_logs(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.toggle_follow()

    def action_clear_logs(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.clear_log()

    def action_toggle_log_search(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.toggle_search()

    def action_toggle_timestamps(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.toggle_timestamps()

    def action_toggle_log_wrap(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.toggle_wrap()

    def action_next_match(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.jump(1)

    def action_prev_match(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.jump(-1)

    def action_log_top(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.jump_home()

    def action_log_bottom(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        log_pane.jump_end()

    def action_export_logs(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return
        try:
            path = write_log_export(log_pane.log_title, log_pane.export_text())
        except OSError as e:
            logger.warning("Log export failed: %s", e)
            self.notify(f"Export failed: {e}", severity="error")
            return
        logger.info("Exported logs to %s", path)
        self.notify(f"Logs exported to {path}")

    def action_log_options(self) -> None:
        log_pane = self.query_one(f"#{LOG_PANE_ID}", LogPane)
        if not log_pane.display:
            return

        def _apply(options: LogOptions | None) -> None:
            if options is not None:
                log_pane.set_options(options)

        self.push_screen(LogOptionsScreen(log_pane.options), _apply)

    @work
    async def action_open_port(self) -> None:
        c = self._get_focused_container()
        if c is None:
            self.notify(self._CONTAINER_TAB_HINT, severity="warning")
            return
        published = [p for p in c.ports if p.host_port]
        if not published:
            self.notify(f"{escape(c.name)} has no published ports", severity="warning")
            return
        if len(published) == 1:
            self._open_port_url(published[0])
            return
        # Keyed by index, not host_port — two entries can publish the same
        # host port (e.g. one TCP, one UDP), and OptionList requires unique
        # option ids.
        options = [
            (str(i), f"{p.host_port} → {p.container_port}")
            for i, p in enumerate(published)
        ]
        chosen = await self.push_screen_wait(
            ContainerPickerScreen(f"Open port — {escape(c.name)}", options)
        )
        if chosen is not None:
            self._open_port_url(published[int(chosen)])

    def _open_port_url(self, port: PortBinding) -> None:
        host = "localhost" if port.host_ip in ("", "0.0.0.0", "::") else port.host_ip
        url = f"http://{host}:{port.host_port}"
        if _open_in_browser(url):
            self.notify(f"Opening {url}")
        else:
            self.notify(
                f"Couldn't launch a browser — copy this URL: {url}",
                severity="warning",
                timeout=10,
            )

    def action_toggle_secrets(self) -> None:
        """`R` — reveal or re-mask secret-looking env var values."""
        self._details.toggle_reveal_secrets()
        if self.query_one(TabbedContent).active != TabID.CONTAINERS:
            return
        entry = self._resource_registry[TabID.CONTAINERS]
        table = self.query_one(f"#{entry.table_id}", DataTable)
        row = table.cursor_row
        if row is None:
            return
        pane = self.query_one(f"#{DETAIL_PANE_ID}", DetailPane)
        try:
            entry.show_details(pane, row)
        except IndexError:
            pass
