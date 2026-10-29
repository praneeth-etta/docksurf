"""Unit tests for core/render — table/detail markup helpers and log-line
highlighting. Plain pytest, no Textual app."""

import unittest
from datetime import datetime, timedelta, timezone

from rich.text import Text

from docksurf_py.core.models import (
    ComposeProject,
    DiskUsageEntry,
    HealthProbe,
    Image,
    ImageLayer,
    SystemDf,
)
from docksurf_py.core.render.images import (
    SIZE_WIDTH,
    RepoParts,
    clean_layer_command,
    compact_relative,
    image_row_cells,
    image_status,
    image_tags,
    images_footer,
    layers_summary,
    layers_title,
    marked_size,
    marked_summary,
    repository_cell,
    split_repository,
    status_rank,
)
from docksurf_py.core.render.logs import highlight_match
from docksurf_py.core.render.markup import (
    format_health_log,
    health_markup,
    image_status_markup,
    project_status_markup,
    status_color,
    status_markup,
    truncate,
)
from docksurf_py.core.render.observability import (
    images_disk_line,
    images_disk_usage,
    split_bar,
)
from docksurf_py.core.render.palette import TIDE_PALETTE
from docksurf_py.core.types import STATUS_GREEN, STATUS_RED, STATUS_YELLOW, SafeMarkup
from tests.test_compose import make_container


class TruncateTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(truncate("nginx", 10), "nginx")
        self.assertEqual(truncate("exactly10!", 10), "exactly10!")

    def test_tail_truncation_keeps_limit(self) -> None:
        out = truncate("a-very-long-repository-name", 10)
        self.assertEqual(out, "a-very-lo…")
        self.assertEqual(len(out), 10)

    def test_middle_truncation_keeps_both_ends(self) -> None:
        out = truncate("project_postgres_data_volume", 11, middle=True)
        self.assertEqual(out, "proje…olume")
        self.assertEqual(len(out), 11)


class StatusMarkupTests(unittest.TestCase):
    def test_color_follows_state(self) -> None:
        self.assertEqual(status_color(make_container("a")), STATUS_GREEN)
        self.assertEqual(status_color(make_container("b", running=False)), STATUS_RED)
        paused = make_container("c")
        paused.running, paused.state = False, "paused"
        self.assertEqual(status_color(paused), STATUS_YELLOW)

    def test_running_health_glyphs(self) -> None:
        self.assertIn("✓", status_markup(make_container("a", health="healthy")))
        self.assertIn("✗", status_markup(make_container("a", health="unhealthy")))
        self.assertIn("…", status_markup(make_container("a", health="starting")))

    def test_nonzero_exit_code_is_shown(self) -> None:
        c = make_container("a", running=False)
        c.exit_code = 137
        self.assertIn("(137)", status_markup(c))

    def test_status_text_is_escaped(self) -> None:
        c = make_container("a")
        c.status = "Up [bold]"
        self.assertIn("\\[bold]", status_markup(c))
        self.assertIsInstance(status_markup(c), SafeMarkup)

    def test_health_markup_dash_when_no_healthcheck(self) -> None:
        self.assertEqual(health_markup(make_container("a")), "[dim]—[/]")


class FormatHealthLogTests(unittest.TestCase):
    def test_empty_log_is_none(self) -> None:
        self.assertIsNone(format_health_log([]))

    def test_keeps_only_most_recent_probes(self) -> None:
        log = [HealthProbe(start="", exit_code=i, output=f"p{i}") for i in range(7)]
        out = format_health_log(log, limit=2)
        assert out is not None
        self.assertNotIn("p4", out)
        self.assertIn("p5", out)
        self.assertIn("p6", out)

    def test_pass_fail_markers_and_empty_output(self) -> None:
        log = [
            HealthProbe(start="", exit_code=0, output=""),
            HealthProbe(start="", exit_code=1, output="boom"),
        ]
        out = format_health_log(log)
        assert out is not None
        self.assertIn("[✓ pass]  —\n(no output)", out)
        self.assertIn("[✗ exit 1]  —\nboom", out)


