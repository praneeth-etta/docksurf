"""Table state for the four resource tabs: which rows each table shows, and
the marks, sort, collapsed Compose projects and dirty flags behind them.

`TablesState` owns that state and the decisions over it (row building, mark
pruning, sort cycling); the Textual layer only turns rows into cells and
moves the cursor. Also row identity, Compose grouping and search matching.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from docksurf_py.core.models import (
    ComposeProject,
    Container,
    DockerSnapshot,
    Image,
    Network,
    Volume,
)
from docksurf_py.core.service import DockerService
from docksurf_py.core.state.ops import DeletePlan, Refusal
from docksurf_py.core.state.status import RunState, classify
from docksurf_py.core.types import TabID

RowKey = tuple[str, str]
SortState = tuple[str, bool]

# Sort a tab starts with when no previous session set one (and its kind has
# a sort key for the column): images largest first.
DEFAULT_SORT: dict[TabID, SortState] = {TabID.IMAGES: ("Size", True)}


def row_key(item: Any) -> RowKey | None:
    """Stable identity for a row-backing object, for selection restore."""
    if isinstance(item, Container):
        return ("container", item.id)
    if isinstance(item, ComposeProject):
        return ("project", item.name)
    if isinstance(item, Image):
        # `get_images()` emits one row per tag, all sharing `item.id` —
        # the tag must be part of the identity, or marking/selection
        # restore/delete on one tag row would silently affect every tag
        # row of the same image (BF-3).
        return ("image", f"{item.id}|{item.repository}:{item.tag}")
    if isinstance(item, Volume):
        return ("volume", item.name)
    if isinstance(item, Network):
        return ("network", item.name)
    return None


def group_by_project(
    containers: list[Container],
    key: Callable[[Container], Any] | None = None,
    reverse: bool = False,
) -> tuple[list[ComposeProject], list[Container]]:
    """Split containers into Compose projects (sorted) and standalone ones.

    Project ordering is always alphabetical (a header isn't a sortable data
    row). Services within a project are sorted by `key`/`reverse` when a
    column sort is active, else by service name (the original default). A
    project's config-file/working-dir come from its first service (they're
    identical across a project's containers).
    """
    grouped: dict[str, list[Container]] = defaultdict(list)
    standalone: list[Container] = []
    for c in containers:
        if c.is_compose:
            grouped[c.compose_project].append(c)
        else:
            standalone.append(c)

    projects: list[ComposeProject] = []
    for name in sorted(grouped):
        if key is not None:
            members = sorted(grouped[name], key=key, reverse=reverse)
        else:
            members = sorted(grouped[name], key=lambda c: c.compose_service)
        first = members[0]
        projects.append(
            ComposeProject(
                name=name,
                containers=members,
                config_files=first.compose_config_files,
                working_dir=first.compose_working_dir,
            )
        )
    return projects, standalone


def matches_container(c: Container, q: str) -> bool:
    return (
        q in c.name.lower()
        or q in c.image_name.lower()
        or q in c.status.lower()
        or q in c.id.lower()
        or q in c.compose_project.lower()
        or q in c.compose_service.lower()
    )


def matches_image(i: Image, q: str) -> bool:
    return q in (i.repository or "").lower() or q in (i.tag or "").lower()


def matches_volume(v: Volume, q: str) -> bool:
    return q in v.name.lower() or q in v.driver.lower() or q in v.mountpoint.lower()


def matches_network(n: Network, q: str) -> bool:
    return q in n.name.lower() or q in n.driver.lower() or q in n.subnet.lower()


ALL_CHIP = "all"


@dataclass(frozen=True)
class Chip:
    """One status filter on a tab (`running`, `stopped`, ...). `matches` takes
    the resource; `role` is the palette role it shows in when inactive."""

    key: str
    label: str
    matches: Callable[[Any], bool]
    role: str = "muted"


def _in_states(*states: RunState) -> Callable[[Container], bool]:
    return lambda c: classify(c) in states


CONTAINER_CHIPS: tuple[Chip, ...] = (
    Chip(
        "running",
        "running",
        _in_states(RunState.RUNNING, RunState.STARTING, RunState.PAUSED),
    ),
    Chip("stopped", "stopped", _in_states(RunState.STOPPED)),
    Chip("failed", "failed", _in_states(RunState.FAILED, RunState.UNHEALTHY), "error"),
)

IMAGE_CHIPS: tuple[Chip, ...] = (
    Chip("in_use", "in use", lambda i: bool(i.used_by), "success"),
    Chip("unused", "unused", lambda i: not i.used_by and not i.is_dangling),
    Chip("dangling", "dangling", lambda i: i.is_dangling, "warning"),
)


@dataclass(frozen=True)
class ResourceKind:
    """What core needs to know about one resource type to build its table rows.

    `snapshot_items` picks this type's list out of a snapshot; `matches` is the
    search predicate (given an already-lowercased query); `sort_keys` maps a
    sortable column name to its key function. `groups_by_project` turns on
    Compose project grouping (header rows + collapsible service rows).
    `plan_delete` decides how one item is deleted (or why it can't be);
    None means the type has no delete. `chips` are the tab's status filters;
    an implicit `all` chip (no filtering) comes first.
    """

    label: str  # singular, human-readable — used in "No {label} selected"
    snapshot_items: Callable[[DockerSnapshot], list]
    matches: Callable[[Any, str], bool]
    sort_keys: dict[str, Callable[[Any], Any]] = field(default_factory=dict)
    groups_by_project: bool = False
    plan_delete: Callable[[DockerService, Any], DeletePlan | Refusal | None] | None = (
        None
    )
    chips: tuple[Chip, ...] = ()


class TablesState:
    """Rows, marks, sort, collapse and dirty flags for every resource tab.

    The collections are exposed as plain, live containers (`rows`, `marked`,
    `sort_state`, `dirty`, `collapsed_projects`) so callers can read them
    directly; mutate through the methods so the rules stay in one place.
    """

    def __init__(
        self,
        kinds: Mapping[TabID, ResourceKind],
        sort_state: Mapping[TabID, SortState | None] | None = None,
    ) -> None:
        self.kinds = dict(kinds)
        # Row-backing list per tab: row index -> the object that row shows
        # (a `ComposeProject` header or a resource).
        self.rows: dict[TabID, list] = {tab: [] for tab in self.kinds}
        # Per-tab marked row keys (see `row_key`); survive refresh, search and
        # collapse, and are pruned when their resource disappears.
        self.marked: dict[TabID, set[RowKey]] = {tab: set() for tab in self.kinds}
        # Per-tab column sort: (column, reverse), or None for snapshot order.
        self.sort_state: dict[TabID, SortState | None] = {
            tab: (sort_state or {}).get(tab) or self._default_sort(tab)
            for tab in self.kinds
        }
        # Tabs whose table hasn't been rebuilt since the last snapshot.
        # Inactive tabs are caught up lazily when they become active.
        self.dirty: set[TabID] = set()
        # Compose project names whose service rows are hidden.
        self.collapsed_projects: set[str] = set()
        # Active status chip per tab (`ALL_CHIP` = no chip filter).
        self.active_chip: dict[TabID, str] = {tab: ALL_CHIP for tab in self.kinds}

    # --- Snapshot lifecycle --------------------------------------------------

    def apply_snapshot(self, snapshot: DockerSnapshot, active: TabID) -> None:
        """Drop marks on resources that no longer exist (checked against the
        full snapshot, not a filtered view) and flag every inactive tab dirty."""
        for tab, kind in self.kinds.items():
            live = {
                key
                for item in kind.snapshot_items(snapshot)
                if (key := row_key(item)) is not None
            }
            self.marked[tab] &= live
        for tab in self.kinds:
            if tab != active:
                self.dirty.add(tab)

    def is_dirty(self, tab: TabID) -> bool:
        return tab in self.dirty

    def mark_clean(self, tab: TabID) -> None:
        self.dirty.discard(tab)

    # --- Rows ----------------------------------------------------------------

    def build_rows(self, tab: TabID, snapshot: DockerSnapshot, query: str = "") -> list:
        """Filter by `query`, then by the active chip, apply the tab's sort,
        group/collapse Compose projects where the kind asks for it, and store
        the result as the tab's rows. Grouping runs last, so a project header
        only appears with its matching services. Doesn't touch the dirty flag
        (see `mark_clean`)."""
        kind = self.kinds[tab]
        items = self._queried(tab, snapshot, query)
        chip = self._active_chip(tab)
        if chip is not None:
            items = [item for item in items if chip.matches(item)]
        items = self._sorted(tab, items)
        rows = self._grouped(tab, items) if kind.groups_by_project else items
        self.rows[tab] = rows
        return rows

    def _queried(self, tab: TabID, snapshot: DockerSnapshot, query: str) -> list:
        kind = self.kinds[tab]
        items = kind.snapshot_items(snapshot)
        if query:
            q = query.lower()
            items = [item for item in items if kind.matches(item, q)]
        return items

    # --- Chips ---------------------------------------------------------------

    def _active_chip(self, tab: TabID) -> Chip | None:
        """The active chip, or None for `all` (or a key the kind no longer has)."""
        key = self.active_chip.get(tab, ALL_CHIP)
        return next((c for c in self.kinds[tab].chips if c.key == key), None)

    def chip_label(self, tab: TabID) -> str:
        """Display label of the active chip (`in use`); `all` when none."""
        chip = self._active_chip(tab)
        return chip.label if chip is not None else ALL_CHIP

    def chip_keys(self, tab: TabID) -> list[str]:
        """Chip keys in cycle order, `all` first."""
        return [ALL_CHIP, *(c.key for c in self.kinds[tab].chips)]

    def set_chip(self, tab: TabID, key: str) -> bool:
        """Activate chip `key`; returns False (state unchanged) if the tab has
        no such chip."""
        if key not in self.chip_keys(tab):
            return False
        self.active_chip[tab] = key
        return True

    def cycle_chip(self, tab: TabID) -> str:
        """Advance to the next chip, wrapping back to `all`. A tab without
        chips stays on `all`."""
        keys = self.chip_keys(tab)
        current = self.active_chip.get(tab, ALL_CHIP)
        nxt = (
            keys[(keys.index(current) + 1) % len(keys)] if current in keys else keys[0]
        )
        self.active_chip[tab] = nxt
        return nxt

    def chip_counts(
        self, tab: TabID, snapshot: DockerSnapshot, query: str = ""
    ) -> dict[str, int]:
        """Matches per chip (including `all`) after the text query but before
        the chip filter, so the numbers stay put as the chip changes."""
        items = self._queried(tab, snapshot, query)
        counts = {ALL_CHIP: len(items)}
        for chip in self.kinds[tab].chips:
            counts[chip.key] = sum(1 for item in items if chip.matches(item))
        return counts

    def index_of(self, tab: TabID, key: RowKey) -> int | None:
        for idx, item in enumerate(self.rows.get(tab, [])):
            if row_key(item) == key:
                return idx
        return None

    def _sort_spec(self, tab: TabID) -> tuple[Callable[[Any], Any], bool] | None:
        """The active sort's key function and direction, or None when the tab
        is unsorted or its sort column has no key."""
        state = self.sort_state.get(tab)
        if state is None:
            return None
        column, reverse = state
        key = self.kinds[tab].sort_keys.get(column)
        return None if key is None else (key, reverse)

    def _sorted(self, tab: TabID, items: list) -> list:
        spec = self._sort_spec(tab)
        if spec is None:
            return items
        key, reverse = spec
        return sorted(items, key=key, reverse=reverse)

    def _grouped(self, tab: TabID, containers: list[Container]) -> list:
        spec = self._sort_spec(tab)
        key, reverse = spec if spec is not None else (None, False)
        projects, standalone = group_by_project(containers, key=key, reverse=reverse)
        rows: list[ComposeProject | Container] = []
        for project in projects:
            rows.append(project)
            if project.name not in self.collapsed_projects:
                rows.extend(project.containers)
        rows.extend(standalone)
        return rows

    # --- Sort ----------------------------------------------------------------

    def _default_sort(self, tab: TabID) -> SortState | None:
        default = DEFAULT_SORT.get(tab)
        if default is not None and default[0] in self.kinds[tab].sort_keys:
            return default
        return None

    def sort(self, tab: TabID) -> SortState | None:
        return self.sort_state.get(tab)

    def cycle_sort(self, tab: TabID, column: str) -> SortState | None:
        """Sort by `column`; the same column again flips direction. Returns the
        new state, or None (state unchanged) if `column` isn't sortable."""
        if column not in self.kinds[tab].sort_keys:
            return None
        current = self.sort_state.get(tab)
        reverse = not current[1] if current and current[0] == column else False
        self.sort_state[tab] = (column, reverse)
        return self.sort_state[tab]

    # --- Marks ---------------------------------------------------------------

    def has_marks(self, tab: TabID) -> bool:
        return bool(self.marked.get(tab))

    def is_marked(self, tab: TabID, key: RowKey | None) -> bool:
        return key is not None and key in self.marked[tab]

    def toggle_mark(self, tab: TabID, key: RowKey) -> bool:
        """Flip `key`'s mark; returns whether it is now marked."""
        marked = self.marked[tab]
        if key in marked:
            marked.discard(key)
            return False
        marked.add(key)
        return True

    def mark(self, tab: TabID, keys: Iterable[RowKey]) -> None:
        self.marked[tab].update(keys)

    def unmark(self, tab: TabID, keys: Iterable[RowKey]) -> None:
        self.marked[tab].difference_update(keys)

    def clear_marks(self, tab: TabID) -> bool:
        """Clear `tab`'s marks; returns whether there were any."""
        had = bool(self.marked[tab])
        self.marked[tab].clear()
        return had

    def marked_items(self, tab: TabID, snapshot: DockerSnapshot | None) -> list:
        """Resolve `tab`'s marked keys back to live objects from `snapshot`."""
        keys = self.marked.get(tab)
        if snapshot is None or not keys:
            return []
        return [
            item
            for item in self.kinds[tab].snapshot_items(snapshot)
            if row_key(item) in keys
        ]

    # --- Compose collapse ----------------------------------------------------

    def is_collapsed(self, project: str) -> bool:
        return project in self.collapsed_projects

    def toggle_collapse(self, project: str) -> bool:
        """Flip `project`'s collapsed state; returns whether it is now collapsed."""
        if project in self.collapsed_projects:
            self.collapsed_projects.discard(project)
            return False
        self.collapsed_projects.add(project)
        return True
