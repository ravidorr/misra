import json
import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, async_playwright

import browser_crawl
import run


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


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty project layout in tmp_path; cwd moved there so relative data/ paths never touch the real repo."""
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "templates").mkdir()
    monkeypatch.chdir(tmp_path)
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


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    async with async_playwright() as p:
        try:
            launch_args = {"args": ["--no-sandbox"]}
            if browser_crawl.EXE:
                launch_args["executable_path"] = browser_crawl.EXE
            browser = await p.chromium.launch(**launch_args)  # type: ignore[arg-type]
        except Exception as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium to run the browser tests: {str(exc).splitlines()[0]}")
        try:
            yield await browser.new_page()
        finally:
            await browser.close()
