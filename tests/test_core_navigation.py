"""Unit tests for core/state/navigation — which containers a resource relates
to. Plain pytest, no Textual."""

import unittest
from dataclasses import replace

from docksurf_py.core.models import (
    DockerSnapshot,
    Image,
    Network,
    NetworkEndpoint,
    Volume,
)
from docksurf_py.core.state.navigation import related_containers
from tests.test_compose import make_container


def _snapshot(*containers) -> DockerSnapshot:
    return DockerSnapshot(list(containers), [], [], [])


def _c(name: str, **changes):
    return replace(make_container(name), **changes)


class ImageRelationTests(unittest.TestCase):
    image = Image("sha256:a", "app", "latest", 1, False, [], "")

    def test_matches_by_image_id(self) -> None:
        snap = _snapshot(_c("web", image_id="sha256:a"), _c("db", image_id="sha256:b"))
        self.assertEqual(
            [c.name for c in related_containers(self.image, snap)], ["web"]
        )

    def test_matches_by_used_by_name(self) -> None:
        image = replace(self.image, used_by=["legacy"])
        snap = _snapshot(_c("legacy", image_id="other"), _c("db", image_id="sha256:b"))
        self.assertEqual([c.name for c in related_containers(image, snap)], ["legacy"])

    def test_a_container_matching_both_ways_is_listed_once(self) -> None:
        image = replace(self.image, used_by=["web"])
        snap = _snapshot(_c("web", image_id="sha256:a"))
        self.assertEqual(len(related_containers(image, snap)), 1)

    def test_stopped_containers_count_and_results_are_sorted_by_name(self) -> None:
        snap = _snapshot(
            _c("zeta", image_id="sha256:a"),
            _c("alpha", image_id="sha256:a", running=False, state="exited"),
        )
        self.assertEqual(
            [c.name for c in related_containers(self.image, snap)], ["alpha", "zeta"]
        )

    def test_unused_image_has_none(self) -> None:
        self.assertEqual(related_containers(self.image, _snapshot(_c("db"))), [])


class VolumeRelationTests(unittest.TestCase):
    volume = Volume("pgdata", "local", "/m", [], {})

    def test_matches_containers_mounting_it(self) -> None:
        snap = _snapshot(_c("db", mounts=["pgdata"]), _c("web", mounts=["other"]))
        self.assertEqual(
            [c.name for c in related_containers(self.volume, snap)], ["db"]
        )

    def test_matches_by_used_by_name(self) -> None:
        volume = replace(self.volume, used_by=["db"])
        self.assertEqual(
            [c.name for c in related_containers(volume, _snapshot(_c("db")))], ["db"]
        )

    def test_orphaned_volume_has_none(self) -> None:
        self.assertEqual(related_containers(self.volume, _snapshot(_c("db"))), [])


class NetworkRelationTests(unittest.TestCase):
    @staticmethod
    def _network(**kw) -> Network:
        base = dict(
            id="n",
            name="backend",
            driver="bridge",
            subnet="",
            gateway="",
            scope="local",
            used_by=[],
        )
        base.update(kw)
        return Network(**base)  # type: ignore[arg-type]

    def test_matches_attached_containers_by_endpoint_or_network_name(self) -> None:
        network = self._network(endpoints=[NetworkEndpoint("api")])
        snap = _snapshot(
            _c("api"),
            _c("worker", networks=["backend"]),
            _c("other", networks=["frontend"]),
        )
        self.assertEqual(
            [c.name for c in related_containers(network, snap)], ["api", "worker"]
        )

    def test_endpoint_without_a_listed_container_is_skipped(self) -> None:
        network = self._network(endpoints=[NetworkEndpoint("ghost")])
        self.assertEqual(related_containers(network, _snapshot(_c("api"))), [])


class OtherItemsTests(unittest.TestCase):
    def test_no_snapshot_or_unrelated_items_give_nothing(self) -> None:
        image = Image("sha256:a", "app", "latest", 1, False, [], "")
        self.assertEqual(related_containers(image, None), [])
        self.assertEqual(related_containers(_c("web"), _snapshot(_c("web"))), [])
        self.assertEqual(related_containers(object(), _snapshot(_c("web"))), [])
