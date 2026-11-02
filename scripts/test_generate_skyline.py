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
        self.assertIn("@keyframes east{from{transform:translateX(", svg)
        self.assertIn("@keyframes west{from{transform:translateX(0)}", svg)
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


if __name__ == "__main__":
    unittest.main()