class ProjectAndImageMarkupTests(unittest.TestCase):
    def test_project_summary_counts_running(self) -> None:
        project = ComposeProject(
            name="shop",
            containers=[make_container("a"), make_container("b", running=False)],
            config_files="",
            working_dir="",
        )
        self.assertEqual(
            project_status_markup(project), f"[{STATUS_YELLOW}]1/2 running[/]"
        )

    def test_image_in_use_vs_unused(self) -> None:
        self.assertIn("In Use", image_status_markup(["web"]))
        self.assertIn("Unused", image_status_markup([]))


class HighlightMatchTests(unittest.TestCase):
    def test_highlights_every_case_insensitive_occurrence(self) -> None:
        out = highlight_match("Error: error", "error")
        self.assertEqual(out.count("[bold yellow]"), 2)

    def test_no_term_just_escapes(self) -> None:
        self.assertEqual(highlight_match("[x]", ""), "\\[x]")

    def test_safe_markup_passes_through(self) -> None:
        line = SafeMarkup("[red]already markup[/]")
        self.assertIs(highlight_match(line, "markup"), line)

    def test_regex_metacharacters_in_term_are_literal(self) -> None:
        out = highlight_match("cost $5.00 (approx)", "(approx)")
        self.assertIn("[bold yellow](approx)[/]", out)


def _cells(markup: str) -> dict[str, int]:
    """Count the `━` cells per colour in a rendered bar."""
    text = Text.from_markup(markup)
    out: dict[str, int] = {}
    for span in text.spans:
        colour = str(span.style).lower()
        out[colour] = out.get(colour, 0) + text.plain[span.start : span.end].count("━")
    return out


class SplitBarTests(unittest.TestCase):
    p = TIDE_PALETTE

    def test_is_proportional_and_fills_the_width(self) -> None:
        bar = split_bar(25, 75, 20, self.p)
        self.assertEqual(Text.from_markup(bar).plain, "━" * 20)
        self.assertEqual(
            _cells(bar), {self.p.success.lower(): 5, self.p.warning.lower(): 15}
        )

    def test_a_tiny_part_still_gets_one_cell(self) -> None:
        self.assertEqual(
            _cells(split_bar(10_000, 1, 20, self.p)),
            {self.p.success.lower(): 19, self.p.warning.lower(): 1},
        )
        self.assertEqual(
            _cells(split_bar(1, 10_000, 20, self.p)),
            {self.p.success.lower(): 1, self.p.warning.lower(): 19},
        )

    def test_all_in_use_and_all_reclaimable(self) -> None:
        self.assertEqual(
            _cells(split_bar(50, 0, 10, self.p)), {self.p.success.lower(): 10}
        )
        self.assertEqual(
            _cells(split_bar(0, 50, 10, self.p)), {self.p.warning.lower(): 10}
        )

    def test_empty_is_a_faint_track_and_zero_width_is_empty(self) -> None:
        self.assertEqual(
            _cells(split_bar(0, 0, 10, self.p)), {self.p.faint.lower(): 10}
        )
        self.assertEqual(split_bar(5, 5, 0, self.p), "")

    def test_approximate_is_drawn_faint(self) -> None:
        bar = split_bar(25, 75, 20, self.p, approximate=True)
        self.assertEqual(_cells(bar), {self.p.faint.lower(): 20})


def _df(size: int, reclaimable: int) -> SystemDf:
    return SystemDf(
        entries=[
            DiskUsageEntry("Containers", 1, 1, 7, 0),
            DiskUsageEntry("Images", 3, 1, size, reclaimable),
        ],
        total_size=size + 7,
        total_reclaimable=reclaimable,
    )


