# docksurf

A keyboard-driven terminal UI for visualising and managing Docker resources like containers, images, volumes, and networks. Compose-aware and live: it reacts to Docker events on its own and streams real time resource usage, so you're observing, not polling. No GUI, no browser tab.


![docksurf's container tab](https://raw.githubusercontent.com/praneeth-etta/docksurf/main/docksurf-container-tab.png)

**Docs:** [Quickstart](https://github.com/praneeth-etta/docksurf/blob/main/QUICKSTART.md) · [Full keybindings reference](https://github.com/praneeth-etta/docksurf/blob/main/KEYBINDINGS.md) · [Changelog](https://github.com/praneeth-etta/docksurf/blob/main/CHANGELOG.md)

## Highlights

- **Live by default** — the tables auto-refresh on `docker events` (container start/stop/die, image pull/delete, …); `r` is a manual reload.
- **Local or remote** — honours your active `docker context` on startup: local, Docker Desktop, Colima, or a remote host over SSH/TCP.
- **Docker Compose aware** — containers grouped by project into a collapsible tree, with project-wide up/down/stop/start/restart, colour-coded per-service logs, and `B` to rebuild + recreate a single service in place.
- **Live resource stats** — CPU %, memory, network and block I/O streamed into the detail pane for the selected running container.
- **Full lifecycle control** — pause/unpause and kill sit alongside stop/start/restart, so a `stop` that hangs on its 10s timeout never needs the CLI.
- **Multi-select + bulk actions** — mark rows on any tab and stop/start/remove them as a batch; ideal for cleaning up after a test run.
- **Inspect & prune** — the full raw `docker inspect` JSON for any resource in a searchable modal, plus a one-key menu to prune stopped containers, dangling images, unused volumes/networks, or everything at once.
- **Full image/volume/network CRUD** — pull, tag, and layer-history for images; create + size-on-disk for volumes; create + connect/disconnect for networks (see [Tabs](#tabs)).
- **Power-user exec & copy** — a custom exec command with a chosen user, `docker cp` in/out of a container, and an on-demand `docker top` process snapshot, all via quick prompts.
- **Full control log viewer** — live follow, in-log search, timestamps, configurable tail/`--since`, and mouse drag-to-select text to copy (`Ctrl+C`).
- **Operational signals at a glance** — colour-coded health, uptime and restart count, plus recent health-check probe output in the detail pane.
- **In-app context switching** — list and switch Docker contexts from inside the TUI (`D`), remembered across restarts.
- **Auto-reconnect** — if the daemon goes down mid-session, DockSurf reconnects and refreshes on its own the moment it's back.

## Requirements

- A reachable Docker daemon (local, or a remote one via `docker context`)
- The `docker` CLI on `PATH` only needed for exec-shell (`e`/`E`), Compose project actions (`ctrl+u`/`ctrl+k`), and file copy (`C`); everything else uses the SDK. DockSurf degrades gracefully if it's absent.

## Install

```bash
pip install docksurf
docksurf
```

Or without installing: `uvx docksurf`. Standalone binaries (no Python required) are on the [latest release](https://github.com/praneeth-etta/docksurf/releases/latest) releases are published via a tagged GitHub Actions workflow using PyPI Trusted Publishing (OIDC, no stored credentials).

Full install options (pip/uvx/binary/source) and a first run in under 2 minutes: see [QUICKSTART.md](https://github.com/praneeth-etta/docksurf/blob/main/QUICKSTART.md).

## Usage

Launch `docksurf` and you land on the Containers tab, cursor on the first row.

- **Move around** — `↑`/`↓` between rows, `Tab` to switch panels, `1`-`4` (or `[`/`]`) to jump tabs.
- **Act on a container** — `s`/`S`/`x` to stop/start/restart, `p` to pause, `K` to kill, `e` to exec a shell in, `l` to tail its logs live.
- **Find something** — `/` to search/filter the active tab.
- **Do it in bulk** — `space` to mark rows across the tab, then `d` to stop/remove them together.
- **Dig deeper** — `i` for the raw `docker inspect` JSON, `Y` to copy a resource's details to the clipboard.
- **Everything else** — `?` opens the in-app help screen with every binding, scoped to whatever you're focused on.

| Key            | Action                                                    |
| -------------- | --------------------------------------------------------- |
| `?`            | Help screen with every keybinding, in-app                 |
| `r`            | Refresh all Docker data                                   |
| `/`            | Search / filter the active tab                            |
| `↑`/`↓`, `Tab` | Navigate rows / switch tabs                               |
| `1`-`4`        | Jump directly to Containers / Images / Volumes / Networks |
| `s`/`S`/`x`    | Stop / start / restart (Containers tab)                   |
| `e`            | Exec shell into the focused container                     |
| `l`            | Toggle log viewer                                         |
| `space`        | Mark for a bulk action                                    |
| `d`            | Delete the selected (or marked) resource                  |
| `i`            | Inspect (raw `docker inspect` JSON)                       |
| `P`            | Prune menu                                                |
| `D`            | Switch Docker context                                     |
| `q`            | Quit                                                      |

Full per-tab reference (log pane keys, Compose header behaviour, everything else): [KEYBINDINGS.md](https://github.com/praneeth-etta/docksurf/blob/main/KEYBINDINGS.md).

## Tabs

Every tab has a leading mark column (`space` to toggle) for multi-select + bulk actions — see [Usage](#usage).

**Containers** — all containers (running and stopped), **grouped by Compose project** into a collapsible tree, with standalone containers below. Columns: name (with a colour-coded status dot) and image. The detail pane adds status, health, uptime, restart count, ports, networks, env vars, health-probe history, live CPU/mem/net/block-IO stats, and an on-demand `docker top` snapshot (`t`). For a Compose service, `B` rebuilds and recreates just that container, streamed live.

**Images** — all images, tagged as *In Use*, *Unused*, or *Dangling*. Detail pane shows size, created date, architecture, and which containers reference the image. Pull new images with live progress (`+`), view per-layer history (`h`), retag (`y`), and one-key mark-all-dangling for bulk cleanup (`a`).

**Volumes** — all volumes, tagged as *In Use* or *Orphaned*. Detail pane shows mountpoint, driver, labels, and attached containers. Create volumes (`+`) and pull on-demand per-volume size on disk (`b`).

**Networks** — all networks with driver and scope. Detail pane shows driver, scope, subnet, gateway, and each attached container's IP/MAC within the network. Create networks (`+`) and connect/disconnect containers (`v`/`m`).

## How it works

`docker/` owns all Docker I/O through the [Docker SDK for Python](https://docker-py.readthedocs.io/), with three narrow subprocess exceptions where the SDK has no equivalent: interactive exec-shell (needs a real TTY), Compose project actions (docker-py has no Compose support), and `docker cp` (its directory/trailing-slash semantics aren't worth reproducing over raw tar archives). `widgets/` is pure presentation with no Docker knowledge of its own. Resource lists are fetched in parallel and kept live via `docker events` (debounced, selection-preserving) rather than polling; stats and logs stream straight from the SDK, with a Compose project's logs merged and colour-coded per service.

DockSurf also honours whatever `docker context` you're on — matching the `docker` CLI's own precedence (`DOCKER_HOST` → active context → default socket) — so it works against a local daemon, Docker Desktop, Colima, or a remote host over SSH/TCP with no extra config. Switching contexts in-app (`D`) never touches `~/.docker/config.json` — DockSurf just opens its own connection elsewhere — and the choice is remembered across restarts. If the daemon goes down mid-session, DockSurf retries on its own and reconnects the moment it's back.

## Logging

App logs go to `~/.local/share/docksurf/docksurf.log`, never stdout (which belongs to the TUI), useful when debugging refresh errors, failed Docker API calls, or connection issues.
