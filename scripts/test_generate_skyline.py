"""Tests for generate_skyline.py. Run: python -m unittest discover -s scripts -p "test_*.py"."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_skyline as gs  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"
START = dt.date(2025, 11, 2)  # a Sunday


def fixture_response(weeks: int = 53, start: dt.date = START, daily=None) -> dict:
    """Build a synthetic GraphQL response. These numbers are test data only.

    `daily(week, day)` returns the count for a cell. The default gives a
    repeating 0 to 4 pattern.
    """
    daily = daily or (lambda w, d: (w + d) % 5)
    out = []
    for w in range(weeks):
        days = []
        for d in range(7):
            day = start + dt.timedelta(weeks=w, days=d)
            days.append({"date": day.isoformat(), "contributionCount": daily(w, d)})
        out.append({"contributionDays": days})
    return {"data": {"user": {"contributionsCollection": {"contributionCalendar": {
        "totalContributions": 0, "weeks": out}}}}}


def rects(svg: str, cls: str | None = None) -> list[ET.Element]:
    root = ET.fromstring(svg)
    found = [r for r in root.iter(f"{SVG_NS}rect")]
    if cls is None:
        return found
    return [r for r in found if cls in (r.get("class") or "").split()]


def body_heights(svg: str) -> list[int]:
    return [int(r.get("height")) for r in rects(svg, "bd")]


def window_levels(svg: str) -> list[int]:
    """Window levels in document order: 52 columns of 7 windows, Sunday first."""
    root = ET.fromstring(svg)
    levels = []
    for group in root.iter(f"{SVG_NS}g"):
        if group.get("class") != "b":
            continue
        for r in group.iter(f"{SVG_NS}rect"):
            match = re.match(r"w(\d)", r.get("class") or "")
            if match:
                levels.append(int(match.group(1)))
    return levels


def render(payload: dict) -> str:
    return gs.render_svg(gs.weeks_from_calendar(gs.extract_calendar(payload)))


class RenderFromFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def render_via_cli(self, payload: dict) -> str:
        src = self.dir / "response.json"
        out = self.dir / "skyline.svg"
        src.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(gs.main(["--input", str(src), "--output", str(out)]), 0)
        return out.read_text(encoding="utf-8")

    def test_renders_52_columns_with_7_windows_each(self):
        svg = self.render_via_cli(fixture_response())
        self.assertEqual(len(body_heights(svg)), 52)
        self.assertEqual(len(window_levels(svg)), 52 * 7)

    def test_caption_uses_real_range_and_total(self):
        payload = fixture_response()
        svg = self.render_via_cli(payload)
        weeks = gs.weeks_from_calendar(gs.extract_calendar(payload))
        total = sum(w["count"] for w in weeks)
        self.assertEqual(weeks[0]["start"], START + dt.timedelta(weeks=1))
        self.assertIn(f"{gs.fmt_date(weeks[0]['start'])} to {gs.fmt_date(weeks[-1]['end'])}", svg)
        self.assertIn(f"{total:,} contributions in 52 weeks", svg)
        self.assertNotIn(gs.OFFLINE_CAPTION, svg)

    def test_output_is_valid_xml_and_self_contained(self):
        svg = self.render_via_cli(fixture_response())
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, f"{SVG_NS}svg")
        self.assertEqual(root.get("role"), "img")
        self.assertIsNotNone(root.find(f"{SVG_NS}title"))
        self.assertIsNotNone(root.find(f"{SVG_NS}desc"))
        for banned in ("<script", "href", "foreignObject", "@import", "@font-face"):
            self.assertNotIn(banned, svg)
        self.assertEqual(svg.count("http"), 1)  # the xmlns only
        for ref in re.findall(r"url\(([^)]*)\)", svg):
            self.assertTrue(ref.startswith("#"), ref)

    def test_size_under_80_kb_at_full_activity(self):
        svg = self.render_via_cli(fixture_response(daily=lambda w, d: 1 + (w * 7 + d) % 40))
        self.assertLess(len(svg.encode("utf-8")), 80 * 1024)

    def test_partial_final_week_keeps_future_days_dark(self):
        payload = fixture_response(daily=lambda w, d: 3)
        last = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][-1]
        last["contributionDays"] = last["contributionDays"][:3]
        levels = window_levels(render(payload))
        self.assertEqual(levels[-7:-4], [1, 1, 1])
        self.assertEqual(levels[-4:], [0, 0, 0, 0])


class HeightScaling(unittest.TestCase):
    def test_linear_scaling_to_peak(self):
        self.assertEqual(gs.column_heights([0, 5, 10]),
                         [gs.MIN_H, gs.MIN_H + (gs.MAX_H - gs.MIN_H) // 2, gs.MAX_H])

    def test_min_height_holds_all_7_windows(self):
        self.assertGreaterEqual(gs.MIN_H, gs.WIN_BAND + 6)

    def test_svg_heights_follow_weekly_totals(self):
        totals = [0] * 49 + [4, 8, 2]
        svg = render(fixture_response(daily=lambda w, d: totals[w - 1] if d == 0 and w > 0 else 0))
        heights = body_heights(svg)
        self.assertEqual(heights[-2], gs.MAX_H)
        self.assertEqual(heights[-3], gs.MIN_H + (gs.MAX_H - gs.MIN_H) // 2)
        self.assertEqual(heights[0], gs.MIN_H)


class WindowLighting(unittest.TestCase):
    def test_quartile_thresholds_use_nonzero_days(self):
        self.assertEqual(gs.quantile_thresholds([0, 0, 1, 2, 3, 4, 5, 6, 7, 8]), (2, 4, 6))
        self.assertEqual(gs.quantile_thresholds([0, 0, 0]), (0, 0, 0))
        self.assertEqual(gs.quantile_thresholds([None, 5]), (5, 5, 5))

    def test_day_level_boundaries(self):
        th = (2, 4, 6)
        expected = {None: 0, 0: 0, 1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 80: 4}
        for count, level in expected.items():
            self.assertEqual(gs.day_level(count, th), level, count)

    def test_windows_follow_daily_counts_sunday_first(self):
        # Only the last week has activity: counts 0 to 6 from Sunday to Saturday.
        # Non-zero days 1 to 6 give quartile thresholds (2, 3, 5).
        levels = window_levels(render(fixture_response(daily=lambda w, d: d if w == 52 else 0)))
        self.assertEqual(levels[-7:], [0, 1, 1, 2, 3, 3, 4])
        self.assertEqual(set(levels[:-7]), {0})


class MotionAndBaseline(unittest.TestCase):
    def test_reduced_motion_block_stops_all_animation(self):
        svg = gs.render_svg(gs.offline_weeks(dt.date(2026, 11, 2)), offline=True)
        self.assertIn("@media (prefers-reduced-motion: reduce){*{animation:none!important}}", svg)

    def test_traffic_moves_both_ways_on_transform(self):
        svg = render(fixture_response())
        self.assertEqual(keyframe_shift(svg, "east"), (gs.EAST_FROM, gs.EAST_TO))
        self.assertEqual(keyframe_shift(svg, "west"), (gs.WEST_FROM, gs.WEST_TO))
        self.assertTrue(rects(svg, "hl"))
        self.assertTrue(rects(svg, "tl"))

    def test_offline_is_zero_data_with_honest_caption(self):
        svg = gs.render_svg(gs.offline_weeks(dt.date(2026, 11, 2)), offline=True)
        ET.fromstring(svg)
        self.assertIn(gs.OFFLINE_CAPTION, svg)
        self.assertEqual(len(body_heights(svg)), 52)
        self.assertEqual(set(body_heights(svg)), {gs.MIN_H})
        self.assertEqual(set(window_levels(svg)), {0})
        self.assertNotIn("contributions in", svg)

    def test_offline_cli_writes_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "skyline.svg"
            self.assertEqual(gs.main(["--offline", "--output", str(out)]), 0)
            self.assertIn(gs.OFFLINE_CAPTION, out.read_text(encoding="utf-8"))

    def test_graphql_errors_raise(self):
        with self.assertRaises(ValueError):
            gs.extract_calendar({"errors": [{"message": "Bad credentials"}]})
        with self.assertRaises(ValueError):
            gs.extract_calendar({"data": {"user": None}})


BANNER = Path(__file__).resolve().parent.parent / "assets" / "header-banner.svg"


def keyframe_shift(svg: str, name: str) -> tuple[float, float]:
    """The translateX start and end of a lane keyframe."""
    m = re.search(r"@keyframes %s\{from\{transform:translateX\((-?[\d.]+)(?:px)?\)\}"
                  r"to\{transform:translateX\((-?[\d.]+)(?:px)?\)\}\}" % name, svg)
    return float(m.group(1)), float(m.group(2))


def lanes(svg: str) -> dict[float, list[ET.Element]]:
    """Vehicle groups keyed by the tyre line of their lane."""
    out = {}
    for g in ET.fromstring(svg).iter(f"{SVG_NS}g"):
        cars = [c for c in g if c.tag == f"{SVG_NS}g" and (c.get("class") or "").startswith("v ")]
        if cars:
            y = float(re.search(r"translate\(0 (-?[\d.]+)\)", g.get("transform")).group(1))
            out[y] = cars
    return out


def direction(car: ET.Element) -> str:
    return (car.get("class") or "").split()[1]


def lamp_x(car: ET.Element, cls: str) -> float:
    (lamp,) = [r for r in car.iter(f"{SVG_NS}rect") if r.get("class") == cls]
    return float(lamp.get("x")) + float(lamp.get("width")) / 2


def beam_xs(car: ET.Element) -> list[float]:
    (beam,) = [p for p in car.iter(f"{SVG_NS}path") if (p.get("class") or "").startswith("hb")]
    nums = [float(n) for n in re.findall(r"-?[\d.]+", beam.get("d"))]
    return nums[0::2]


def style_rule(svg: str, selector: str) -> str:
    m = re.search(r"(?m)^%s\{([^}]*)\}" % re.escape(selector), svg)
    return m.group(1) if m else ""


class Traffic(unittest.TestCase):
    """Run on the generated skyline and on the hand-edited header banner."""

    def scenes(self):
        yield "skyline", render(fixture_response())
        yield "banner", BANNER.read_text(encoding="utf-8")

    def test_road_markings_carry_no_animation(self):
        for name, svg in self.scenes():
            with self.subTest(name):
                for sel in (".mid", ".kerb", ".mark"):
                    self.assertNotIn("animation", style_rule(svg, sel))
                    self.assertNotIn("dashoffset", style_rule(svg, sel))
                root = ET.fromstring(svg)
                marks = [p for p in root.iter(f"{SVG_NS}path") if p.get("class") in ("mid", "kerb", "mark")]
                self.assertTrue(marks)
                for p in marks:
                    self.assertIsNone(p.get("style"))
                for car_list in lanes(svg).values():
                    for car in car_list:
                        inner = {e.get("class") for e in car.iter()}
                        self.assertFalse(inner & {"mid", "kerb", "mark"})

    def test_headlights_lead_and_tail_lights_trail(self):
        for name, svg in self.scenes():
            for car_list in lanes(svg).values():
                for car in car_list:
                    with self.subTest(name, kind=car.get("data-kind")):
                        head, tail, beam = lamp_x(car, "hl"), lamp_x(car, "tl"), beam_xs(car)
                        if direction(car) == "east":
                            self.assertGreater(head, tail)
                            self.assertGreater(max(beam), head)  # beam reaches ahead, to the right
                            self.assertGreaterEqual(min(beam), head - 1)
                        else:
                            self.assertLess(head, tail)
                            self.assertLess(min(beam), head)  # beam reaches ahead, to the left
                            self.assertLessEqual(max(beam), head + 1)

    def test_each_lane_moves_one_way_and_keeps_left(self):
        for name, svg in self.scenes():
            with self.subTest(name):
                by_lane = lanes(svg)
                self.assertEqual(len(by_lane), 2)
                dirs = {y: {direction(c) for c in cars} for y, cars in by_lane.items()}
                for d in dirs.values():
                    self.assertEqual(len(d), 1)
                far, near = min(dirs), max(dirs)
                self.assertEqual(dirs[far], {"east"})   # UK keep-left, seen from the south bank
                self.assertEqual(dirs[near], {"west"})
                e0, e1 = keyframe_shift(svg, "east")
                w0, w1 = keyframe_shift(svg, "west")
                self.assertLess(e0, e1)
                self.assertGreater(w0, w1)
                self.assertGreaterEqual(min(e1, w0), gs.WIDTH)  # enter and leave off-canvas
                self.assertLessEqual(max(e0, w1), -gs.MAX_LEN)

    def test_cars_in_a_lane_share_speed_and_keep_apart(self):
        for name, svg in self.scenes():
            for cars in lanes(svg).values():
                with self.subTest(name):
                    shifts = sorted(float(re.search(r"translateX\((-?[\d.]+)px\)", c.get("style")).group(1))
                                    for c in cars)
                    gaps = [b - a for a, b in zip(shifts, shifts[1:])]
                    self.assertGreater(min(gaps), gs.MAX_LEN + gs.BEAM)
                    self.assertIn("linear infinite", style_rule(svg, "." + direction(cars[0])))


def with_stats(payload: dict, viewer="mdtarek-islam", **fields) -> dict:
    """Add breakdown fields and a viewer to a synthetic response."""
    coll = payload["data"]["user"]["contributionsCollection"]
    coll.update(fields)
    if viewer is not None:
        payload["data"]["viewer"] = {"login": viewer}
    return payload


FULL = dict(
    totalCommitContributions=900, totalPullRequestReviewContributions=120,
    totalPullRequestContributions=80, totalIssueContributions=30,
    totalRepositoryContributions=12, restrictedContributionsCount=0,
    commitContributionsByRepository=[
        {"repository": {"isPrivate": False}, "contributions": {"totalCount": 600}},
        {"repository": {"isPrivate": True}, "contributions": {"totalCount": 300}},
    ],
)


def render_stats(payload: dict) -> str:
    weeks = gs.weeks_from_calendar(gs.extract_calendar(payload))
    return gs.render_svg(weeks, stats=gs.extract_stats(payload, gs.DEFAULT_LOGIN))


def bar_widths(svg: str) -> dict:
    return {r.get("data-k"): float(r.get("width")) for r in rects(svg, "ab")}


def scene_text(svg: str) -> str:
    """The visible markup after the defs: captions, labels and shapes."""
    return svg.split("</defs>")[1]


class TokenSelection(unittest.TestCase):
    def test_profile_token_wins(self):
        env = {"PROFILE_STATS_TOKEN": " p ", "GITHUB_TOKEN": "g"}
        self.assertEqual(gs.pick_token(env), ("p", "PROFILE_STATS_TOKEN"))

    def test_falls_back_to_github_token(self):
        self.assertEqual(gs.pick_token({"PROFILE_STATS_TOKEN": "  ", "GITHUB_TOKEN": "g"}),
                         ("g", "GITHUB_TOKEN"))

    def test_no_token(self):
        self.assertEqual(gs.pick_token({}), ("", ""))


class PrivateVisibility(unittest.TestCase):
    def test_owner_viewer_sees_private(self):
        stats = gs.extract_stats(with_stats(fixture_response(), viewer="MDTarek-Islam", **FULL), "mdtarek-islam")
        self.assertTrue(stats["viewer_is_owner"])
        self.assertTrue(stats["private_visible"])

    def test_other_viewer_without_restricted_sees_none(self):
        stats = gs.extract_stats(with_stats(fixture_response(), viewer="github-actions[bot]", **FULL), "mdtarek-islam")
        self.assertFalse(stats["viewer_is_owner"])
        self.assertFalse(stats["private_visible"])

    def test_other_viewer_with_restricted_count_is_visible(self):
        fields = dict(FULL, restrictedContributionsCount=40)
        stats = gs.extract_stats(with_stats(fixture_response(), viewer="bot", **fields), "mdtarek-islam")
        self.assertFalse(stats["viewer_is_owner"])
        self.assertTrue(stats["private_visible"])
        self.assertEqual(stats["private"], 300)  # the larger of 300 private commits and 40 restricted

    def test_query_asks_for_every_field(self):
        for name in ("viewer { login }", "totalCommitContributions", "totalPullRequestReviewContributions",
                     "totalPullRequestContributions", "totalIssueContributions",
                     "totalRepositoryContributions", "restrictedContributionsCount",
                     "commitContributionsByRepository(maxRepositories: 100)", "isPrivate",
                     "contributionCalendar"):
            self.assertIn(name, gs.QUERY)


class Breakdown(unittest.TestCase):
    def setUp(self):
        self.payload = with_stats(fixture_response(), **FULL)
        self.stats = gs.extract_stats(self.payload, "mdtarek-islam")

    def test_counts(self):
        s = self.stats
        self.assertEqual((s["commits"], s["reviews"], s["pull_requests"], s["issues"], s["repositories"],
                          s["private"]), (900, 120, 80, 30, 12, 300))

    def test_svg_shows_labels_and_counts(self):
        svg = render_stats(self.payload)
        for label in ("Commits", "Code reviews", "Pull requests", "Issues", "New repositories", "Private"):
            self.assertIn(f">{label}<", svg)
        for count in ("900", "120", "80", "30", "12", "300"):
            self.assertIn(f">{count}<", svg)

    def test_bar_widths_are_proportional_to_the_largest(self):
        widths = bar_widths(render_stats(self.payload))
        self.assertEqual(set(widths), {"commits", "reviews", "pull_requests", "issues", "repositories", "private"})
        self.assertEqual(widths["commits"], gs.BAR_MAX)
        self.assertAlmostEqual(widths["reviews"], gs.BAR_MAX * 120 / 900, delta=0.1)
        self.assertAlmostEqual(widths["private"], gs.BAR_MAX / 3, delta=0.1)

    def test_bars_animate_with_stagger_and_reduced_motion_stops_them(self):
        svg = render_stats(self.payload)
        self.assertIn("@keyframes grow{from{transform:scaleX(0)}}", svg)
        delays = [int(re.search(r"animation-delay:(\d+)ms", r.get("style")).group(1)) for r in rects(svg, "ab")]
        self.assertEqual(delays, sorted(delays))
        self.assertEqual(len(set(delays)), 6)
        self.assertIn("@media (prefers-reduced-motion: reduce){*{animation:none!important}}", svg)

    def test_caption_mentions_private_only_when_visible(self):
        self.assertIn("includes private contributions", scene_text(render_stats(self.payload)))
        hidden = with_stats(fixture_response(), viewer="bot", **FULL)
        self.assertNotIn("private contributions", scene_text(render_stats(hidden)))

    def test_caption_states_window_and_total(self):
        svg = render_stats(self.payload)
        weeks = gs.weeks_from_calendar(gs.extract_calendar(self.payload))
        self.assertIn(f"{gs.fmt_date(weeks[0]['start'])} to {gs.fmt_date(weeks[-1]['end'])}", svg)
        self.assertIn(f"{sum(w['count'] for w in weeks):,} contributions in 52 weeks", svg)

    def test_missing_fields_fall_back_to_zero(self):
        payload = fixture_response()
        stats = gs.extract_stats(payload, "mdtarek-islam")
        self.assertEqual([stats[k] for k in ("commits", "reviews", "pull_requests", "issues", "repositories",
                                              "private")], [0] * 6)
        self.assertFalse(stats["private_visible"])
        svg = render_stats(payload)
        ET.fromstring(svg)
        self.assertEqual(set(bar_widths(svg).values()), {0.0})

    def test_null_fields_and_bare_calendar_do_not_crash(self):
        fields = dict(FULL, totalCommitContributions=None, commitContributionsByRepository=None)
        stats = gs.extract_stats(with_stats(fixture_response(), **fields), "mdtarek-islam")
        self.assertEqual((stats["commits"], stats["private"]), (0, 0))
        bare = gs.extract_calendar(fixture_response())
        self.assertEqual(gs.extract_stats(bare, "mdtarek-islam")["commits"], 0)
        self.assertEqual(gs.extract_stats({}, "mdtarek-islam")["commits"], 0)

    def test_valid_xml_self_contained_and_small(self):
        busy = with_stats(fixture_response(daily=lambda w, d: 1 + (w * 7 + d) % 40), **FULL)
        svg = render_stats(busy)
        ET.fromstring(svg)
        for banned in ("<script", "href", "foreignObject", "@import"):
            self.assertNotIn(banned, svg)
        self.assertEqual(svg.count("http"), 1)
        self.assertLess(len(svg.encode("utf-8")), 80 * 1024)
        self.assertNotIn("\u2014", svg)
        self.assertNotIn("\u2013", svg)

    def test_offline_shows_zero_breakdown_and_baseline_caption(self):
        svg = gs.render_svg(gs.offline_weeks(dt.date(2026, 11, 2)), offline=True)
        ET.fromstring(svg)
        self.assertIn(gs.OFFLINE_CAPTION, svg)
        self.assertNotIn("private contributions", scene_text(svg))
        self.assertEqual(set(bar_widths(svg).values()), {0.0})

    def test_cli_input_renders_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "r.json", Path(tmp) / "o.svg"
            src.write_text(json.dumps(self.payload), encoding="utf-8")
            self.assertEqual(gs.main(["--input", str(src), "--output", str(out)]), 0)
            self.assertEqual(bar_widths(out.read_text(encoding="utf-8"))["commits"], gs.BAR_MAX)


if __name__ == "__main__":
    unittest.main()
