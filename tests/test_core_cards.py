"""Unit tests for the detail-card builders (`core/render/cards.py`)."""

import unittest

from rich.console import Console, RenderableType

from docksurf_py.core.models import (
    ComposeProject,
    ContainerDetail,
    ContainerTop,
    HealthProbe,
    Image,
    ImagePlatform,
    PortBinding,
)
from docksurf_py.core.render.cards import (
    CardModel,
    container_card,
    image_card,
    project_card,
    section_rule,
)
from docksurf_py.core.render.palette import TIDE_PALETTE as P
from tests.test_compose import make_container


def _plain(renderable: RenderableType, width: int = 70) -> str:
    console = Console(width=width, no_color=True, highlight=False)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


def _detail(**kw) -> ContainerDetail:
    base = dict(
        env=["A=1", "DB_PASSWORD=x", "API_TOKEN=y"],
        health_log=[],
        started_at="",
        restart_count=0,
    )
    base.update(kw)
    return ContainerDetail(**base)  # type: ignore[arg-type]


def _sections(card: CardModel) -> dict:
    return {s.key: s for s in card.sections}


class SectionRuleTests(unittest.TestCase):
    def test_fills_the_width_it_is_rendered_at(self) -> None:
        for width in (20, 45):
            line = _plain(section_rule("NETWORK", P), width).rstrip("\n")
            self.assertTrue(line.startswith("NETWORK ─"))
            self.assertEqual(len(line), width)


