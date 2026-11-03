#!/usr/bin/env python3
"""Render the London skyline contribution graph for the profile README.

The scene is London at night, seen across the Thames from the south bank.
Landmark silhouettes stand behind a row of 52 data buildings. Each building
is one week of contributions: its height scales with the weekly total, and
its 7 windows light up by daily count, Sunday at the top. Traffic runs both
ways along the Embankment road, and the river reflects the lit weeks.

Window brightness uses quartiles of the non-zero daily counts, like the
GitHub contribution calendar. The script uses the standard library only.

Modes:
  default    fetch the calendar from the GitHub GraphQL API (needs GITHUB_TOKEN)
  --input    render from a saved GraphQL response or calendar JSON file
  --offline  render an honest zero-data baseline
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

API_URL = "https://api.github.com/graphql"
DEFAULT_LOGIN = "mdtarek-islam"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "assets" / "london-skyline.svg"
OFFLINE_CAPTION = "Contribution data loads on the first scheduled run"

QUERY = (
    "query($login: String!) { user(login: $login) { contributionsCollection { "
    "contributionCalendar { totalContributions weeks { contributionDays { "
    "date contributionCount } } } } } }"
)

# Design tokens. Colours follow the Sunset ramp in DESIGN.md.
TOKENS = {
    "canvas": "#0B0F19",
    "surface": "#0B0F19",
    "border": "#9F1239",
    "text": "#F8FAFC",
    "muted": "#A1A1AA",
    "accent": "#F97316",
    "lamp": "#F59E0B",
    "window": "#F59E0B",
    "window_hot": "#FBBF24",
    "window_off": "#2E1065",
    "tail": "#F43F5E",
    "indigo": "#2E1065",
    "deep": "#4C0519",
    "violet": "#7C3AED",
    "rose": "#F43F5E",
    "coral": "#FB7185",
    "crimson": "#DC2626",
    "gold": "#FBBF24",
}
FONT = '-apple-system,"Segoe UI",Helvetica,Arial,sans-serif'

WEEKS = 52
DAYS = 7
WIDTH, HEIGHT = 960, 440
GROUND = 300            # base of the buildings and top of the road
ROAD_BOTTOM = 328
RIVER_TOP = 346
RIVER_BOTTOM = 404
COL_W = 13
STEP = 17
X0 = (WIDTH - (WEEKS - 1) * STEP - COL_W) // 2
WIN_W, WIN_H, WIN_PITCH = 7, 4, 6
WIN_BAND = DAYS * WIN_PITCH - (WIN_PITCH - WIN_H)   # 40 px from top window to bottom window
MIN_H = WIN_BAND + 12                                # an empty week still shows its 7 windows
MAX_H = 130
STAGGER_MS = 7
LANE_TILE = 240

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

T = TOKENS
STYLE = f"""
text{{font-family:{FONT}}}
.t{{fill:{T['text']};font-size:16px;font-weight:600}}
.s{{fill:{T['muted']};font-size:12px}}
.c{{fill:{T['text']};font-size:13px}}
.pkt{{fill:{T['accent']};font-size:12px}}
.lm{{fill:{T['indigo']};stroke:{T['rose']};stroke-opacity:.45;stroke-width:1}}
.ln{{fill:none;stroke:{T['rose']};stroke-opacity:.45;stroke-width:1}}
.cap{{fill:{T['gold']};fill-opacity:.7}}
.st{{fill:{T['text']}}}
.moon{{fill:{T['text']};fill-opacity:.85}}
.eye{{transform-box:fill-box;transform-origin:center;animation:spin 150s linear infinite}}
.bc{{fill:{T['tail']};animation:blink 2.4s ease-in-out infinite}}
.bd{{fill:{T['deep']};stroke:{T['border']};stroke-width:1}}
.b{{transform-box:fill-box;transform-origin:50% 100%;animation:rise .7s cubic-bezier(.2,.7,.3,1) both}}
.w0{{fill:{T['window_off']}}}
.w1{{fill:{T['window']};fill-opacity:.35}}
.w2{{fill:{T['window']};fill-opacity:.55}}
.w3{{fill:{T['window']};fill-opacity:.8}}
.w4{{fill:{T['window_hot']}}}
.fl{{animation:flicker 6.5s ease-in-out infinite}}
.road{{fill:{T['surface']}}}
.kerb{{fill:none;stroke:{T['border']};stroke-width:1}}
.mid{{fill:none;stroke:{T['muted']};stroke-opacity:.5;stroke-width:1;stroke-dasharray:10 14}}
.wall{{fill:{T['canvas']};stroke:{T['border']};stroke-width:1}}
.hl{{fill:{T['text']}}}
.hb{{fill:{T['text']};fill-opacity:.18}}
.tl{{fill:{T['tail']}}}
.tt{{fill:{T['tail']};fill-opacity:.3}}
.east{{animation:east 6s linear infinite}}
.west{{animation:west 7.5s linear infinite}}
.water{{fill:{T['accent']};fill-opacity:.08}}
.rf{{fill:url(#sk-fade)}}
.shim{{animation:shim 4.8s ease-in-out infinite alternate}}
.shim.s2{{animation-delay:-2.4s}}
.rip{{fill:none;stroke:{T['gold']};stroke-opacity:.35;stroke-width:1;stroke-dasharray:8 18;animation:flow 10s linear infinite}}
.rip.r2{{stroke-opacity:.18;animation-duration:14s;animation-direction:reverse}}
.pk{{fill:{T['accent']}}}
@keyframes rise{{from{{transform:scaleY(0)}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.15}}}}
@keyframes flicker{{0%,100%{{opacity:1}}50%{{opacity:.55}}}}
@keyframes east{{from{{transform:translateX(-{LANE_TILE}px)}}to{{transform:translateX(0)}}}}
@keyframes west{{from{{transform:translateX(0)}}to{{transform:translateX(-{LANE_TILE}px)}}}}
@keyframes shim{{from{{opacity:1}}to{{opacity:.55}}}}
@keyframes flow{{to{{stroke-dashoffset:-104}}}}
@media (prefers-reduced-motion: reduce){{*{{animation:none!important}}}}
"""


def fmt_date(day: dt.date) -> str:
    """Format a date as '3 Nov 2025' without locale dependence."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def weekday_row(day: dt.date) -> int:
    """Row of a day in the window stack: Sunday is row 0, Saturday is row 6."""
    return (day.weekday() + 1) % 7


# Data ---------------------------------------------------------------------

def extract_calendar(payload: dict) -> dict:
    """Return the contributionCalendar object from a GraphQL response or a bare calendar."""
    if "weeks" in payload:
        return payload
    if payload.get("errors"):
        messages = "; ".join(str(e.get("message", e)) for e in payload["errors"])
        raise ValueError(f"GraphQL error: {messages}")
    try:
        user = payload["data"]["user"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Response has no data.user field") from exc
    if user is None:
        raise ValueError("GitHub user not found")
    return user["contributionsCollection"]["contributionCalendar"]


def weeks_from_calendar(calendar: dict, limit: int = WEEKS) -> list[dict]:
    """Collapse the calendar into week records and keep the last `limit` weeks.

    Each record holds the first and last date, the weekly total and a 7-slot
    list of daily counts indexed Sunday first. Days outside the calendar are None.
    """
    weeks = []
    for week in calendar.get("weeks", []):
        entries = week.get("contributionDays") or []
        if not entries:
            continue
        days: list[int | None] = [None] * DAYS
        dates = []
        for entry in entries:
            day = dt.date.fromisoformat(entry["date"])
            dates.append(day)
            days[weekday_row(day)] = int(entry.get("contributionCount", 0))
        weeks.append({
            "start": min(dates),
            "end": max(dates),
            "count": sum(c for c in days if c),
            "days": days,
        })
    return weeks[-limit:]


def offline_weeks(today: dt.date | None = None, limit: int = WEEKS) -> list[dict]:
    """Build zero-count weeks ending with the current Sunday-start week."""
    today = today or dt.date.today()
    current = today - dt.timedelta(days=weekday_row(today))
    weeks = []
    for back in range(limit - 1, -1, -1):
        start = current - dt.timedelta(weeks=back)
        days = [0 if start + dt.timedelta(days=d) <= today else None for d in range(DAYS)]
        weeks.append({"start": start, "end": start + dt.timedelta(days=6), "count": 0, "days": days})
    return weeks


def column_heights(counts: list[int]) -> list[int]:
    """Scale counts linearly from MIN_H to MAX_H. The peak week reaches MAX_H."""
    peak = max(counts, default=0)
    if peak <= 0:
        return [MIN_H for _ in counts]
    span = MAX_H - MIN_H
    return [MIN_H + round(span * max(c, 0) / peak) for c in counts]


def quantile_thresholds(counts: list[int]) -> tuple[int, int, int]:
    """Return the 25th, 50th and 75th percentile of the non-zero daily counts.

    Nearest-rank percentiles keep the thresholds on real observed counts.
    """
    nonzero = sorted(c for c in counts if c and c > 0)
    if not nonzero:
        return (0, 0, 0)
    n = len(nonzero)

    def rank(p: float) -> int:
        return nonzero[max(0, math.ceil(p * n) - 1)]

    return (rank(0.25), rank(0.5), rank(0.75))


def day_level(count: int | None, thresholds: tuple[int, int, int]) -> int:
    """Map a daily count to a window level: 0 dark, 1 to 4 brighter by quartile."""
    if not count or count <= 0:
        return 0
    q1, q2, q3 = thresholds
    if count <= q1:
        return 1
    if count <= q2:
        return 2
    if count <= q3:
        return 3
    return 4


# Scene --------------------------------------------------------------------

def stars() -> str:
    """Scatter a fixed field of stars in the upper sky with a seeded generator."""
    seed = 20261102
    dots = []
    for _ in range(46):
        seed = (seed * 1103515245 + 12345) % 2**31
        x = 20 + seed % 920
        seed = (seed * 1103515245 + 12345) % 2**31
        y = 52 + seed % 110
        seed = (seed * 1103515245 + 12345) % 2**31
        r = 0.5 + (seed % 7) / 10
        op = 0.25 + (seed % 5) / 10
        dots.append(f'<circle cx="{x}" cy="{y}" r="{r:.1f}" opacity="{op:.2f}"/>')
    return f'<g class="st">{"".join(dots)}</g>'


def london_eye(cx: int, ground: int) -> str:
    """The London Eye: A-frame legs, a slow-turning rim, spokes and capsules."""
    hub = 104
    radius = 86
    spokes = []
    for k in range(16):
        a = math.pi * k / 16
        dx, dy = radius * math.cos(a), radius * math.sin(a)
        spokes.append(f"M{-dx:.1f} {-dy:.1f}L{dx:.1f} {dy:.1f}")
    capsules = []
    for k in range(32):
        a = 2 * math.pi * k / 32
        capsules.append(
            f'<circle class="cap" cx="{(radius + 4) * math.cos(a):.1f}" '
            f'cy="{(radius + 4) * math.sin(a):.1f}" r="2.3"/>'
        )
    return (
        f'<g transform="translate({cx} {ground - hub})">'
        f'<path class="ln" d="M-34 {hub}L0 0L34 {hub}M-22 {hub}L0 0L22 {hub}"/>'
        '<g class="eye">'
        f'<circle class="ln" r="{radius}"/>'
        f'<circle class="ln" r="{radius - 6}"/>'
        f'<path class="ln" stroke-opacity=".25" d="{"".join(spokes)}"/>'
        f'{"".join(capsules)}'
        "</g>"
        '<circle class="lm" r="5"/>'
        "</g>"
    )


def st_pauls(cx: int, ground: int) -> str:
    """St Paul's Cathedral: west towers, drum, peristyle, dome, lantern and cross."""
    return (
        f'<g transform="translate({cx} {ground})">'
        '<path class="lm" d="M-64 0V-46H64V0Z"/>'
        '<path class="lm" d="M-64 -46V-96H-46V-46ZM46 -46V-96H64V-46Z"/>'
        '<path class="lm" d="M-64 -96Q-55 -112 -46 -96ZM46 -96Q55 -112 64 -96Z"/>'
        '<path class="ln" d="M-55 -106V-116M55 -106V-116"/>'
        '<path class="lm" d="M-22 -46L0 -60L22 -46Z"/>'
        '<path class="lm" d="M-36 -46V-78H36V-46Z"/>'
        '<path class="ln" d="M-28 -50V-74M-18 -50V-74M-8 -50V-74M2 -50V-74M12 -50V-74M22 -50V-74"/>'
        '<path class="lm" d="M-29 -78V-94H29V-78Z"/>'
        '<path class="lm" d="M-31 -94C-31 -134 31 -134 31 -94Z"/>'
        '<path class="ln" d="M-15 -100C-14 -116 -8 -124 0 -126M15 -100C14 -116 8 -124 0 -126"/>'
        '<path class="lm" d="M-5 -124V-142H5V-124Z"/>'
        '<path class="lm" d="M-5 -142Q0 -150 5 -142Z"/>'
        '<path class="ln" d="M0 -148V-160M-4 -155H4"/>'
        "</g>"
    )


def gherkin(cx: int, ground: int) -> str:
    """30 St Mary Axe: a tapered bullet with spiral bands."""
    return (
        f'<g transform="translate({cx} {ground})">'
        '<path class="lm" d="M-24 0C-35 -70 -29 -142 0 -178C29 -142 35 -70 24 0Z"/>'
        '<path class="ln" d="M-24 -8C-6 -46 12 -96 18 -150M-6 0C10 -40 20 -90 24 -122'
        'M24 -8C6 -46 -12 -96 -18 -150M6 0C-10 -40 -20 -90 -24 -122"/>'
        '<path class="ln" d="M-6 -172C-2 -176 2 -176 6 -172"/>'
        "</g>"
    )


def tower_bridge(cx: int, ground: int) -> str:
    """Tower Bridge: twin Gothic towers, high walkways, bascule deck and chains."""
    tower = (
        '<path class="lm" d="M-14 0V-112H14V0Z"/>'
        '<path class="lm" d="M-14 -112V-124H-8V-112ZM8 -112V-124H14V-112Z"/>'
        '<path class="lm" d="M-9 -112L0 -142L9 -112Z"/>'
        '<path class="ln" d="M-11 -124V-132M11 -124V-132M0 -142V-150"/>'
        '<path class="ln" d="M-5 -30V-48H5V-30M-5 -62V-80H5V-62"/>'
    )
    return (
        f'<g transform="translate({cx} {ground})">'
        '<path class="ln" d="M-70 -86C-92 -52 -120 -32 -150 -26M70 -86C92 -52 120 -32 150 -26"/>'
        '<path class="lm" d="M-44 -100H44V-94H-44ZM-44 -88H44V-82H-44Z"/>'
        '<path class="lm" d="M-152 -28H152V-22H-152Z"/>'
        f'<g transform="translate(-58 0)">{tower}</g>'
        f'<g transform="translate(58 0)">{tower}</g>'
        "</g>"
    )


def shard(cx: int, ground: int) -> str:
    """The Shard: a tapered glass spire with an aircraft warning light."""
    return (
        f'<g transform="translate({cx} {ground})">'
        '<path class="lm" d="M-38 0L-6 -222L-2 -240L1 -228L4 -234L8 -216L38 0Z"/>'
        '<path class="ln" d="M-4 0L-4 -214M14 0L4 -206"/>'
        '<circle class="bc" cx="-2" cy="-240" r="1.8"/>'
        "</g>"
    )


def moon() -> str:
    """A crescent moon, drawn as two arcs."""
    return '<path class="moon" d="M752 60A14 14 0 1 0 762 84A11 11 0 0 1 752 60Z"/>'


def landmarks() -> str:
    g = GROUND
    return "".join((
        london_eye(118, g),
        st_pauls(308, g),
        gherkin(478, g),
        tower_bridge(648, g),
        shard(862, g),
    ))


def traffic() -> str:
    """Two lanes of traffic: headlights head east, tail-lights head west."""
    east_offsets = (12, 98, 176)
    west_offsets = (44, 130, 204)
    east, west = [], []
    for tile in range(-1, WIDTH // LANE_TILE + 2):
        base = tile * LANE_TILE
        for o in east_offsets:
            x = base + o
            east.append(f'<rect class="hb" x="{x + 4}" y="320" width="20" height="2"/>'
                        f'<rect class="hl" x="{x}" y="319.5" width="4" height="3" rx="1"/>')
        for o in west_offsets:
            x = base + o
            west.append(f'<rect class="tl" x="{x}" y="305.5" width="4" height="3" rx="1"/>'
                        f'<rect class="tt" x="{x + 4}" y="306" width="12" height="2"/>')
    return (
        f'<rect class="road" x="0" y="{GROUND}" width="{WIDTH}" height="{ROAD_BOTTOM - GROUND}"/>'
        f'<g clip-path="url(#sk-road)">'
        f'<g class="west">{"".join(west)}</g>'
        f'<g class="east">{"".join(east)}</g>'
        "</g>"
        f'<path class="mid" d="M0 314H{WIDTH}"/>'
        f'<path class="kerb" d="M0 {GROUND + 0.5}H{WIDTH}"/>'
    )


def month_labels(weeks: list[dict]) -> str:
    """Label the first column of each month along the river wall."""
    labels = []
    last_x = -100
    previous = None
    for i, week in enumerate(weeks):
        month = week["start"].month
        if month != previous:
            x = X0 + i * STEP
            if x - last_x >= 40:
                labels.append(f'<text class="s" x="{x}" y="341">{MONTHS[month - 1]}</text>')
                last_x = x
            previous = month
    return "".join(labels)


def legend(right: int, y: int) -> str:
    """A small key for window brightness, from fewer to more contributions."""
    parts = [f'<text class="s" x="{right - 114}" y="{y}" text-anchor="end">Fewer</text>']
    for k in range(5):
        x = right - 106 + k * 14
        parts.append(f'<rect class="w{k}" x="{x}" y="{y - 8}" width="10" height="6"/>')
    parts.append(f'<text class="s" x="{right}" y="{y}" text-anchor="end">More</text>')
    return "".join(parts)


def render_svg(weeks: list[dict], login: str = DEFAULT_LOGIN, offline: bool = False) -> str:
    """Render the skyline SVG for the given week records."""
    counts = [w["count"] for w in weeks]
    heights = column_heights(counts)
    peak = max(counts, default=0)
    peak_index = counts.index(peak) if peak > 0 else -1
    total = sum(counts)
    all_days = [c for w in weeks for c in w.get("days", []) if c is not None]
    thresholds = quantile_thresholds(all_days)
    landmark_names = "the London Eye, St Paul's Cathedral, the Gherkin, Tower Bridge and the Shard"

    if offline or not weeks:
        caption = OFFLINE_CAPTION
        desc = (
            f"Night skyline of London over the Thames Embankment for {login}. "
            f"Silhouettes of {landmark_names} stand behind 52 week buildings. "
            f"No contribution data yet: every building sits at the base height with dark windows. "
            f"{OFFLINE_CAPTION}."
        )
    else:
        start, end = weeks[0]["start"], weeks[-1]["end"]
        caption = f"{fmt_date(start)} to {fmt_date(end)}: {plural(total, 'contribution')} in {len(weeks)} weeks"
        desc = (
            f"Night skyline of London over the Thames Embankment for {login}. "
            f"Silhouettes of {landmark_names} stand behind {len(weeks)} buildings, one per week. "
            f"Height scales with the weekly contribution count. Seven windows per building show the days, "
            f"Sunday at the top, brighter for busier days. {caption}."
        )
        if peak > 0:
            desc += f" Peak week of {fmt_date(weeks[peak_index]['start'])}: {peak:,}."

    columns = []
    reflections = ([], [])
    for i, (week, h) in enumerate(zip(weeks, heights)):
        x = X0 + i * STEP
        y = GROUND - h
        parts = [f'<rect class="bd" x="{x}" y="{y}" width="{COL_W}" height="{h}"/>']
        band_top = GROUND - 6 - WIN_BAND
        levels = []
        for row, count in enumerate(week.get("days", [None] * DAYS)):
            lv = day_level(count, thresholds)
            levels.append(lv)
            cls = f"w{lv}"
            if lv >= 3 and (i + row) % 4 == 0:
                cls += " fl"
            parts.append(
                f'<rect class="{cls}" x="{x + 3}" y="{band_top + row * WIN_PITCH}" '
                f'width="{WIN_W}" height="{WIN_H}"/>'
            )
        if i == peak_index:
            parts.append(f'<rect class="pk" x="{x}" y="{y}" width="{COL_W}" height="2"/>')
        columns.append(f'<g class="b" style="animation-delay:{i * STAGGER_MS}ms">{"".join(parts)}</g>')
        glow = sum(levels) / (4 * DAYS)
        if glow > 0:
            reflections[i % 2].append(
                f'<rect class="rf" x="{x}" y="{RIVER_TOP + 2}" width="{COL_W}" '
                f'height="{max(8, round(h * 0.38))}" opacity="{0.15 + 0.6 * glow:.2f}"/>'
            )

    peak_label = ""
    if peak_index >= 0:
        px = X0 + peak_index * STEP + COL_W // 2
        peak_label = (f'<text class="pkt" x="{px}" y="{GROUND - heights[peak_index] - 6}" '
                      f'text-anchor="middle">{peak:,}</text>')

    right = WIDTH - X0
    river = (
        f'<rect class="water" x="0" y="{RIVER_TOP}" width="{WIDTH}" height="{RIVER_BOTTOM - RIVER_TOP}"/>'
        f'<g class="shim">{"".join(reflections[0])}</g>'
        f'<g class="shim s2">{"".join(reflections[1])}</g>'
        f'<path class="rip" d="M{X0} 360H{right}"/>'
        f'<path class="rip r2" d="M{X0 + 30} 372H{right - 30}"/>'
        f'<path class="rip" d="M{X0 + 70} 386H{right - 70}"/>'
        f'<path class="rip r2" d="M{X0 + 120} 398H{right - 120}"/>'
    )

    defs = (
        "<defs>"
        '<linearGradient id="sk-sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{T["indigo"]}"/>'
        f'<stop offset="0.5" stop-color="{T["border"]}"/>'
        f'<stop offset="0.82" stop-color="{T["accent"]}"/>'
        f'<stop offset="1" stop-color="{T["gold"]}"/>'
        "</linearGradient>"
        '<linearGradient id="sk-fade" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{T["window"]}" stop-opacity=".5"/>'
        f'<stop offset="1" stop-color="{T["window"]}" stop-opacity="0"/>'
        "</linearGradient>"
        f'<clipPath id="sk-road"><rect x="0" y="{GROUND}" width="{WIDTH}" height="{ROAD_BOTTOM - GROUND}"/></clipPath>'
        "</defs>"
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" '
        'role="img" aria-labelledby="sk-title sk-desc">'
        f'<title id="sk-title">Live from the Embankment: London skyline of GitHub contributions for {escape(login)}</title>'
        f'<desc id="sk-desc">{escape(desc)}</desc>'
        f"<style>{STYLE}</style>"
        f"{defs}"
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="6" fill="{T["canvas"]}"/>'
        f'<rect y="40" width="{WIDTH}" height="{GROUND - 40}" fill="url(#sk-sky)"/>'
        f"{stars()}{moon()}"
        f'<text class="t" x="{X0}" y="30">Live from the Embankment</text>'
        f'<text class="s" x="{right}" y="30" text-anchor="end">One building per week, one window per day</text>'
        f"{landmarks()}"
        f'<g>{"".join(columns)}</g>'
        f"{peak_label}"
        f"{traffic()}"
        f'<rect class="wall" x="-1" y="{ROAD_BOTTOM}" width="{WIDTH + 2}" height="{RIVER_TOP - ROAD_BOTTOM}"/>'
        f"{month_labels(weeks)}"
        f"{river}"
        f'<text class="c" x="{X0}" y="428">{escape(caption)}</text>'
        f"{legend(right, 428)}"
        "</svg>\n"
    )


# I/O ----------------------------------------------------------------------

def fetch_calendar(login: str, token: str, timeout: int = 30) -> dict:
    """Fetch the contribution calendar for `login` from the GitHub GraphQL API."""
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{login}-skyline-generator",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the London skyline contribution graph.")
    parser.add_argument("--login", default=DEFAULT_LOGIN, help="GitHub login to chart")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="SVG output path")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--offline", action="store_true", help="render a zero-data baseline")
    source.add_argument("--input", type=Path, help="render from a saved GraphQL response JSON file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.offline:
        svg = render_svg(offline_weeks(), login=args.login, offline=True)
    else:
        if args.input:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        else:
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            if not token:
                print("GITHUB_TOKEN is not set. Use --offline for a zero-data baseline.", file=sys.stderr)
                return 2
            try:
                payload = fetch_calendar(args.login, token)
            except urllib.error.URLError as exc:
                print(f"GitHub API request failed: {exc}", file=sys.stderr)
                return 1
        try:
            weeks = weeks_from_calendar(extract_calendar(payload))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        svg = render_svg(weeks, login=args.login)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output} ({len(svg.encode('utf-8')):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
