#!/usr/bin/env python3
"""Sync the profile README with the public repositories of the owner.

Steps:
  1. List owner repositories from the GitHub REST API. Keep public repos
     that are neither forks nor archived, minus the EXCLUDE_REPOS names (comma separated, default empty).
  2. Download docs/cover-img.webp and docs/readme-cover-img.webp from each
     default branch into assets/covers/.
  3. Fetch the last 52 weeks of contributions and redraw the London skyline.
  4. Rewrite the showcase grid between the SHOWCASE markers in README.md.

Modes:
  default     call the GitHub API (needs PROFILE_STATS_TOKEN or GITHUB_TOKEN)
  --dry-run   call the API for reads only and print what would change
  --offline   read fixtures from tests/fixtures and make no network call

Needs the standard library and requests.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import generate_skyline as skyline  # noqa: E402

OWNER = "mdtarek-islam"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
ALWAYS_EXCLUDE = frozenset({OWNER})
EMBLEM_PATH = "docs/cover-img.webp"
BANNER_PATH = "docs/readme-cover-img.webp"
START_MARK = "<!-- SHOWCASE:START -->"
END_MARK = "<!-- SHOWCASE:END -->"
FIXTURES = ROOT / "tests" / "fixtures"
COLUMNS = 2


def excluded_names() -> frozenset:
    """Names to skip: the profile repo plus the comma separated EXCLUDE_REPOS variable."""
    extra = os.environ.get("EXCLUDE_REPOS", "")
    return ALWAYS_EXCLUDE | frozenset(n.strip() for n in extra.split(",") if n.strip())


def api_headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"{OWNER}-portfolio-sync"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# Repository data ----------------------------------------------------------

def fetch_repos(session, token: str | None, owner: str = OWNER) -> list[dict]:
    """Read every owner repository, page by page."""
    repos: list[dict] = []
    page = 1
    while True:
        response = session.get(
            f"{API}/users/{owner}/repos",
            params={"type": "owner", "per_page": 100, "page": page},
            headers=api_headers(token),
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        repos.extend(batch)
        if len(batch) < 100:
            return repos
        page += 1


def keep_repo(repo: dict, exclude: frozenset | None = None) -> bool:
    """Keep a public, active, original repository that is not excluded."""
    return (
        repo.get("visibility", "public") == "public"
        and not repo.get("private", False)
        and not repo.get("fork", False)
        and not repo.get("archived", False)
        and repo.get("name") not in (excluded_names() if exclude is None else exclude)
    )


def summarise(repo: dict) -> dict:
    return {
        "name": repo["name"],
        "url": repo.get("html_url") or f"https://github.com/{OWNER}/{repo['name']}",
        "description": (repo.get("description") or "").strip(),
        "language": repo.get("language") or "",
        "stars": int(repo.get("stargazers_count", 0)),
        "pushed_at": repo.get("pushed_at") or "",
        "branch": repo.get("default_branch") or "main",
    }


def select_repos(raw: list[dict], exclude: frozenset | None = None) -> list[dict]:
    """Filter and sort: most stars first, then name. The order never depends on time."""
    kept = [summarise(r) for r in raw if keep_repo(r, exclude)]
    return sorted(kept, key=lambda r: (-r["stars"], r["name"].lower()))


# Covers -------------------------------------------------------------------

def cover_targets(repo: dict, covers_dir: Path) -> list[tuple[str, Path]]:
    base = f"{RAW}/{OWNER}/{repo['name']}/{repo['branch']}"
    return [
        (f"{base}/{EMBLEM_PATH}", covers_dir / f"{repo['name']}.webp"),
        (f"{base}/{BANNER_PATH}", covers_dir / f"{repo['name']}-banner.webp"),
    ]


def download_covers(session, repos: list[dict], covers_dir: Path, dry_run: bool = False) -> list[str]:
    """Download each cover that exists. Return the files changed. A 404 leaves the old file alone."""
    written = []
    for repo in repos:
        for url, target in cover_targets(repo, covers_dir):
            response = session.get(url, timeout=30)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            if target.exists() and target.read_bytes() == response.content:
                continue
            written.append(target.name)
            if not dry_run:
                covers_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
    return written


# Showcase -----------------------------------------------------------------

def card(repo: dict, covers_dir: Path) -> str:
    name = html.escape(repo["name"])
    url = html.escape(repo["url"], quote=True)
    lines = []
    if (covers_dir / f"{repo['name']}.webp").exists():
        lines.append(
            f'<a href="{url}"><img src="./assets/covers/{name}.webp" width="100%" '
            f'alt="{name} emblem" /></a>'
        )
    lines.append(f'<h3><a href="{url}">{name}</a></h3>')
    if repo["description"]:
        lines.append(f"<p>{html.escape(repo['description'])}</p>")
    meta = [html.escape(repo["language"])] if repo["language"] else []
    meta.append("1 star" if repo["stars"] == 1 else f"{repo['stars']} stars")
    lines.append(f"<p><sub>{', '.join(meta)}</sub></p>")
    return "\n".join(lines)


def render_showcase(repos: list[dict], covers_dir: Path, columns: int = COLUMNS) -> str:
    if not repos:
        return "No public projects to show yet."
    width = f"{100 // columns}%"
    rows = []
    for i in range(0, len(repos), columns):
        cells = [
            f'<td width="{width}" valign="top">\n{card(r, covers_dir)}\n</td>'
            for r in repos[i:i + columns]
        ]
        cells += [f'<td width="{width}"></td>'] * (columns - len(cells))
        rows.append("<tr>\n" + "\n".join(cells) + "\n</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def replace_between_markers(text: str, body: str, start: str = START_MARK, end: str = END_MARK) -> str:
    a, b = text.find(start), text.find(end)
    if a < 0 or b < 0 or b < a:
        raise ValueError(f"README needs {start} and {end} in that order")
    return text[:a + len(start)] + "\n" + body + "\n" + text[b:]


# Skyline ------------------------------------------------------------------

def fetch_contributions(session, token: str, login: str = OWNER) -> dict:
    response = session.post(
        skyline.API_URL,
        json={"query": skyline.QUERY, "variables": {"login": login}},
        headers={"Authorization": f"bearer {token}", "User-Agent": f"{login}-portfolio-sync"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def build_skyline(payload: dict, login: str = OWNER) -> str:
    weeks = skyline.weeks_from_calendar(skyline.extract_calendar(payload))
    return skyline.render_svg(weeks, login=login, stats=skyline.extract_stats(payload, login))


# Orchestration ------------------------------------------------------------

def write_if_changed(path: Path, content: str, dry_run: bool) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return True


def run(args, session=None) -> int:
    root = args.root
    covers_dir = root / "assets" / "covers"
    stats_token, stats_source = skyline.pick_token()
    # GITHUB_TOKEN lists repositories. The stats token is the fallback.
    token = os.environ.get("GITHUB_TOKEN", "").strip() or stats_token

    covers: list[str] = []
    if args.offline:
        raw = json.loads((args.fixtures / "repos.json").read_text(encoding="utf-8"))
        payload = json.loads((args.fixtures / "calendar.json").read_text(encoding="utf-8"))
    else:
        if not token:
            print("PROFILE_STATS_TOKEN and GITHUB_TOKEN are not set. Use --offline for fixtures.", file=sys.stderr)
            return 2
        if session is None:
            import requests
            session = requests.Session()
        raw = fetch_repos(session, token)
        payload = fetch_contributions(session, stats_token)
    repos = select_repos(raw)
    print(skyline.describe_stats(stats_source if not args.offline else "fixtures",
                                 skyline.extract_stats(payload, OWNER)))
    if not args.offline:
        covers = download_covers(session, repos, covers_dir, dry_run=args.dry_run)

    changed = []
    if write_if_changed(root / "assets" / "london-skyline.svg", build_skyline(payload), args.dry_run):
        changed.append("assets/london-skyline.svg")
    readme = root / "README.md"
    new_readme = replace_between_markers(readme.read_text(encoding="utf-8"), render_showcase(repos, covers_dir))
    if write_if_changed(readme, new_readme, args.dry_run):
        changed.append("README.md")
    changed += [f"assets/covers/{name}" for name in covers]

    verb = "Would change" if args.dry_run else "Changed"
    print(f"{verb}: {', '.join(changed) if changed else 'nothing'}")
    print(f"Showcase repositories: {len(repos)}")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync the profile README with public repositories.")
    parser.add_argument("--dry-run", action="store_true", help="read from the API and write nothing")
    parser.add_argument("--offline", action="store_true", help="use fixtures and make no network call")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="fixture directory for --offline")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to update")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