class ImagesDiskLineTests(unittest.TestCase):
    p = TIDE_PALETTE
    GB = 1024**3

    def test_usage_comes_from_the_df_images_entry(self) -> None:
        self.assertEqual(images_disk_usage(_df(100, 40), []), (100, 40, False))

    def test_usage_falls_back_to_the_snapshot_sum_as_approximate(self) -> None:
        used = Image("sha256:a", "a", "latest", 30, False, ["c1"], "")
        unused = Image("sha256:b", "b", "latest", 70, False, [], "")
        self.assertEqual(images_disk_usage(None, [used, unused]), (100, 70, True))
        # A df without an Images entry is as good as none.
        empty = SystemDf(entries=[], total_size=0, total_reclaimable=0)
        self.assertTrue(images_disk_usage(empty, [used])[2])

    def test_line_shows_totals_bar_legend_and_prune_hint(self) -> None:
        line = images_disk_line(_df(62 * self.GB, 40 * self.GB), [], 120, self.p)
        plain = Text.from_markup(line).plain
        self.assertTrue(plain.startswith("62.00GB on disk"))
        self.assertIn("■ in use 22.00GB", plain)
        self.assertIn("■ reclaimable 40.00GB", plain)
        self.assertTrue(plain.endswith("P prune"))
        self.assertLessEqual(len(plain), 120)
        self.assertGreater(plain.count("━"), 8)

    def test_fallback_is_marked_with_an_approx_sign(self) -> None:
        plain = Text.from_markup(images_disk_line(None, [], 120, self.p)).plain
        self.assertTrue(plain.startswith("≈0B on disk"))

    def test_narrow_widths_drop_the_bar_then_the_legend(self) -> None:
        df = _df(62 * self.GB, 40 * self.GB)
        no_bar = Text.from_markup(images_disk_line(df, [], 70, self.p)).plain
        self.assertNotIn("━", no_bar)
        self.assertIn("reclaimable", no_bar)
        self.assertLessEqual(len(no_bar), 70)
        tiny = Text.from_markup(images_disk_line(df, [], 20, self.p)).plain
        self.assertEqual(tiny, "62.00GB on disk")


class SplitRepositoryTests(unittest.TestCase):
    def test_ecr_keeps_the_account_id_as_namespace(self) -> None:
        self.assertEqual(
            split_repository("123456789012.dkr.ecr.eu-west-1.amazonaws.com/app"),
            RepoParts("ecr", "123456789012", "app"),
        )
        self.assertEqual(
            split_repository("123456789012.dkr.ecr.us-east-1.amazonaws.com/team/app"),
            RepoParts("ecr", "123456789012/team", "app"),
        )

    def test_ghcr_and_quay(self) -> None:
        self.assertEqual(
            split_repository("ghcr.io/org/tool"), RepoParts("ghcr", "org", "tool")
        )
        self.assertEqual(
            split_repository("quay.io/org/tool"), RepoParts("quay", "org", "tool")
        )

    def test_gcr_hosts(self) -> None:
        for host in ("gcr.io", "eu.gcr.io", "europe-docker.pkg.dev"):
            self.assertEqual(
                split_repository(f"{host}/proj/img"), RepoParts("gcr", "proj", "img")
            )

    def test_other_hosts_show_the_host_truncated_to_ten(self) -> None:
        self.assertEqual(
            split_repository("my.reg.io/a/b"), RepoParts("my.reg.io", "a", "b")
        )
        label = split_repository("registry.example.internal/a/b").registry
        assert label is not None
        self.assertEqual(len(label), 10)
        self.assertTrue(label.endswith("…"))
        self.assertEqual(
            split_repository("localhost:5000/app"),
            RepoParts("localhost…", "", "app"),
        )

    def test_docker_hub_has_no_pill(self) -> None:
        for repo in ("docker.io/library/nginx", "library/nginx", "nginx"):
            self.assertEqual(split_repository(repo), RepoParts(None, "", "nginx"))
        self.assertEqual(
            split_repository("docker.io/org/x"), RepoParts(None, "org", "x")
        )
        self.assertEqual(split_repository("org/x"), RepoParts(None, "org", "x"))

    def test_none_and_empty_are_the_dangling_placeholder(self) -> None:
        for repo in ("<none>", ""):
            self.assertEqual(split_repository(repo), RepoParts(None, "", "<none>"))


