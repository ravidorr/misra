import json
import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright

import browser_crawl
import common
import run

# real shapes of Comeet feeds (the fields the adapter reads): see test_adapters.py
COMEET_FEEDS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "comeet_positions.json").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def no_inherited_git_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-push hook exports GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE. A test that runs git must never see them:
    with them set, `git init` or `git config` in a temporary directory would act on the real checkout."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def private_work_directory(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The descriptions work file lives in the runner's temporary directory: a test never touches the real one."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path_factory.mktemp("runner_temp")))


@pytest.fixture(autouse=True)
def close_connections(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The pipeline's main() functions never close their sqlite connections; close them at teardown."""
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: Any, **kw: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kw)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    yield
    for conn in opened:
        conn.close()


class NoNetwork:
    """The requests session of the tests: any request that reaches it is a bug in the test."""

    def get(self, url: str, **_kw: Any) -> None:
        raise AssertionError(f"a test reached the network: GET {url}")

    post = get


@pytest.fixture(autouse=True)
def offline_and_polite(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test touches the network or waits for the host delay; no host has a robots.txt unless a test says so."""
    monkeypatch.setattr(common, "_s", NoNetwork())
    monkeypatch.setattr(common, "DELAY", 0.0)
    monkeypatch.setattr(common, "_last", {})
    monkeypatch.setattr(common, "_load_robots", lambda _origin: None)
    monkeypatch.setattr(common, "_robots", {})
    monkeypatch.setattr(common, "_robots_locks", {})
    monkeypatch.setattr(common, "ROBOTS_WOULD_BLOCK", {})


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty project layout in tmp_path; cwd moved there so relative data/ paths never touch the real repo."""
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "templates").mkdir()
    (tmp_path / "data" / "excluded_companies.csv").write_text("company,reason,date_added\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    conn = run.init()  # the pull's output: a daily run never starts without a history file
    conn.execute(f"PRAGMA user_version = {common.PULLED}")
    conn.close()
    return tmp_path


@pytest.fixture
def db(workdir: Path) -> sqlite3.Connection:
    conn = run.init()
    return conn


def add_crawls(
    conn: sqlite3.Connection,
    company: str,
    rows: list[tuple[int, str | None]],
    ats: str = "browser",
    start_day: int = 1,
) -> None:
    """Crawl log rows for a company, oldest first, one per day from 2026-01-<start_day>: (ok, note)."""
    for day, (ok, note) in enumerate(rows, start=start_day):
        conn.execute(
            "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)",
            (f"2026-01-{day:02d}T06:00:00", company, ats, ok, 0 if not ok else 5, note),
        )
    conn.commit()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def add_job(conn: sqlite3.Connection, company: str, job_id: str, title: str, **kw: Any) -> None:
    """Insert a job row; keyword overrides: location, status, reported, first_seen, team, url."""
    row = {
        "location": "Tel Aviv, Israel",
        "status": "open",
        "reported": 0,
        "first_seen": "2024-01-02T00:00:00",
        "team": "T",
        "url": "https://x.test/j",
        **kw,
    }
    conn.execute(
        "INSERT INTO jobs(company,job_id,title,team,location,url,posted,first_seen,last_seen,status,reported)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            company,
            job_id,
            title,
            row["team"],
            row["location"],
            row["url"],
            "",
            row["first_seen"],
            row["first_seen"],
            row["status"],
            row["reported"],
        ),
    )
    conn.commit()


@pytest.fixture(scope="session")
async def browser() -> AsyncIterator[Browser]:
    """One Chromium for all the browser tests of a worker: starting Playwright and the browser for every test cost
    more than most of the tests."""
    async with async_playwright() as p:
        try:
            launch_args = {"args": ["--no-sandbox"]}
            if browser_crawl.EXE:
                launch_args["executable_path"] = browser_crawl.EXE
            chromium = await p.chromium.launch(**launch_args)  # type: ignore[arg-type]
        except Exception as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium to run the browser tests: {str(exc).splitlines()[0]}")
        try:
            yield chromium
        finally:
            await chromium.close()


@pytest.fixture
async def page(browser: Browser) -> AsyncIterator[Page]:
    """A new page in a context of its own: no cookies, storage or routes are shared between tests."""
    context = await browser.new_context()
    try:
        yield await context.new_page()
    finally:
        await context.close()
