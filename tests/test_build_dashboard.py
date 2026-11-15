import base64
import hashlib
import json
import re
import runpy
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import build_dashboard
from tests.conftest import add_job, write_json


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("HTTP 403, title='x'", "its site blocks automated access from GitHub's servers"),
        ("blocked page", "its site blocks automated access from GitHub's servers"),
        ("x" * 200, "x" * 120),
        ("", "the last crawl failed"),
        (None, "the last crawl failed"),
    ],
)
def test_reason(note: str | None, expected: str) -> None:
    assert build_dashboard.reason(note) == expected


def log(conn: sqlite3.Connection, ts: str, company: str, ok: int, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)", (ts, company, "x", ok, 0, note)
    )
    conn.commit()


def write_css(workdir: Path) -> None:
    (workdir / "design-system" / "components").mkdir(parents=True)
    (workdir / "design-system" / "tokens.css").write_text(":root{--a:1}\n", encoding="utf-8")
    (workdir / "design-system" / "components" / "b.css").write_text(".b{}\n", encoding="utf-8")
    (workdir / "design-system" / "components" / "a.css").write_text(".a{}\n", encoding="utf-8")
    (workdir / "templates" / "dashboard.css").write_text("body{}\n", encoding="utf-8")


def write_unresolved(workdir: Path, *rows: tuple[str, str, str]) -> None:
    """data/unresolved_candidates.csv with the given (name, reason, note) rows."""
    lines = ["name,domain,segment,reason,note"] + [f'{n},{n.lower()}.test,deeptech,{r},"{note}"' for n, r, note in rows]
    (workdir / "data" / "unresolved_candidates.csv").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def prepare(workdir: Path) -> None:
    write_json(workdir / "data" / "companies.json", [{"name": "Zed", "status": "ok"}, {"name": "Alpha", "status": "x"}])
    write_unresolved(workdir, ("Beta", "no_openings", "No open roles"))
    write_static(workdir)