class RepositoryCellTests(unittest.TestCase):
    p = TIDE_PALETTE

    def _plain(self, repo: str, width: int = 40) -> str:
        return Text.from_markup(repository_cell(repo, self.p, width)).plain

    def test_registry_pill_muted_prefix_and_name(self) -> None:
        cell = repository_cell("ghcr.io/org/tool", self.p, 40)
        self.assertEqual(Text.from_markup(cell).plain, " ghcr  org › tool")
        self.assertIn(f"on {self.p.secondary}] ghcr [/]", cell)

    def test_ecr_cell(self) -> None:
        self.assertEqual(
            self._plain("123456789012.dkr.ecr.eu-west-1.amazonaws.com/app"),
            " ecr  123456789012 › app",
        )

    def test_docker_hub_cells_have_no_pill(self) -> None:
        self.assertEqual(self._plain("docker.io/library/nginx"), "nginx")
        self.assertEqual(self._plain("library/nginx"), "nginx")
        self.assertEqual(self._plain("nginx"), "nginx")
        self.assertEqual(self._plain("docker.io/org/x"), "org/x")

    def test_none_is_muted(self) -> None:
        cell = repository_cell("<none>", self.p, 20)
        self.assertEqual(Text.from_markup(cell).plain, "<none>")
        self.assertIn(self.p.muted, cell)

    def test_markup_in_names_is_escaped(self) -> None:
        self.assertEqual(self._plain("org/[bold]x"), "org/[bold]x")

    def test_namespace_is_truncated_before_the_name(self) -> None:
        plain = self._plain("123456789012.dkr.ecr.eu-west-1.amazonaws.com/app", 16)
        self.assertEqual(len(plain), 16)
        self.assertTrue(plain.endswith(" › app"))
        self.assertIn("…", plain)

    def test_name_is_truncated_once_the_namespace_is_a_lone_ellipsis(self) -> None:
        plain = self._plain("ghcr.io/org/averyveryverylongimagename", 14)
        self.assertEqual(len(plain), 14)
        self.assertIn("…", plain)
        self.assertNotIn("ghcr", plain)  # pill dropped: nothing else fits

    def test_every_width_is_respected(self) -> None:
        for repo in (
            "123456789012.dkr.ecr.eu-west-1.amazonaws.com/team/application",
            "quay.io/organisation/application",
            "organisation/application",
            "application",
        ):
            for width in range(1, 50):
                self.assertLessEqual(
                    len(self._plain(repo, width)), width, (repo, width)
                )


NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def _ago(**delta: int) -> str:
    return (NOW - timedelta(**delta)).isoformat()


class CompactRelativeTests(unittest.TestCase):
    def test_one_unit_per_range(self) -> None:
        cases = [
            (_ago(seconds=5), "now"),
            (_ago(minutes=7), "7m"),
            (_ago(hours=5), "5h"),
            (_ago(days=3), "3d"),
            (_ago(days=14), "2w"),
            (_ago(days=29), "4w"),
            (_ago(days=60), "2mo"),
            (_ago(days=400), "1y"),
        ]
        for created, expected in cases:
            self.assertEqual(compact_relative(created, NOW), expected, created)

    def test_unknown_or_future_timestamps(self) -> None:
        self.assertEqual(compact_relative("", NOW), "—")
        self.assertEqual(compact_relative("garbage", NOW), "—")
        self.assertEqual(
            compact_relative((NOW + timedelta(hours=1)).isoformat(), NOW), "now"
        )

    def test_accepts_docker_z_timestamps_with_nanoseconds(self) -> None:
        self.assertEqual(compact_relative("2026-10-24T20:00:00.123456789Z", NOW), "3d")


class ImageStatusAndRowTests(unittest.TestCase):
    p = TIDE_PALETTE

    @staticmethod
    def _img(used: list[str] | None = None, dangling: bool = False, **kw) -> Image:
        return Image(
            kw.get("id", "sha256:x"),
            kw.get("repo", "app"),
            kw.get("tag", "latest"),
            kw.get("size", 1024**3),
            dangling,
            used or [],
            kw.get("created", _ago(days=3)),
        )

    def test_status_precedence_and_sort_rank(self) -> None:
        in_use = self._img(["c"])
        unused = self._img()
        dangling = self._img(dangling=True)
        in_use_dangling = self._img(["c"], dangling=True)
        self.assertEqual(
            [image_status(i) for i in (in_use, unused, dangling, in_use_dangling)],
            ["in use", "unused", "dangling", "in use"],
        )
        self.assertEqual(
            [status_rank(i) for i in (in_use, unused, dangling)], [0, 1, 2]
        )

    def test_row_cells(self) -> None:
        repo, tag, size, created, status = (
            Text.from_markup(c) for c in image_row_cells(self._img(["c"]), self.p, NOW)
        )
        self.assertEqual(repo.plain, "app")
        self.assertEqual(tag.plain, "latest")
        self.assertEqual(size.plain, "1.00GB".rjust(SIZE_WIDTH))
        self.assertEqual(created.plain, "3d")
        self.assertEqual(status.plain, "● in use")

    def test_status_glyph_and_colour_per_state(self) -> None:
        for image, text, role in [
            (self._img(["c"]), "● in use", self.p.success),
            (self._img(), "○ unused", self.p.muted),
            (self._img(dangling=True), "✕ dangling", self.p.warning),
        ]:
            cell = image_row_cells(image, self.p, NOW)[4]
            self.assertEqual(Text.from_markup(cell).plain, text)
            self.assertIn(role, cell)

    def test_long_tag_is_truncated_and_none_tag_is_muted(self) -> None:
        long_tag = image_row_cells(self._img(tag="x" * 40), self.p, NOW)[1]
        self.assertLessEqual(len(Text.from_markup(long_tag).plain), 12)
        none_tag = image_row_cells(self._img(tag="<none>"), self.p, NOW)[1]
        self.assertIn(self.p.muted, none_tag)


