"""Offline tests for scripts/portfolio_sync.py. No network access."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import portfolio_sync as ps  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
RAW = json.loads((FIXTURES / "repos.json").read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status=200, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, missing=()):
        self.urls, self.missing = [], missing

    def get(self, url, **kwargs):
        self.urls.append(url)
        if any(url.endswith(m) for m in self.missing):
            return FakeResponse(404)
        return FakeResponse(content=b"webp:" + url.encode())


@pytest.fixture
def site(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "README.md").write_text(
        f"# Hi\n\n{ps.START_MARK}\nold\n{ps.END_MARK}\n\nTail\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def exclude_fixture(monkeypatch):
    monkeypatch.setenv("EXCLUDE_REPOS", "excluded-fixture")


def names(raw, exclude=None):
    return [r["name"] for r in ps.select_repos(raw, exclude or frozenset({"excluded-fixture"}))]


def test_filter_drops_private_fork_archived_and_excluded():
    assert names(RAW) == ["alpha-demo", "beta-demo", "gamma-demo"]
    assert ps.OWNER in ps.excluded_names()


def test_env_var_feeds_exclude_list(monkeypatch):
    monkeypatch.setenv("EXCLUDE_REPOS", " a , b ,")
    assert {"a", "b", ps.OWNER} == set(ps.excluded_names())
    monkeypatch.delenv("EXCLUDE_REPOS")
    assert ps.excluded_names() == ps.ALWAYS_EXCLUDE


def test_sort_is_deterministic():
    assert names(RAW) == names(list(reversed(RAW)))


def test_cover_targets_use_default_branch():
    repo = ps.select_repos(RAW, frozenset({"excluded-fixture"}))
    gamma = next(r for r in repo if r["name"] == "gamma-demo")
    urls = [u for u, _ in ps.cover_targets(gamma, Path("c"))]
    assert urls[0].endswith("/gamma-demo/trunk/docs/cover-img.webp")
    assert urls[1].endswith("/gamma-demo/trunk/docs/readme-cover-img.webp")


def test_download_skips_404_and_writes_found(tmp_path):
    repos = ps.select_repos(RAW, frozenset({"excluded-fixture"}))
    session = FakeSession(missing=("readme-cover-img.webp",))
    written = ps.download_covers(session, repos, tmp_path)
    assert (tmp_path / "alpha-demo.webp").exists()
    assert not (tmp_path / "alpha-demo-banner.webp").exists()
    assert len(written) == 3


def test_download_dry_run_writes_nothing(tmp_path):
    ps.download_covers(FakeSession(), ps.select_repos(RAW, frozenset()), tmp_path, dry_run=True)
    assert list(tmp_path.iterdir()) == []


def test_showcase_escapes_and_links(tmp_path):
    (tmp_path / "alpha-demo.webp").write_bytes(b"x")
    grid = ps.render_showcase(ps.select_repos(RAW, frozenset({"excluded-fixture"})), tmp_path)
    assert "&lt;b&gt;markup&lt;/b&gt;" in grid
    assert "./assets/covers/alpha-demo.webp" in grid
    assert "gamma-demo.webp" not in grid
    ET.fromstring(grid)


def test_replace_between_markers_keeps_outside_text():
    text = f"a\n{ps.START_MARK}\nold\n{ps.END_MARK}\nb"
    assert ps.replace_between_markers(text, "new") == f"a\n{ps.START_MARK}\nnew\n{ps.END_MARK}\nb"
    with pytest.raises(ValueError):
        ps.replace_between_markers("no markers", "new")


def test_offline_run_updates_readme_and_skyline(site, exclude_fixture):
    assert ps.run(ps.parse_args(["--offline", "--root", str(site)])) == 0
    readme = (site / "README.md").read_text(encoding="utf-8")
    assert "alpha-demo" in readme and "excluded-fixture" not in readme and "Tail" in readme
    svg = (site / "assets" / "london-skyline.svg").read_text(encoding="utf-8")
    ET.fromstring(svg)
    assert svg.count('class="b"') == 52


def test_skyline_height_follows_weekly_total(site, exclude_fixture):
    ps.run(ps.parse_args(["--offline", "--root", str(site)]))
    svg = (site / "assets" / "london-skyline.svg").read_text(encoding="utf-8")
    heights = [int(h) for h in __import__("re").findall(r'<rect class="bd"[^>]*height="(\d+)"', svg)]
    assert max(heights) == ps.skyline.MAX_H
    assert ps.skyline.MIN_H <= min(heights) < max(heights)


def test_offline_run_is_idempotent(site, exclude_fixture):
    args = ps.parse_args(["--offline", "--root", str(site)])
    ps.run(args)
    first = (site / "README.md").read_bytes(), (site / "assets" / "london-skyline.svg").read_bytes()
    ps.run(args)
    assert first == ((site / "README.md").read_bytes(), (site / "assets" / "london-skyline.svg").read_bytes())


def test_dry_run_writes_nothing(site, exclude_fixture, capsys):
    before = (site / "README.md").read_text(encoding="utf-8")
    assert ps.run(ps.parse_args(["--offline", "--dry-run", "--root", str(site)])) == 0
    assert (site / "README.md").read_text(encoding="utf-8") == before
    assert not (site / "assets" / "london-skyline.svg").exists()
    assert "Would change" in capsys.readouterr().out


def test_missing_token_fails_cleanly(site, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert ps.run(ps.parse_args(["--root", str(site)])) == 2


class FakePost:
    """Session stub that records the GraphQL call and returns the calendar fixture."""

    def __init__(self, status=200):
        self.headers, self.status = None, status

    def post(self, url, json=None, headers=None, timeout=None):
        self.headers = headers
        payload = __import__("json").loads((FIXTURES / "calendar.json").read_text(encoding="utf-8"))
        return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: payload})()


def test_contributions_use_profile_token_first(monkeypatch):
    session = FakePost()
    monkeypatch.setenv("PROFILE_STATS_TOKEN", "profile-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "actions-secret")
    token, source = ps.skyline.pick_token()
    ps.fetch_contributions(session, token)
    assert source == "PROFILE_STATS_TOKEN"
    assert session.headers["Authorization"] == "bearer profile-secret"


def test_run_logs_source_name_and_visibility_never_the_value(site, monkeypatch, capsys):
    monkeypatch.setenv("PROFILE_STATS_TOKEN", "profile-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "actions-secret")
    monkeypatch.setattr(ps, "fetch_repos", lambda session, token: RAW)
    monkeypatch.setattr(ps, "download_covers", lambda *a, **k: [])
    assert ps.run(ps.parse_args(["--dry-run", "--root", str(site)]), session=FakePost()) == 0
    out = capsys.readouterr().out
    assert "Token source: PROFILE_STATS_TOKEN" in out
    assert "Private contributions visible: yes" in out
    assert "secret" not in out


def test_offline_skyline_carries_the_breakdown(site, exclude_fixture):
    ps.run(ps.parse_args(["--offline", "--root", str(site)]))
    svg = (site / "assets" / "london-skyline.svg").read_text(encoding="utf-8")
    assert "Code reviews" in svg and "includes private contributions" in svg