def write_static(workdir: Path) -> None:
    (workdir / "templates" / "dashboard.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    (workdir / "templates" / "dashboard.js").write_text("const dashboard = 1;\n", encoding="utf-8")
    (workdir / "templates" / "theme.js").write_text("const theme = 1;\n", encoding="utf-8")
    (workdir / "templates" / "auth.js").write_text("const auth = '__SUPABASE_URL__';\n", encoding="utf-8")
    (workdir / "templates" / "email.html").write_text("<!doctype html><title>e</title>", encoding="utf-8")
    (workdir / "templates" / "email.js").write_text("const email = '__SUPABASE_URL__';\n", encoding="utf-8")
    (workdir / "vendor").mkdir(exist_ok=True)
    (workdir / "vendor" / "supabase.js").write_bytes(b"var supabase = '__SUPABASE_URL__'; \xc3\xa9\r\n")


def read_payload(workdir: Path) -> Any:
    return json.loads((workdir / "docs" / "jobs.json").read_text(encoding="utf-8"))


def test_main_builds_dashboard(db: sqlite3.Connection, workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prepare(workdir)
    log(db, "2024-01-01", "Down", 1)
    log(db, "2024-01-02", "Down", 0, "HTTP 403")
    log(db, "2024-01-03", "Down", 0, None)
    log(db, "2024-01-01", "Flaky", 1)
    log(db, "2024-01-02", "Flaky", 0, "oops")
    log(db, "2024-01-01", "Never", 0, "x")
    log(db, "2024-01-02", "Never", 0, "x")
    add_job(db, "Acme", "1", "Product Manager </b>")
    add_job(db, "Acme", "2", "Product Intern", first_seen="2024-02-01")
    add_job(db, "Acme", "3", "Student", location=None, first_seen=None, team=None)
    add_job(db, "Acme", "4", "Accountant", location="London " + "y" * 100)
    add_job(db, "Acme", "5", "Closed Product", status="closed")
    add_job(db, "Down", "6", "Junior Dev")
    add_job(db, "Acme", "7", "Junior Intern")
    add_job(db, "Acme", "8", "Graduate Program")
    add_job(db, "Acme", "9", "Junior Product Manager")
    db.close()
    build_dashboard.main()
    payload = read_payload(workdir)
    gaps = payload["gaps"]
    assert gaps["untracked"] == ["Alpha"]
    assert [g["c"] for g in gaps["blocked"]] == ["Down", "Never"]
    assert gaps["blocked"][0]["last"] == "2024-01-01"
    assert gaps["blocked"][0]["why"].startswith("the last crawl failed")
    assert gaps["blocked"][1]["last"] == "never"
    # Down (a failing crawl), Never (the same), Alpha (no feed found) and Beta (from the CSV), grouped by reason
    assert gaps["total"] == len(["Down", "Never", "Alpha", "Beta"])
    assert [(g["reason"], g["count"]) for g in gaps["groups"]] == [
        ("crawl_failing", 2),
        ("no_openings", 1),
        ("no_feed_found", 1),
    ]
    jobs = {j["i"]: j for j in payload["jobs"]}
    assert sorted(jobs) == ["1", "2", "3", "4", "6", "7", "8", "9"]
    # level and role type are separate, from classify.py: Product no longer swallows the level
    assert [(jobs[i]["lv"], jobs[i]["rt"]) for i in ("1", "2", "3", "4", "6", "7", "8", "9")] == [
        ("Not specified", ["Product"]),
        ("Intern", ["Product"]),
        ("Intern", ["Other"]),
        ("Not specified", ["Finance"]),
        ("Junior", ["R&D"]),
        ("Intern", ["Other"]),
        ("Junior", ["Other"]),
        ("Junior", ["Product"]),
    ]
    assert jobs["1"]["rg"] == ["Tel Aviv area"]
    assert jobs["1"]["lo"] == []
    assert jobs["1"]["il"] is True
    assert jobs["3"]["il"] is False
    assert jobs["3"]["f"] == ""
    assert len(jobs["4"]["l"]) == len("London " + "y" * 100) - 27
    assert jobs["1"]["n"] is False
    assert jobs["2"]["n"] is True
    assert jobs["6"]["s"] is True
    assert "failing" in capsys.readouterr().out


def test_old_schema_without_note_column_runs_as_script(workdir: Path) -> None:
    prepare(workdir)
    conn = sqlite3.connect(build_dashboard.DB)
    conn.executescript(
        """
        CREATE TABLE jobs(company, job_id, title, team, location, url, first_seen, status);
        CREATE TABLE crawl_log(ts, company, ats, ok, n_jobs);
        INSERT INTO crawl_log VALUES('2024-01-01','Old','x',0,0);
        INSERT INTO crawl_log VALUES('2024-01-02','Old','x',0,0);
        """
    )
    conn.commit()
    conn.close()
    runpy.run_module("build_dashboard", run_name="__main__")
    payload = read_payload(workdir)
    assert payload["gaps"]["blocked"] == [{"c": "Old", "why": "the last crawl failed", "last": "never"}]
    assert payload["jobs"] == []


def test_read_css_bundles_sources_in_order(workdir: Path) -> None:
    write_css(workdir)
    assert build_dashboard.read_css() == ":root{--a:1}\n.a{}\n.b{}\nbody{}"


JOB = {
    "c": "Acme",
    "t": "PM",
    "u": "https://x.test/1",
    "l": "Tel Aviv",
    "f": "2024-01-02",
    "il": True,
    "lv": "Not specified",
    "rt": ["Product"],
    "rg": ["Tel Aviv area"],
    "lo": [],
}


def test_job_posting() -> None:
    job = JOB
    posting = build_dashboard.job_posting(job)
    assert posting["title"] == "PM"
    assert posting["hiringOrganization"]["name"] == "Acme"
    assert posting["datePosted"] == "2024-01-02"
    assert posting["jobLocation"] == [
        {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressRegion": "Tel Aviv area", "addressCountry": "IL"},
        }
    ]
    abroad = build_dashboard.job_posting({**job, "f": "", "il": False, "rg": [], "l": "London"})
    assert "datePosted" not in abroad
    assert abroad["jobLocation"]["address"] == {"@type": "PostalAddress", "addressLocality": "London"}


def test_job_posting_says_the_level_the_role_type_and_every_region() -> None:
    """FR-9 AC 1 and FR-3 AC 10 for crawlers and assistants reading jobs.jsonld without running the page."""
    posting = build_dashboard.job_posting(
        {**JOB, "lv": "Intern", "rt": ["Product", "Design"], "rg": ["Tel Aviv area", "Sharon", "Remote"]}
    )
    assert posting["experienceRequirements"] == "Intern"
    assert posting["employmentType"] == "INTERN"
    assert posting["occupationalCategory"] == ["Product", "Design"]
    assert [p["address"]["addressRegion"] for p in posting["jobLocation"]] == ["Tel Aviv area", "Sharon"]
    assert posting["jobLocationType"] == "TELECOMMUTE"
    junior = build_dashboard.job_posting({**JOB, "lv": "Junior", "rt": ["Other"]})
    assert "employmentType" not in junior
    assert junior["experienceRequirements"] == "Junior"
    assert junior["occupationalCategory"] == ["Other"]
    # a place with no region keeps its own string as the region; a remote role alone keeps the location it was given
    ungrouped = build_dashboard.job_posting({**JOB, "rg": [], "lo": ["Kfar Foo, Israel"]})
    assert ungrouped["jobLocation"][0]["address"]["addressLocality"] == "Kfar Foo, Israel"
    both = build_dashboard.job_posting({**JOB, "rg": ["Sharon"], "lo": ["Kfar Foo, Israel", "Israel, city not given"]})
    assert [(p["address"].get("addressRegion"), p["address"].get("addressLocality")) for p in both["jobLocation"]] == [
        ("Sharon", None),
        (None, "Kfar Foo, Israel"),
    ]
    remote = build_dashboard.job_posting({**JOB, "rg": ["Remote"], "l": "Remote, Israel"})
    assert remote["jobLocation"]["address"]["addressLocality"] == "Remote, Israel"
    assert remote["jobLocationType"] == "TELECOMMUTE"
    assert remote["applicantLocationRequirements"] == {"@type": "Country", "name": "IL"}


def test_site_files_are_written_next_to_the_page(db: sqlite3.Connection, workdir: Path) -> None:
    write_css(workdir)
    write_static(workdir)
    write_json(workdir / "data" / "companies.json", [])
    write_unresolved(workdir)
    add_job(db, "Acme", "1", "Product Manager </script> é")
    add_job(db, "Acme", "2", "Accountant", location="London")
    db.close()
    build_dashboard.main()
    docs = workdir / "docs"
    assert sorted(p.name for p in docs.iterdir()) == [
        "auth.js",
        "dashboard.css",
        "dashboard.js",
        "email.html",
        "email.js",
        "index.html",
        "jobs.json",
        "jobs.jsonld",
        "supabase.js",
        "theme.js",
    ]
    assert (docs / "index.html").read_text(encoding="utf-8") == "<!doctype html><title>x</title>"
    assert (docs / "dashboard.js").read_text(encoding="utf-8") == "const dashboard = 1;\n"
    assert (docs / "theme.js").read_text(encoding="utf-8") == "const theme = 1;\n"
    assert (docs / "email.html").read_text(encoding="utf-8") == "<!doctype html><title>e</title>"
    assert (docs / "email.js").read_text(encoding="utf-8") == f"const email = '{build_dashboard.SUPABASE_URL}';\n"
    assert (docs / "auth.js").read_text(encoding="utf-8") == f"const auth = '{build_dashboard.SUPABASE_URL}';\n"
    # a vendored script is copied byte for byte: nothing is injected into it and its line endings stay
    assert (docs / "supabase.js").read_bytes() == b"var supabase = '__SUPABASE_URL__'; \xc3\xa9\r\n"
    assert (docs / "dashboard.css").read_text(encoding="utf-8").endswith("body{}")
    listing = json.loads((docs / "jobs.jsonld").read_text(encoding="utf-8"))
    assert [i["item"]["title"] for i in listing["itemListElement"]] == ["Product Manager </script> é"]
    assert listing["itemListElement"][0]["position"] == 1
    assert "é" in (docs / "jobs.json").read_text(encoding="utf-8")


def test_page_gets_integrity_hashes_of_its_files(db: sqlite3.Connection, workdir: Path) -> None:
    write_css(workdir)
    write_static(workdir)
    write_json(workdir / "data" / "companies.json", [])
    write_unresolved(workdir)
    (workdir / "templates" / "dashboard.html").write_text(
        '<link integrity="__SRI_DASHBOARD_CSS__"><script integrity="__SRI_THEME_JS__"></script>'
        '<script integrity="__SRI_DASHBOARD_JS__"></script><script integrity="__SRI_AUTH_JS__"></script>'
        '<script integrity="__SRI_SUPABASE_JS__"></script>',
        encoding="utf-8",
    )
    db.close()
    build_dashboard.main()
    docs = workdir / "docs"
    html = (docs / "index.html").read_text(encoding="utf-8")

    def expected(name: str) -> str:
        return "sha384-" + base64.b64encode(hashlib.sha384((docs / name).read_bytes()).digest()).decode()

    assert html == (
        f'<link integrity="{expected("dashboard.css")}"><script integrity="{expected("theme.js")}"></script>'
        f'<script integrity="{expected("dashboard.js")}"></script><script integrity="{expected("auth.js")}"></script>'
        f'<script integrity="{expected("supabase.js")}"></script>'
    )
    assert "__SRI" not in html
    (workdir / "templates" / "email.html").write_text(
        '<link integrity="__SRI_DASHBOARD_CSS__"><script integrity="__SRI_EMAIL_JS__"></script>', encoding="utf-8"
    )
    build_dashboard.main()
    email = (docs / "email.html").read_text(encoding="utf-8")
    assert (
        email == f'<link integrity="{expected("dashboard.css")}"><script integrity="{expected("email.js")}"></script>'
    )


COMPANIES = [
    {"name": "Page", "status": "careers_page_only"},
    {"name": "Nowhere", "status": "not_found"},
    {"name": "Odd", "status": "something_new"},
]


def test_gap_groups_follow_the_reasons_in_order_and_count_each_site_once() -> None:
    blocked = [
        {"c": "Walled", "why": build_dashboard.BLOCKED_WHY, "last": "2026-01-02"},
        {"c": "Down", "why": "the last crawl failed", "last": "never"},
        {"c": "Page", "why": "HTTP 500", "last": "never"},  # also untracked: counted as failing only
    ]
    unresolved = [
        {"name": "Zeta", "reason": "needs_reader", "note": "Avature board"},
        {"name": "alef", "reason": "needs_reader", "note": ""},
        {"name": "Nowhere", "reason": "bot_protection", "note": "researched: wins over the bare status"},
        {"name": "Walled", "reason": "no_openings", "note": "ignored: the crawl is failing"},
    ]
    groups = build_dashboard.gap_groups(blocked, COMPANIES, unresolved)
    assert [(g["reason"], g["count"]) for g in groups] == [
        ("crawl_failing", 2),
        ("bot_protection", 2),
        ("needs_reader", 2),
        ("no_feed_found", 1),
    ]
    assert all(g["label"] == build_dashboard.REASONS[g["reason"]] for g in groups)
    by_reason = {g["reason"]: g["sites"] for g in groups}
    assert [s["c"] for s in by_reason["crawl_failing"]] == ["Down", "Page"]  # sorted by name
    assert by_reason["crawl_failing"][0]["why"] == "The last crawl failed; its roles may be old (last good crawl never)"
    assert by_reason["bot_protection"] == [
        {"c": "Nowhere", "why": "researched: wins over the bare status"},
        {
            "c": "Walled",
            "why": f"Its {build_dashboard.BLOCKED_WHY[4:]}; its roles may be old (last good crawl 2026-01-02)",
        },
    ]
    assert by_reason["needs_reader"] == [{"c": "alef", "why": ""}, {"c": "Zeta", "why": "Avature board"}]  # any case
    assert by_reason["no_feed_found"] == [{"c": "Odd", "why": ""}]


def test_gap_groups_leave_out_a_reason_with_no_site() -> None:
    assert build_dashboard.gap_groups([], [], []) == []


def test_read_unresolved_returns_the_rows(workdir: Path) -> None:
    write_unresolved(workdir, ("Beta", "no_openings", "No open roles, none at all"))
    assert build_dashboard.read_unresolved() == [
        {
            "name": "Beta",
            "domain": "beta.test",
            "segment": "deeptech",
            "reason": "no_openings",
            "note": "No open roles, none at all",
        }
    ]


def test_read_unresolved_rejects_a_reason_that_is_not_known(workdir: Path) -> None:
    write_unresolved(workdir, ("Beta", "because", ""))
    with pytest.raises(ValueError, match="Beta has the unknown reason 'because'"):
        build_dashboard.read_unresolved()


def test_read_unresolved_needs_the_reason_and_note_columns(workdir: Path) -> None:
    (workdir / "data" / "unresolved_candidates.csv").write_text(
        "name,domain,segment\r\nBeta,b.test,x\r\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no note, reason column"):
        build_dashboard.read_unresolved()


def test_read_unresolved_reads_a_short_row_as_an_empty_note(workdir: Path) -> None:
    (workdir / "data" / "unresolved_candidates.csv").write_text(
        "name,domain,segment,reason,note\r\nBeta,b.test,x,no_openings\r\n", encoding="utf-8"
    )
    assert [r["note"] for r in build_dashboard.read_unresolved()] == [""]


def test_the_real_unresolved_csv_has_only_known_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    assert build_dashboard.read_unresolved()


def test_the_backend_settings_and_page_data_are_injected_into_the_page(db: sqlite3.Connection, workdir: Path) -> None:
    write_css(workdir)
    write_static(workdir)
    write_json(workdir / "data" / "companies.json", [{"name": "A</script>", "status": "x"}])
    write_unresolved(workdir)
    (workdir / "templates" / "dashboard.html").write_text(
        "connect-src __SUPABASE_URL__<script id=d>__PAGE_DATA__</script>", encoding="utf-8"
    )
    (workdir / "templates" / "dashboard.js").write_text(
        "const url = '__SUPABASE_URL__'; const key = '__SUPABASE_ANON_KEY__';", encoding="utf-8"
    )
    db.close()
    build_dashboard.main()
    docs = workdir / "docs"
    page = (docs / "index.html").read_text(encoding="utf-8")
    assert page.startswith(f"connect-src {build_dashboard.SUPABASE_URL}<script id=d>")
    assert (docs / "dashboard.js").read_text(encoding="utf-8") == (
        f"const url = '{build_dashboard.SUPABASE_URL}'; const key = '{build_dashboard.SUPABASE_ANON_KEY}';"
    )
    block = page.split("<script id=d>")[1].split("</script>")[0]
    assert "<" not in block  # the data cannot close its own block
    data = json.loads(block)
    assert data["gaps"]["untracked"] == ["A</script>"]
    assert data["generated"].endswith(" UTC")


def test_the_vendored_supabase_script_is_the_pinned_release_unmodified() -> None:
    """vendor/README.md names the version and the SHA-256 of the file: an edited or swapped copy fails here."""
    vendor = Path(__file__).resolve().parents[1] / "vendor"
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    row = re.search(
        r"\| `supabase\.js` \| `@supabase/supabase-js` \(MIT\) \| (\d+\.\d+\.\d+) \| `([0-9a-f]{64})` \|", readme
    )
    assert row
    assert hashlib.sha256((vendor / "supabase.js").read_bytes()).hexdigest() == row[2]
    assert build_dashboard.VENDORED_FILES == ("vendor/supabase.js",)
    assert "eval(" not in (vendor / "supabase.js").read_text(encoding="utf-8")  # the page forbids unsafe-eval


def test_every_file_the_build_writes_to_docs_is_staged_by_the_daily_workflow(
    db: sqlite3.Connection, workdir: Path
) -> None:
    """A generated file the daily workflow does not `git add` is never published."""
    write_css(workdir)
    write_static(workdir)
    write_json(workdir / "data" / "companies.json", [])
    write_unresolved(workdir)
    db.close()
    build_dashboard.main()
    built = {f"docs/{p.name}" for p in (workdir / "docs").iterdir()}
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    staged = set(re.search(r"for f in (data/jobs\.db [^;]+); do", workflow)[1].split()) - {"data/jobs.db"}  # type: ignore[index]
    assert built == staged