class MarkedSummaryTests(unittest.TestCase):
    p = TIDE_PALETTE

    @staticmethod
    def _img(image_id: str, tag: str, size: int) -> Image:
        return Image(image_id, "app", tag, size, False, [], "")

    def test_size_counts_each_image_id_once(self) -> None:
        items = [
            self._img("sha256:a", "latest", 100),
            self._img("sha256:a", "1.0", 100),  # second tag of the same image
            self._img("sha256:b", "old", 50),
        ]
        self.assertEqual(marked_size(items), 150)
        self.assertEqual(marked_size([]), 0)

    def test_summary_counts_rows_but_sums_distinct_images(self) -> None:
        gb = 1024**3
        items = [self._img("sha256:a", "x", 2 * gb), self._img("sha256:a", "y", 2 * gb)]
        self.assertEqual(
            Text.from_markup(marked_summary(items, self.p)).plain, "✓ 2 marked · 2.00GB"
        )

    def test_footer_without_marks_is_the_registry_note(self) -> None:
        self.assertEqual(
            Text.from_markup(images_footer([], self.p)).plain,
            "registry prefixes shortened · full name in detail",
        )

    def test_footer_with_marks_lists_the_actions(self) -> None:
        plain = Text.from_markup(
            images_footer([self._img("sha256:a", "x", 1024**3)], self.p)
        ).plain
        self.assertEqual(
            plain,
            "✓ 1 marked · 1.00GB · d delete marked   a mark dangling   esc clear",
        )


class ImageRelationTests(unittest.TestCase):
    @staticmethod
    def _img(image_id: str, repo: str, tag: str, dangling: bool = False) -> Image:
        return Image(image_id, repo, tag, 1, dangling, [], "")

    def test_tags_of_the_same_image_id_only(self) -> None:
        images = [
            self._img("sha256:a", "app", "latest"),
            self._img("sha256:a", "app", "1.0"),
            self._img("sha256:a", "mirror/app", "latest"),
            self._img("sha256:b", "app", "other"),
            self._img("sha256:a", "app", "latest"),  # duplicate row
        ]
        self.assertEqual(
            image_tags(images[0], images), ["latest", "1.0", "mirror/app:latest"]
        )

    def test_untagged_images_have_no_tags(self) -> None:
        dangling = self._img("sha256:d", "<none>", "<none>", dangling=True)
        self.assertEqual(image_tags(dangling, [dangling]), [])


class CleanLayerCommandTests(unittest.TestCase):
    def test_nop_prefix_is_stripped(self) -> None:
        self.assertEqual(
            clean_layer_command('/bin/sh -c #(nop)  CMD ["bash"]'), 'CMD ["bash"]'
        )
        self.assertEqual(
            clean_layer_command("/bin/sh -c #(nop) ADD file:abc in /"),
            "ADD file:abc in /",
        )

    def test_shell_commands_read_as_run(self) -> None:
        self.assertEqual(
            clean_layer_command("/bin/sh -c apk add   curl"), "RUN apk add curl"
        )

    def test_buildkit_marker_and_whitespace(self) -> None:
        self.assertEqual(
            clean_layer_command("RUN /bin/sh -c make \\\n  install # buildkit"),
            "RUN /bin/sh -c make \\ install",
        )

    def test_empty_command(self) -> None:
        self.assertEqual(clean_layer_command(""), "—")


def _layer(command: str, size: int) -> ImageLayer:
    return ImageLayer(command, size, "0")


