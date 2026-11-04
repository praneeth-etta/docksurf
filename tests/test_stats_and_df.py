"""Pure tests: stats and df parsing, uptime, event noise, SDK context, df bars."""

import os
import unittest
from unittest.mock import MagicMock, patch

from docksurf_py.core.docker import (
    DockerResourceFetcher,
    _parse_stats,
    _parse_system_df,
    format_uptime,
)
from docksurf_py.core.models import (
    ContainerTop,
    DiskUsageEntry,
    SystemDf,
)
from docksurf_py.core.render.observability import render_df, render_df_bars, render_top
from docksurf_py.core.render.palette import TIDE_PALETTE
from docksurf_py.core.state.refresh import is_noise_event

GB = 1024**3


def _df(*entries: DiskUsageEntry) -> SystemDf:
    return SystemDf(
        entries=list(entries),
        total_size=sum(e.size_bytes for e in entries),
        total_reclaimable=sum(e.reclaimable_bytes for e in entries),
    )


def _bars(df: SystemDf, width: int = 80) -> list[str]:
    from rich.console import Console

    console = Console(width=width, no_color=True, highlight=False)
    with console.capture() as cap:
        console.print(render_df_bars(df, TIDE_PALETTE, width))
    return [line.rstrip() for line in cap.get().splitlines()]


class ParseStatsTests(unittest.TestCase):
    def test_computes_cpu_mem_net_block(self) -> None:
        sample = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 2000, "percpu_usage": [1, 1]},
                "system_cpu_usage": 10000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 1000},
                "system_cpu_usage": 5000,
            },
            "memory_stats": {
                "usage": 200,
                "limit": 1000,
                "stats": {"inactive_file": 50},
            },
            "networks": {"eth0": {"rx_bytes": 100, "tx_bytes": 40}},
            "blkio_stats": {
                "io_service_bytes_recursive": [
                    {"op": "Read", "value": 10},
                    {"op": "Write", "value": 5},
                ]
            },
        }
        stats = _parse_stats(sample)
        # cpu_delta=1000, system_delta=5000, online=2 -> 1000/5000 * 2 * 100 = 40%
        self.assertAlmostEqual(stats.cpu_percent, 40.0)
        self.assertEqual(stats.mem_used, 150)  # 200 - 50 inactive_file
        self.assertAlmostEqual(stats.mem_percent, 15.0)
        self.assertEqual((stats.net_rx, stats.net_tx), (100, 40))
        self.assertEqual((stats.blk_read, stats.blk_write), (10, 5))

    def test_zero_system_delta_is_zero_cpu(self) -> None:
        stats = _parse_stats({"cpu_stats": {}, "precpu_stats": {}, "memory_stats": {}})
        self.assertEqual(stats.cpu_percent, 0.0)
        self.assertEqual(stats.mem_percent, 0.0)


class FormatUptimeTests(unittest.TestCase):
    def test_empty_and_zero_time_are_dash(self) -> None:
        self.assertEqual(format_uptime(""), "—")
        self.assertEqual(format_uptime("0001-01-01T00:00:00Z"), "—")

    def test_past_timestamp_has_no_ago_suffix(self) -> None:
        out = format_uptime("2000-01-01T00:00:00Z")
        self.assertNotEqual(out, "—")
        self.assertNotIn("ago", out)


class ParseSystemDfTests(unittest.TestCase):
    def test_totals_and_reclaimable(self) -> None:
        raw = {
            "Images": [
                {"Size": 100, "Containers": 1},
                {"Size": 200, "Containers": 0},
            ],
            "Containers": [
                {"SizeRw": 10, "State": "running"},
                {"SizeRw": 20, "State": "exited"},
            ],
            "Volumes": [
                {"UsageData": {"Size": 50, "RefCount": 1}},
                {"UsageData": {"Size": 70, "RefCount": 0}},
            ],
            "BuildCache": [
                {"Size": 30, "InUse": False},
                {"Size": 40, "InUse": True},
            ],
        }
        df = _parse_system_df(raw)
        by_kind = {e.kind: e for e in df.entries}
        self.assertEqual(by_kind["Images"].size_bytes, 300)
        self.assertEqual(by_kind["Images"].reclaimable_bytes, 200)
        self.assertEqual(by_kind["Images"].active_count, 1)
        self.assertEqual(by_kind["Containers"].reclaimable_bytes, 20)
        self.assertEqual(by_kind["Local Volumes"].reclaimable_bytes, 70)
        self.assertEqual(by_kind["Build Cache"].reclaimable_bytes, 30)
        self.assertEqual(df.total_size, 520)
        self.assertEqual(df.total_reclaimable, 320)