class ContainerCardTests(unittest.TestCase):
    def test_title_subtitle_and_key(self) -> None:
        c = make_container("redis")
        card = container_card(c, None, P)
        self.assertIn("redis · container", card.title)
        self.assertEqual(card.subtitle, "i inspect")
        self.assertEqual(card.key, c.id)

    def test_pill_line_running_healthy(self) -> None:
        c = make_container("redis", health="healthy")
        c.status = "Up 3 hours (healthy)"
        text = _plain(container_card(c, _detail(restart_count=2), P).body)
        self.assertIn("RUNNING", text)
        self.assertIn("✓ healthy", text)
        self.assertIn("up 3h", text)
        self.assertIn("2 restarts", text)

    def test_pill_line_failed_shows_exit_code_and_age(self) -> None:
        c = make_container("job", running=False)
        c.exit_code = 137
        c.status = "Exited (137) 14 minutes ago"
        text = _plain(container_card(c, _detail(), P).body)
        self.assertIn("FAILED", text)
        self.assertIn("exited 137", text)
        self.assertIn("14m ago", text)

    def test_restart_count_is_singular_and_hidden_until_detail_lands(self) -> None:
        c = make_container("a")
        self.assertNotIn("restart", _plain(container_card(c, None, P).body))
        text = _plain(container_card(c, _detail(restart_count=1), P).body)
        self.assertIn("1 restart", text)
        self.assertNotIn("1 restarts", text)

    def test_identity_rows_for_compose_service(self) -> None:
        c = make_container("docker-redis-1", project="docker", service="redis")
        c.image_name = "redis:7-alpine"
        c.image_id = "sha256:5d3c8e1fabcdef"
        text = _plain(container_card(c, None, P).body)
        self.assertIn("docker › redis", text)
        self.assertIn("redis:7-alpine · sha256:5d3c8e1f…", text)
        self.assertIn("docker-redis-"[:12], text)
        self.assertIn("created", text)

    def test_standalone_has_no_project_row(self) -> None:
        text = _plain(container_card(make_container("solo"), None, P).body)
        self.assertNotIn("project", text)

    def test_ports_published_unpublished_and_none(self) -> None:
        c = make_container("a")
        c.ports = [
            PortBinding("6379/tcp", "0.0.0.0", "6379"),
            PortBinding("6379/tcp", "::", "6379"),
        ]
        text = _plain(container_card(c, None, P).body)
        self.assertIn("0.0.0.0:6379 → 6379/tcp", text)
        self.assertIn("O open", text)
        self.assertEqual(text.count("6379/tcp"), 1)  # IPv6 duplicate collapsed
        c.ports = [PortBinding("80/tcp")]
        text = _plain(container_card(c, None, P).body)
        self.assertIn("80/tcp (not published)", text)
        self.assertNotIn("O open", text)
        c.ports = []
        self.assertIn("none", _plain(container_card(c, None, P).body))

    def test_network_ips_come_from_detail(self) -> None:
        c = make_container("a")
        c.networks = ["app_default", "other"]
        detail = _detail(network_ips={"app_default": "172.18.0.7"})
        text = _plain(container_card(c, detail, P).body)
        self.assertIn("app_default · 172.18.0.7", text)
        self.assertIn("other", text)
        self.assertNotIn("other ·", text)

    def test_body_has_section_rules(self) -> None:
        text = _plain(container_card(make_container("a"), _detail(), P).body)
        self.assertIn("NETWORK ─", text)
        self.assertIn("MORE ─", text)

    def test_no_more_rule_without_sections(self) -> None:
        c = make_container("a", running=False)
        card = container_card(c, _detail(env=[]), P)
        self.assertEqual(card.sections, ())
        self.assertNotIn("MORE", _plain(card.body))

    def test_env_section_masks_and_counts(self) -> None:
        card = container_card(make_container("a"), _detail(), P)
        env = _sections(card)["env"]
        self.assertEqual(env.summary, "env · 3 vars · 2 masked")
        self.assertEqual(env.hint, "R reveal")
        assert isinstance(env.content, str)
        self.assertIn("DB_PASSWORD=••••••••", env.content)

    def test_env_section_revealed(self) -> None:
        card = container_card(make_container("a"), _detail(), P, reveal=True)
        env = _sections(card)["env"]
        self.assertEqual(env.summary, "env · 3 vars · revealed")
        self.assertEqual(env.hint, "R mask")
        assert isinstance(env.content, str)
        self.assertIn("DB_PASSWORD=x", env.content)

    def test_env_without_secrets_has_no_reveal_hint(self) -> None:
        card = container_card(make_container("a"), _detail(env=["A=1"]), P)
        env = _sections(card)["env"]
        self.assertEqual((env.summary, env.hint), ("env · 1 var", ""))

    def test_env_pending_until_detail_lands(self) -> None:
        env = _sections(container_card(make_container("a"), None, P))["env"]
        self.assertEqual(env.content, "…")

    def test_empty_env_has_no_section(self) -> None:
        card = container_card(make_container("a"), _detail(env=[]), P)
        self.assertNotIn("env", _sections(card))

    def test_health_section(self) -> None:
        probes = [HealthProbe("2026-07-02T09:05:00Z", 0, "ok")] * 4 + [
            HealthProbe("2026-07-02T09:06:00Z", 1, "bad")
        ]
        c = make_container("a", health="healthy")
        card = container_card(c, _detail(health_log=probes, health_interval_s=10), P)
        health = _sections(card)["health"]
        self.assertEqual(health.summary, "health · 4/5 ok · every 10s")
        assert isinstance(health.content, str)
        self.assertIn("exit 1", health.content)

    def test_health_section_absent_without_healthcheck(self) -> None:
        card = container_card(make_container("a"), _detail(), P)
        self.assertNotIn("health", _sections(card))

    def test_top_section_states(self) -> None:
        c = make_container("a")
        idle = _sections(container_card(c, _detail(), P))["top"]
        self.assertEqual((idle.summary, idle.hint), ("top", "t toggle"))
        loading = _sections(container_card(c, _detail(), P, top_loading=True))["top"]
        self.assertEqual(loading.content, "fetching processes…")
        top = ContainerTop(titles=["PID", "CMD"], processes=[["1", "redis"]])
        done = _sections(container_card(c, _detail(), P, top=top))["top"]
        self.assertEqual(done.summary, "top · 1 process")
        two = ContainerTop(titles=["PID"], processes=[["1"], ["2"]])
        self.assertEqual(
            _sections(container_card(c, _detail(), P, top=two))["top"].summary,
            "top · 2 processes",
        )
        self.assertIn("redis", _plain(done.content))  # type: ignore[arg-type]

    def test_no_top_section_for_stopped_container(self) -> None:
        card = container_card(make_container("a", running=False), _detail(), P)
        self.assertNotIn("top", _sections(card))

    def test_markup_in_names_is_escaped(self) -> None:
        c = make_container("[red]x")
        c.image_name = "[b]img"
        text = _plain(container_card(c, None, P).body)
        self.assertIn("[b]img", text)


