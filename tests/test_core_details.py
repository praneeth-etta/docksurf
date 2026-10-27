"""Unit tests for core/state/details — the detail-pane cache and which
container the stats stream / process list follow. Plain pytest, no Textual."""

import unittest

from docksurf_py.core.models import ComposeProject, ContainerDetail, ContainerTop
from docksurf_py.core.state.details import DetailCache, LiveTargets
from tests.test_compose import make_container


def _detail() -> ContainerDetail:
    return ContainerDetail(env=["A=1"], health_log=[], started_at="", restart_count=0)


class ContainerDetailStoreTests(unittest.TestCase):
    def test_store_reports_whether_the_detail_changed(self) -> None:
        cache = DetailCache()
        self.assertTrue(cache.store_container_detail("c1", _detail()))  # first
        self.assertFalse(cache.store_container_detail("c1", _detail()))  # same
        changed = ContainerDetail(
            env=["B=2"], health_log=[], started_at="", restart_count=0
        )
        self.assertTrue(cache.store_container_detail("c1", changed))
        self.assertFalse(cache.store_container_detail("c1", None))  # failed fetch
        self.assertEqual(cache.container_detail("c1"), changed)


class DetailCacheTests(unittest.TestCase):
    def test_architecture_fetched_once_and_deduped_in_flight(self) -> None:
        cache = DetailCache()
        self.assertEqual(cache.architecture("img"), "…")
        self.assertTrue(cache.want_architecture("img"))
        self.assertFalse(cache.want_architecture("img"))  # in flight
        cache.store_architecture("img", None)
        self.assertEqual(cache.architecture("img"), "unknown")
        self.assertFalse(cache.want_architecture("img"))  # cached
        self.assertFalse(cache.want_architecture(None))

    def test_container_detail_refetches_but_dedupes_in_flight(self) -> None:
        cache = DetailCache()
        self.assertTrue(cache.want_container_detail("c1"))
        self.assertFalse(cache.want_container_detail("c1"))
        cache.store_container_detail("c1", _detail())
        self.assertEqual(cache.container_detail("c1"), _detail())
        # Unlike architecture, a stored detail doesn't stop a refetch.
        self.assertTrue(cache.want_container_detail("c1"))

    def test_failed_detail_fetch_keeps_last_known_value(self) -> None:
        cache = DetailCache()
        cache.want_container_detail("c1")
        cache.store_container_detail("c1", _detail())
        cache.want_container_detail("c1")
        cache.store_container_detail("c1", None)
        self.assertEqual(cache.container_detail("c1"), _detail())

    def test_volume_sizes_replace_wholesale(self) -> None:
        cache = DetailCache()
        cache.set_volume_sizes({"a": 1})
        cache.set_volume_sizes({"b": 2})
        self.assertIsNone(cache.volume_size("a"))
        self.assertEqual(cache.volume_size("b"), 2)

    def test_toggle_reveal_secrets(self) -> None:
        cache = DetailCache()
        self.assertTrue(cache.toggle_reveal_secrets())
        self.assertFalse(cache.toggle_reveal_secrets())


class LiveTargetsTests(unittest.TestCase):
    def test_stats_follow_running_containers_only(self) -> None:
        self.assertEqual(LiveTargets.stats_target_for(make_container("web")), "web")
        self.assertIsNone(
            LiveTargets.stats_target_for(make_container("web", running=False))
        )
        header = ComposeProject(
            name="p", containers=[], config_files="", working_dir=""
        )
        self.assertIsNone(LiveTargets.stats_target_for(header))
        self.assertIsNone(LiveTargets.stats_target_for(None))

    def test_retarget_only_on_change(self) -> None:
        live = LiveTargets()
        self.assertTrue(live.retarget_stats("web"))
        self.assertFalse(live.retarget_stats("web"))
        self.assertTrue(live.retarget_stats(None))

    def test_generation_invalidates_older_streams(self) -> None:
        live = LiveTargets()
        first = live.next_generation()
        self.assertTrue(live.is_current(first))
        live.next_generation()
        self.assertFalse(live.is_current(first))

    def test_top_result_is_kept_for_its_container_only(self) -> None:
        live = LiveTargets()
        top = ContainerTop(titles=["PID"], processes=[["1"]])
        self.assertFalse(live.store_top("web", top))  # nothing asked for it
        live.toggle_top("web")
        self.assertIsNone(live.top_for("web"))  # still fetching
        self.assertTrue(live.store_top("web", top))
        self.assertIs(live.top_for("web"), top)
        self.assertIsNone(live.top_for("db"))
        live.drop_stale_top("db")
        self.assertIsNone(live.top_result)
        live.toggle_top("web")
        self.assertIsNone(live.top_result)  # a new fetch starts clean
        live.store_top("web", top)
        live.toggle_top("web")  # hide
        self.assertIsNone(live.top_for("web"))

    def test_top_toggle_and_staleness(self) -> None:
        live = LiveTargets()
        self.assertTrue(live.toggle_top("web"))
        self.assertTrue(live.accepts_top_result("web"))
        self.assertFalse(live.drop_stale_top("web"))
        self.assertTrue(live.drop_stale_top("db"))
        self.assertFalse(live.accepts_top_result("web"))
        self.assertTrue(live.toggle_top("web"))
        self.assertFalse(live.toggle_top("web"))  # second `t` hides
        self.assertIsNone(live.top_target)
