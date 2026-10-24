"""docker/ — All system-level Docker execution lives here.

Facade package: re-exports everything the single-file `docker.py` used to
expose, so callers keep using `from docksurf_py.core.docker import X` unchanged.
"""

from docksurf_py.core.docker.client import DockerClient
from docksurf_py.core.docker.fetcher import DockerResourceFetcher
from docksurf_py.core.docker.format import (
    _parse_system_df,
    format_env,
    format_labels,
    format_ports,
    format_relative_time,
    format_size,
    format_uptime,
)
from docksurf_py.core.docker.streams import (
    ComposeBuildStream,
    EventStream,
    LogStream,
    MergedLogStream,
    PullStream,
    StatsStream,
    _assign_service_colors,
    _parse_stats,
    _split_timestamp,
    _strip_ansi,
)

__all__ = [
    "DockerClient",
    "DockerResourceFetcher",
    "LogStream",
    "MergedLogStream",
    "StatsStream",
    "EventStream",
    "PullStream",
    "ComposeBuildStream",
    "format_relative_time",
    "format_uptime",
    "format_size",
    "format_ports",
    "format_labels",
    "format_env",
    "_split_timestamp",
    "_strip_ansi",
    "_assign_service_colors",
    "_parse_stats",
    "_parse_system_df",
]