class ProjectCardTests(unittest.TestCase):
    def _project(self) -> ComposeProject:
        a = make_container("a", project="shop", service="web")
        b = make_container("b", running=False, project="shop", service="db")
        b.exit_code = 1
        return ComposeProject(
            "shop", [a, b], "/srv/shop/compose.yml,/srv/shop/o.yml", "/srv/shop"
        )

    def test_pill_services_and_locations(self) -> None:
        card = project_card(self._project(), P)
        text = _plain(card.body)
        self.assertIn("1/2 UP", text)
        self.assertIn("1 failed", text)
        self.assertIn("db", text)
        self.assertIn("web", text)
        self.assertIn("/srv/shop", text)
        self.assertIn("/srv/shop/o.yml", text)
        self.assertIn("project logs", text)
        self.assertEqual(card.key, "project:shop")
        self.assertIn("shop · project", card.title)
        self.assertEqual(card.sections, ())

    def test_empty_project(self) -> None:
        card = project_card(ComposeProject("x", [], "", ""), P)
        self.assertIn("0/0 UP", _plain(card.body))


def _image(**kw) -> Image:
    base = dict(
        id="sha256:944fc84f56cfaaaaaaaa",
        repository="shop-app",
        tag="latest",
        size_bytes=2 * 1024**3,
        is_dangling=False,
        used_by=[],
        created="",
    )
    base.update(kw)
    return Image(**base)  # type: ignore[arg-type]


class ImageCardTests(unittest.TestCase):
    linux = ImagePlatform("amd64", "linux")

    def _card(self, image=None, platform=None, tags=("latest",), containers=()):
        return image_card(image or _image(), platform, list(tags), list(containers), P)

    def test_title_names_the_full_reference(self) -> None:
        card = self._card(_image(repository="ghcr.io/org/tool", tag="1.2"))
        self.assertIn("ghcr.io/org/tool:1.2 · image", card.title)

    def test_pills_and_facts_for_an_in_use_image(self) -> None:
        c = make_container("web")
        text = _plain(
            self._card(_image(used_by=["web"]), self.linux, containers=[c]).body
        )
        first = text.splitlines()[0]
        for part in ("IN USE", "amd64", "linux", "2.00GB", "created"):
            self.assertIn(part, first)

    def test_status_pill_per_state(self) -> None:
        self.assertIn("UNUSED", _plain(self._card().body))
        dangling = _image(repository="<none>", tag="<none>", is_dangling=True)
        self.assertIn("DANGLING", _plain(self._card(dangling).body))

    def test_platform_pending_shows_ellipses(self) -> None:
        first = _plain(self._card(platform=None).body).splitlines()[0]
        self.assertEqual(first.count("…"), 2)

    def test_id_and_every_tag_with_their_hints(self) -> None:
        text = _plain(self._card(tags=("latest", "1.27")).body)
        self.assertIn("sha256:944fc84f56cf", text)
        self.assertNotIn("sha256:944fc84f56cfa", text)  # 12 hex chars only
        self.assertIn("i inspect", text)
        self.assertIn("latest, 1.27", text)
        self.assertIn("y tag", text)

    def test_no_tags_for_a_dangling_image(self) -> None:
        self.assertIn("none", _plain(self._card(tags=()).body))

    def test_used_by_lists_containers_with_one_jump_hint(self) -> None:
        web = make_container("shop-web-1", project="shop", service="web")
        job = make_container("job", running=False)
        text = _plain(
            self._card(
                _image(used_by=["shop-web-1", "job"]), containers=[web, job]
            ).body
        )
        self.assertIn("USED BY", text)
        self.assertIn("shop-web-1", text)
        self.assertIn("shop › web · running", text)
        self.assertIn("job", text)
        self.assertEqual(text.count("⏎ jump"), 1)
        self.assertLess(text.index("⏎ jump"), text.index("job"))

    def test_used_by_is_empty_for_an_unused_image(self) -> None:
        text = _plain(self._card().body)
        self.assertIn("USED BY", text)
        self.assertNotIn("⏎ jump", text)

    def test_card_has_no_sections_and_a_stable_key(self) -> None:
        card = self._card()
        self.assertEqual(card.sections, ())
        self.assertEqual(card.key, self._card().key)
        other = self._card(_image(tag="1.0"))
        self.assertNotEqual(card.key, other.key)  # per tag row