class GetContainersFieldsTests(unittest.TestCase):
    """`get_containers()` now builds every field from the `/containers/json`
    list summary alone (PATCH_WORK.md P-1) — health, exit code, and the table's
    Uptime hint are parsed from the summary's human `Status` string rather
    than a per-container inspect. `env`/`health_log`/precise `started_at`/
    `restart_count` are detail-pane-only and fetched lazily instead — see
    `ContainerDetailTests` in test_docker.py."""

    def test_maps_health_exit_code_and_uptime_hint_from_status_text(self) -> None:
        summary = {
            "Id": "abc123" + "0" * 58,
            "Names": ["/web"],
            "ImageID": "sha256:deadbeef",
            "Created": 1751328000,  # 2025-07-01T00:00:00Z
            "State": "running",
            "Status": "Up 5 minutes (unhealthy)",
            "Labels": {},
            "NetworkSettings": {"Networks": {}},
            "Mounts": [],
            "Ports": [],
        }
        sdk = MagicMock()
        sdk.api.containers.return_value = [summary]
        sdk.api.images.return_value = [
            {"Id": "sha256:deadbeef", "RepoTags": ["nginx:latest"]}
        ]

        containers = DockerResourceFetcher(sdk).get_containers()

        self.assertEqual(len(containers), 1)
        got = containers[0]
        self.assertEqual(got.name, "web")
        self.assertEqual(got.image_name, "nginx:latest")
        self.assertEqual(got.health, "unhealthy")
        self.assertEqual(got.exit_code, 0)
        self.assertEqual(got.uptime_hint, "5 minutes")
        sdk.api.inspect_container.assert_not_called()

    def test_exited_container_parses_exit_code_from_status_text(self) -> None:
        summary = {
            "Id": "b" * 64,
            "Names": ["/db"],
            "ImageID": "sha256:deadbeef",
            "Created": 0,
            "State": "exited",
            "Status": "Exited (137) 3 minutes ago",
            "Labels": {},
            "NetworkSettings": {"Networks": {}},
            "Mounts": [],
            "Ports": [],
        }
        sdk = MagicMock()
        sdk.api.containers.return_value = [summary]
        sdk.api.images.return_value = []

        got = DockerResourceFetcher(sdk).get_containers()[0]

        self.assertFalse(got.running)
        self.assertEqual(got.exit_code, 137)
        self.assertEqual(got.uptime_hint, "")
        sdk.api.inspect_container.assert_not_called()


class EventNoiseFilterTests(unittest.TestCase):
    """The noise filter must match the action *prefix* (Docker appends the
    healthcheck command, e.g. 'exec_create: /bin/sh -c echo ok')."""

    def test_exec_events_with_command_suffix_are_noise(self) -> None:
        self.assertTrue(is_noise_event("exec_create: /bin/sh -c echo ok"))
        self.assertTrue(is_noise_event("exec_start: /bin/sh -c echo ok"))
        self.assertTrue(is_noise_event("exec_die"))

    def test_periodic_health_status_is_noise(self) -> None:
        self.assertTrue(is_noise_event("health_status: healthy"))
        self.assertTrue(is_noise_event("health_status: unhealthy"))

    def test_real_state_changes_are_not_noise(self) -> None:
        for action in ("start", "die", "stop", "create", "destroy", "pull"):
            self.assertFalse(is_noise_event(action), action)


class CreateSdkClientContextTests(unittest.TestCase):
    """docker.from_env() ignores `docker context`; _create_sdk_client fixes that."""

    def test_docker_host_env_takes_precedence(self) -> None:
        from docksurf_py.core.docker import context as dockmod

        with (
            patch.dict(os.environ, {"DOCKER_HOST": "tcp://1.2.3.4:2375"}),
            patch.object(dockmod.docker, "from_env") as from_env,
        ):
            dockmod._create_sdk_client()
        from_env.assert_called_once()

    def test_non_default_context_uses_its_endpoint(self) -> None:
        from docksurf_py.core.docker import context as dockmod

        fake_ctx = MagicMock(Name="colima", Host="unix:///home/u/.colima/docker.sock")
        fake_ctx.TLSConfig = None
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "docker.context.ContextAPI.get_current_context", return_value=fake_ctx
            ),
            patch.object(dockmod.docker, "DockerClient") as dc,
            patch.object(dockmod.docker, "from_env") as from_env,
        ):
            dockmod._create_sdk_client()
        dc.assert_called_once()
        self.assertEqual(
            dc.call_args.kwargs["base_url"], "unix:///home/u/.colima/docker.sock"
        )
        from_env.assert_not_called()

    def test_default_context_falls_back_to_from_env(self) -> None:
        from docksurf_py.core.docker import context as dockmod

        fake_ctx = MagicMock(Name="default", Host=dockmod._DEFAULT_DOCKER_SOCK)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "docker.context.ContextAPI.get_current_context", return_value=fake_ctx
            ),
            patch.object(dockmod.docker, "from_env") as from_env,
        ):
            dockmod._create_sdk_client()
        from_env.assert_called_once()