class LayersSummaryTests(unittest.TestCase):
    p = TIDE_PALETTE
    MB = 1024**2

    def _lines(self, layers, rows=10, width=60) -> list[str]:
        return Text.from_markup(
            layers_summary(layers, self.p, rows, width)
        ).plain.split("\n")

    def test_title_counts_every_layer(self) -> None:
        layers = [_layer("a", 1), _layer("b", 0), _layer("c", 0)]
        self.assertEqual(layers_title(layers), "layers · 3 · largest first")

    def test_largest_first_with_right_aligned_sizes(self) -> None:
        layers = [
            _layer("/bin/sh -c small", self.MB),
            _layer("/bin/sh -c big", 90 * self.MB),
        ]
        lines = self._lines(layers)
        self.assertEqual(lines[0], "   90.00MB  RUN big")
        self.assertEqual(lines[1], "    1.00MB  RUN small")

    def test_empty_layers_are_counted_not_listed(self) -> None:
        layers = [
            _layer('/bin/sh -c #(nop)  CMD ["x"]', 0),
            _layer("/bin/sh -c work", self.MB),
            _layer("/bin/sh -c #(nop)  ENV A=1", 0),
        ]
        lines = self._lines(layers)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], "… 2 empty metadata layers")
        self.assertEqual(
            self._lines([_layer("a", self.MB), _layer("b", 0)])[-1],
            "… 1 empty metadata layer",
        )

    def test_no_summary_line_when_nothing_is_left_out(self) -> None:
        self.assertEqual(
            len(self._lines([_layer("a", self.MB), _layer("b", 2 * self.MB)])), 2
        )

    def test_max_rows_caps_the_output_and_counts_the_rest(self) -> None:
        layers = [_layer(f"/bin/sh -c step{i}", (i + 1) * self.MB) for i in range(6)]
        lines = self._lines(layers, rows=4)
        self.assertEqual(len(lines), 4)
        self.assertIn("step5", lines[0])  # the largest
        self.assertEqual(lines[-1], "… 3 more")

    def test_more_and_empty_share_the_summary_line(self) -> None:
        layers = [_layer(f"/bin/sh -c s{i}", (i + 1) * self.MB) for i in range(5)]
        layers.append(_layer("CMD", 0))
        self.assertEqual(
            self._lines(layers, rows=3)[-1], "… 3 more · 1 empty metadata layer"
        )

    def test_one_row_shows_the_largest_layer_alone(self) -> None:
        layers = [
            _layer("/bin/sh -c a", self.MB),
            _layer("/bin/sh -c b", 2 * self.MB),
            _layer("CMD", 0),
        ]
        self.assertEqual(self._lines(layers, rows=1), ["    2.00MB  RUN b"])
        self.assertEqual(layers_summary(layers, self.p, 0, 60), "")

    def test_commands_are_ellipsized_to_the_width(self) -> None:
        layers = [_layer("/bin/sh -c " + "x" * 100, self.MB)]
        (line,) = self._lines(layers, width=40)
        self.assertEqual(len(line), 40)
        self.assertTrue(line.endswith("…"))

    def test_the_base_layer_is_muted_and_marked(self) -> None:
        layers = [
            _layer('/bin/sh -c #(nop)  CMD ["bash"]', 0),
            _layer("/bin/sh -c make", 5 * self.MB),
            _layer("/bin/sh -c #(nop) ADD file:abc in /", 80 * self.MB),  # oldest
        ]
        markup = layers_summary(layers, self.p, 10, 60)
        lines = Text.from_markup(markup).plain.split("\n")
        self.assertTrue(lines[0].endswith("ADD file:abc in / (base)"))
        self.assertNotIn("(base)", lines[1])
        self.assertIn(f"[{self.p.muted}]", markup.split("\n")[0])

    def test_no_base_marker_without_a_root_filesystem_layer(self) -> None:
        layers = [
            _layer("/bin/sh -c make", 5 * self.MB),
            _layer("/bin/sh -c apk add x", self.MB),
        ]
        self.assertNotIn("(base)", "\n".join(self._lines(layers)))

    def test_layer_commands_are_escaped(self) -> None:
        (line,) = self._lines([_layer("/bin/sh -c echo [bold]x", self.MB)])
        self.assertIn("RUN echo [bold]x", line)