class RenderSmokeTests(unittest.TestCase):
    def test_render_df_does_not_raise(self) -> None:
        df = SystemDf(
            entries=[DiskUsageEntry("Images", 1, 0, 100, 100)],
            total_size=100,
            total_reclaimable=100,
        )
        self.assertIsNotNone(render_df(df))

    def test_render_top_does_not_raise_and_uses_all_titles(self) -> None:
        top = ContainerTop(
            titles=["PID", "USER", "CMD"],
            processes=[["1", "root", "python app.py"], ["7", "root", "sh"]],
        )
        panel = render_top(top, "web")
        self.assertIsNotNone(panel)
        table = panel.renderable
        self.assertEqual([c.header for c in table.columns], ["PID", "USER", "CMD"])
        self.assertEqual(table.row_count, 2)

    def test_render_top_handles_no_processes(self) -> None:
        top = ContainerTop(titles=["PID", "CMD"], processes=[])
        self.assertIsNotNone(render_top(top, "web"))


class RenderDfBarsTests(unittest.TestCase):
    ENTRIES = (
        DiskUsageEntry("Containers", 4, 2, 1 * GB, 0),
        DiskUsageEntry("Local Volumes", 29, 8, 5 * GB, 4 * GB),
        DiskUsageEntry("Build Cache", 10, 0, 10 * GB, 8 * GB),
        DiskUsageEntry("Images", 58, 20, 20 * GB, 12 * GB),
    )

    def test_rows_in_the_designed_order_with_sizes_and_reclaimable(self) -> None:
        lines = _bars(_df(*self.ENTRIES))
        self.assertEqual(
            [line.split()[0] for line in lines],
            ["images", "build", "volumes", "containers"],
        )
        self.assertTrue(lines[0].endswith("20.00GB · 12.00GB free-able"))
        self.assertTrue(lines[3].endswith("1.00GB · 0B free-able"))

    def test_bars_share_one_scale(self) -> None:
        lines = _bars(_df(*self.ENTRIES))
        cells = [
            line.count("━") for line in lines
        ]  # images, cache, volumes, containers
        self.assertEqual(cells[0], max(cells))
        self.assertAlmostEqual(cells[1] / cells[0], 0.5, delta=0.05)
        self.assertAlmostEqual(cells[2] / cells[0], 0.25, delta=0.05)
        self.assertGreaterEqual(cells[3], 1)

    def test_rows_fit_the_width(self) -> None:
        for width in (40, 60, 80):
            for line in _bars(_df(*self.ENTRIES), width):
                self.assertLessEqual(len(line), width + 12, width)

    def test_missing_categories_are_skipped_and_unknown_ones_ignored(self) -> None:
        df = _df(
            DiskUsageEntry("Images", 1, 1, 100, 0), DiskUsageEntry("Other", 1, 1, 5, 0)
        )
        lines = _bars(df)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("images"))

    def test_a_zero_size_category_has_an_empty_bar(self) -> None:
        lines = _bars(
            _df(
                DiskUsageEntry("Images", 1, 1, 100, 0),
                DiskUsageEntry("Containers", 0, 0, 0, 0),
            )
        )
        self.assertEqual(lines[1].count("━"), 0)
        self.assertIn("0B · 0B free-able", lines[1])

    def test_reclaimable_part_uses_the_warning_colour(self) -> None:
        bar = render_df_bars(
            _df(DiskUsageEntry("Images", 1, 0, 100, 100)), TIDE_PALETTE, 60
        )
        from rich.console import Console

        console = Console(width=60, force_terminal=True, color_system="truecolor")
        with console.capture() as cap:
            console.print(bar)
        warning = TIDE_PALETTE.warning.lstrip("#")
        rgb = ";".join(str(int(warning[i : i + 2], 16)) for i in (0, 2, 4))
        self.assertIn(rgb, cap.get())

    def test_no_known_category_gives_a_message(self) -> None:
        self.assertEqual(
            _bars(_df(DiskUsageEntry("Other", 1, 1, 5, 0))), ["no disk usage data"]
        )
