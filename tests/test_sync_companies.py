"""The company list in the sync (PLAN_2.md step 3.2): the pull writes data/companies.json from the table, and
--push-companies sends back the discovered fields of the records that discovery changed, and nothing else."""

import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

import build_dashboard
import common
import discover
import export_accounts
import run
import sync_supabase
from common import ExclusionError, load_excluded
from sync_supabase import SyncError
from tests.conftest import excluded_record, write_json
from tests.fakes import FakeHttpResponse
from tests.test_build_dashboard import prepare, read_payload
from tests.test_sync_supabase import KEY, URL, company_row, job_row
from tests.test_sync_supabase import Pages as HistoryPages

SECRET_TEXT = "a secret cell"
COUNT = 4  # rows of the first test


class Companies:
    """PostgREST for the `companies` table: ordered pages with a Content-Range total, and a PATCH that applies the
    filters of the request (name, crawl, excluded) to the rows and answers the rows it changed."""

    def __init__(self, rows: list[dict[str, Any]], fail_patch_of: str | None = None) -> None:
        self.rows = rows
        self.fail_patch_of = fail_patch_of
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kw: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kw})
        assert url.endswith("/rest/v1/companies"), url
        params = kw["params"]
        if method == "GET":
            window = [dict(row) for row in self.rows[params["offset"] : params["offset"] + params["limit"]]]  # copies
            answer = FakeHttpResponse(data=window)
            answer.headers = {"Content-Range": f"0-{len(window)}/{len(self.rows)}"}  # type: ignore[attr-defined]
            return answer
        assert method == "PATCH"
        name = params["name"].removeprefix("eq.")
        if name == self.fail_patch_of:
            return FakeHttpResponse(status_code=400, text=f"violates a check, Failing row contains ({SECRET_TEXT})")
        hit = [row for row in self.rows if all(matches(row, k, v) for k, v in params.items() if k != "select")]
        for row in hit:
            row.update(kw["json"])
        return FakeHttpResponse(data=[{"name": row["name"]} for row in hit])

    def patches(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["method"] == "PATCH"]


def matches(row: dict[str, Any], column: str, condition: str) -> bool:
    """One PostgREST filter, as the database reads it: `is.null`, or `eq.<value>` (`true` for a boolean; null is
    never equal to anything, an empty string equals `eq.`)."""
    if condition == "is.null":
        return row[column] is None
    value = condition.removeprefix("eq.")
    return row[column] is True if value == "true" and isinstance(row[column], bool) else row[column] == value


def source(fake: Any) -> export_accounts.Source:
    return export_accounts.Source(fake, URL, KEY)


def client(fake: Any) -> sync_supabase.Client:
    return sync_supabase.Client(fake, URL, KEY)


def found(name: str, **more: Any) -> dict[str, Any]:
    """A record of the file after discover.py: the discovered fields set."""
    base = {"name": name, "crawl": True, "careers_url": "", "ats": "lever", "slug": name.lower(), "source": "page"}
    return {**base, "status": "ok", **more}


def push(
    rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    tmp_path: Path,
    edit: Callable[[list[dict[str, Any]]], None] | None = None,
    **kw: Any,
) -> tuple[Any, Any]:
    """The discover-companies run in miniature: pull the table into the file (and the baseline), let discovery write
    `records` over the file, let `edit` change the table as an editor would, then push."""
    fake = Companies(rows, **kw)
    path = tmp_path / "companies.json"
    sync_supabase.pull_companies(source(fake), str(path))
    write_json(path, records)
    if edit:
        edit(rows)
    return sync_supabase.push_companies(client(fake), str(path)), fake


# ---- the pull ----


def test_the_pull_writes_the_list_sorted_in_the_format_of_discover_without_the_keys_a_record_lacks(
    tmp_path: Path,
) -> None:
    rows = [
        company_row("beta", slug="b", ats="lever", source="page", careers_url="", israel_only=True),
        company_row("Alpha", gap=None, links={"global": "https://alpha.test/jobs"}, segment="cyber_infra"),
        company_row("Émile", crawl=False, gap={"reason": "no_openings", "note": "à voir"}, segment="deeptech"),
        {**dict.fromkeys(common.RECORD_KEYS), **excluded_record("Gone"), "crawl": False},
    ]
    path = tmp_path / "companies.json"
    assert sync_supabase.pull_companies(source(CompanyPages(rows)), str(path)) == COUNT
    records = json.loads(path.read_text(encoding="utf-8"))
    assert [r["name"] for r in records] == ["Alpha", "beta", "Gone", "Émile"]  # by name, ignoring case
    assert path.read_text(encoding="utf-8") == json.dumps(records, indent=1, ensure_ascii=False) + "\n"
    assert "à voir" in path.read_text(encoding="utf-8")  # not escaped
    for record in records:
        assert list(record) == [k for k in common.RECORD_KEYS if k in record]
        assert None not in record.values()
    assert records[2] == excluded_record("Gone")  # an excluded record carries the name, crawl and the exclusion only
    assert load_excluded(str(path)) == ["Gone"]
    baseline = json.loads((tmp_path / "companies.pulled.json").read_text(encoding="utf-8"))
    assert baseline[1] == {"name": "beta", "domain": "beta.test", "crawl": True, "careers_url": "", "ats": "lever"} | {
        "slug": "b",
        "source": "page",
        "status": "ok",
        "israel_only": True,
    }  # the table as it is: a null is a key left out
    assert not (tmp_path / "companies.json.part").exists()


class CompanyPages:
    """The same PostgREST read for any rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def request(self, method: str, url: str, **kw: Any) -> Any:
        return Companies(self.rows).request(method, url, **kw)


def test_the_pull_creates_the_data_directory_a_fresh_checkout_does_not_have(tmp_path: Path) -> None:
    path = tmp_path / "data" / "companies.json"
    assert sync_supabase.pull_companies(source(CompanyPages([company_row("Acme")])), str(path)) == 1
    assert path.exists()
    db = tmp_path / "fresh" / "jobs.db"
    tables = {"jobs": [job_row("Acme", "1")], "crawl_log": []}
    sync_supabase.pull(source(HistoryPages(tables)), str(db), allow_shrink=True)
    assert db.exists()


def test_the_pull_reads_every_column_and_all_the_rows_the_key_sees(tmp_path: Path) -> None:
    fake = Companies([company_row(f"C{n:03d}") for n in range(sync_supabase.PAGE + 5)])
    assert sync_supabase.pull_companies(source(fake), str(tmp_path / "c.json")) == sync_supabase.PAGE + 5
    assert fake.calls[0]["params"]["select"] == ",".join(common.RECORD_KEYS)
    assert fake.calls[0]["headers"]["apikey"] == KEY


def test_an_empty_table_is_refused_and_the_old_file_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    path.write_text("previous", encoding="utf-8")
    with pytest.raises(SyncError, match="Supabase holds no companies"):
        sync_supabase.pull_companies(source(CompanyPages([])), str(path))
    assert path.read_text(encoding="utf-8") == "previous"
    assert not (tmp_path / "companies.json.part").exists()


def test_a_table_that_lost_more_than_half_of_the_last_pull_is_refused_and_the_file_is_left_alone(
    tmp_path: Path,
) -> None:
    path = tmp_path / "companies.json"
    sync_supabase.pull_companies(source(CompanyPages([company_row(f"C{n}") for n in range(10)])), str(path))
    before = path.read_text(encoding="utf-8")
    with pytest.raises(SyncError, match=r"holds 4 companies and the last pull had 10"):
        sync_supabase.pull_companies(source(CompanyPages([company_row(f"C{n}") for n in range(4)])), str(path))
    assert path.read_text(encoding="utf-8") == before
    sync_supabase.pull_companies(
        source(CompanyPages([company_row(f"C{n}") for n in range(5)])), str(path)
    )  # half is fine
    (tmp_path / "companies.pulled.json").write_text("cut", encoding="utf-8")  # an unreadable baseline: no yardstick
    assert sync_supabase.pull_companies(source(CompanyPages([company_row("A")])), str(path)) == 1


def test_a_table_that_cannot_be_read_leaves_no_file(tmp_path: Path) -> None:
    class Down:
        def request(self, *_a: Any, **_k: Any) -> Any:
            raise requests.ConnectionError("down")

    with pytest.raises(export_accounts.ExportError, match="reading companies failed"):
        sync_supabase.pull_companies(source(Down()), str(tmp_path / "companies.json"))
    assert not list(tmp_path.iterdir())


def run_pull(monkeypatch: pytest.MonkeyPatch, fake: Any, *args: str) -> None:
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main(list(args))


def test_pull_companies_writes_the_list_only_and_prints_a_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Path
) -> None:
    (workdir / "data" / "jobs.db").unlink()
    run_pull(monkeypatch, CompanyPages([company_row("Acme")]), "--pull-companies")
    assert capsys.readouterr().out.splitlines() == ["Supabase: pulled 1 companies into data/companies.json"]
    assert [r["name"] for r in json.loads((workdir / "data" / "companies.json").read_text())] == ["Acme"]
    assert not (workdir / "data" / "jobs.db").exists()  # the history is not touched


def test_the_pull_writes_the_list_as_well_as_the_history(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> None:
    (workdir / "data" / "jobs.db").unlink()
    tables = {"companies": [company_row("Zed"), company_row("Acme")], "jobs": [job_row("Acme", "1")], "crawl_log": []}
    run_pull(monkeypatch, HistoryPages(tables), "--pull", "--allow-shrink")
    assert (workdir / "data" / "jobs.db").exists()
    assert [r["name"] for r in json.loads((workdir / "data" / "companies.json").read_text())] == ["Acme", "Zed"]


def test_a_pull_that_finds_no_companies_stops_the_run_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, workdir: Path
) -> None:
    """D4: a run must not crawl without the list, and so without the exclusions."""
    (workdir / "data" / "companies.json").write_text("previous", encoding="utf-8")
    with pytest.raises(SystemExit) as info:
        run_pull(monkeypatch, CompanyPages([]), "--pull-companies")
    assert str(info.value).startswith("Supabase pull failed: Supabase holds no companies: refusing to start a run")
    assert KEY not in str(info.value)
    assert (workdir / "data" / "companies.json").read_text() == "previous"


# ---- the push ----


def test_only_the_discovered_fields_that_changed_are_sent_and_nothing_a_person_owns(tmp_path: Path) -> None:
    rows = [
        company_row(
            "Acme", ats="greenhouse", slug="old", source="guess", careers_url="", links={"global": "https://a.test"}
        ),
        company_row("Same", ats="lever", slug="same", source="page", careers_url=""),
    ]
    edited = found("Acme", ats="lever", slug="acme", israel_only=True, verify=False, links={"global": "x"}, segment="s")
    done, fake = push(rows, [edited, found("Same")], tmp_path)
    assert done == (1, 0, 0, 0)
    (patch,) = fake.patches()
    assert patch["json"] == {"ats": "lever", "slug": "acme", "source": "page"}  # careers_url and status agree
    assert set(patch["json"]) <= set(discover.DISCOVERED)
    assert patch["params"] == {  # the row as it was pulled: five discovered values, crawling, not excluded
        "name": "eq.Acme",
        "crawl": "eq.true",
        "excluded": "is.null",
        "select": "name",
        "careers_url": "eq.",
        "ats": "eq.greenhouse",
        "slug": "eq.old",
        "source": "eq.guess",
        "status": "eq.ok",
    }
    assert rows[0]["links"] == {"global": "https://a.test"} and rows[0]["israel_only"] is None  # untouched


def test_a_value_the_pull_left_out_is_matched_as_null_and_an_empty_string_equals_a_null_for_the_comparison(
    tmp_path: Path,
) -> None:
    rows = [company_row("Acme", ats=None, slug=None, source=None, careers_url=None, status=None)]
    record = {"name": "Acme", "crawl": True, "careers_url": "", "ats": "", "slug": "", "source": "", "status": ""}
    assert push(rows, [record], tmp_path)[0] == (0, 0, 0, 0)
    assert push(rows, [{"name": "Acme", "crawl": True}], tmp_path)[0] == (0, 0, 0, 0)  # keys the record lacks
    done, fake = push(rows, [{**record, "slug": "s"}], tmp_path)
    assert done == (1, 0, 0, 0)
    assert fake.patches()[0]["params"]["ats"] == "is.null" and fake.patches()[0]["json"] == {"slug": "s"}
    assert rows[0]["slug"] == "s"


def test_a_careers_address_that_is_http_is_sent_as_https(tmp_path: Path) -> None:
    rows = [company_row("Acme", careers_url="")]
    record = found("Acme", careers_url="http://acme.test/jobs", ats="", slug="", source="", status="ok")
    done, fake = push(rows, [record], tmp_path)
    assert done[0] == 1
    assert fake.patches()[0]["json"]["careers_url"] == "https://acme.test/jobs"


def test_a_company_that_is_not_crawled_or_excluded_or_new_is_left_alone(tmp_path: Path) -> None:
    rows = [
        company_row("Card", crawl=False, gap={"reason": "no_openings", "note": ""}),
        {**company_row("Gone"), "crawl": False, "excluded": excluded_record("Gone")["excluded"]},
        company_row("Acme"),
    ]
    records = [found("Card"), found("Gone"), found("Missing"), found("Acme", slug="new"), found("Out", crawl=False)]
    done, fake = push(rows, records, tmp_path)
    assert done == (1, 3, 0, 0)  # a record of the file that is not crawled is not counted at all
    assert [p["params"]["name"] for p in fake.patches()] == ["eq.Acme"]


@pytest.mark.parametrize(
    "edit",
    [
        lambda rows: rows[0].update(source="override", ats="lever", slug="mine"),  # pinned during the run
        lambda rows: rows[0].update(source="override", slug="fixed"),  # a pin corrected during the run
        lambda rows: rows[0].update(slug="other"),  # any discovered field saved since the pull
        lambda rows: rows[0].update(careers_url="https://a.test/careers"),
        lambda rows: rows[0].update(crawl=False, gap={"reason": "no_openings", "note": ""}),  # taken off the crawl
        lambda rows: rows[0].update(crawl=False, excluded=excluded_record("Acme")["excluded"]),
    ],
)
def test_a_row_an_editor_changed_since_the_pull_is_left_alone(tmp_path: Path, edit: Any) -> None:
    rows = [company_row("Acme", ats="greenhouse", slug="old", source="guess", careers_url="")]
    edited = [dict(rows[0])]
    edit(edited)  # what the editor alone leaves
    done, _fake = push(rows, [found("Acme", slug="new", source="page")], tmp_path, edit=edit)
    assert done == (0, 1, 0, 0)
    assert rows == edited


def test_two_pins_with_a_corrected_ats_and_slug_keep_the_correction(tmp_path: Path) -> None:
    """Discovery keeps a pin's ats and slug as pulled; the push must not write them back over a correction."""
    rows = [company_row("Pinned", ats="lever", slug="wrong", source="override", status="ats_failed")]
    record = found("Pinned", ats="lever", slug="wrong", source="override", status="not_found")
    done, fake = push(rows, [record], tmp_path, edit=lambda r: r[0].update(slug="right"))
    assert (done, rows[0]["slug"]) == ((0, 1, 0, 0), "right")
    assert len(fake.patches()) == 1  # it asked, and the row did not match
    done, _ = push(
        [pinned := company_row("Pinned", ats="lever", slug="s", source="override", status="ats_failed")],
        [found("Pinned", ats="lever", slug="s", source="override", status="not_found")],
        tmp_path,
    )
    assert (done, pinned["status"]) == ((1, 0, 0, 0), "not_found")  # a feed that was not ok is rewritten


def test_a_working_feed_that_discovery_no_longer_finds_is_held_back_with_a_counts_only_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        company_row(n, ats="lever", slug=n.lower(), source="page", careers_url="") for n in ("Down", "Blank", "Fine")
    ]
    gone = {"ats": "", "slug": "", "source": "", "status": "not_found"}
    blank = {"ats": "", "slug": "", "source": "", "status": "ok"}  # still ok, but a value turned blank
    records = [found("Down", **gone), found("Blank", **blank), found("Fine", slug="new")]
    done, fake = push(rows, records, tmp_path)
    assert done == (1, 0, 0, 2)
    assert [p["params"]["name"] for p in fake.patches()] == ["eq.Fine"]
    assert (rows[0]["ats"], rows[1]["slug"]) == ("lever", "blank")
    assert capsys.readouterr().out == (
        "::warning::2 companies with a working feed were left as they are: discovery found nothing for them\n"
    )


def test_a_company_that_was_not_ok_may_stay_blank_or_change_to_any_status(tmp_path: Path) -> None:
    rows = [company_row("Was", ats="", slug="", source="", status="not_found", careers_url=None)]
    done, _ = push(
        rows,
        [found("Was", ats="", slug="", source="", status="careers_page_only", careers_url="https://w.test")],
        tmp_path,
    )
    assert done == (1, 0, 0, 0)


def test_a_request_that_fails_is_counted_and_the_next_one_still_goes_and_the_log_holds_no_cell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [company_row("Bad"), company_row("Good")]
    done, _fake = push(rows, [found("Bad", slug="x"), found("Good", slug="y")], tmp_path, fail_patch_of="Bad")
    assert done == (1, 0, 1, 0)
    out = capsys.readouterr().out
    assert out.endswith("::warning::a company could not be updated: PATCH companies failed: HTTP 400\n")
    assert SECRET_TEXT not in out
    assert rows[1]["slug"] == "y"


def test_an_answer_that_is_not_a_list_is_a_failure(tmp_path: Path) -> None:
    class Odd(Companies):
        def request(self, method: str, url: str, **kw: Any) -> Any:
            if method == "PATCH":
                return FakeHttpResponse(data={"message": "x"})
            return super().request(method, url, **kw)

    fake = Odd([company_row("Acme")])
    path = tmp_path / "c.json"
    sync_supabase.pull_companies(source(fake), str(path))
    write_json(path, [found("Acme", slug="new")])
    assert sync_supabase.push_companies(client(fake), str(path)) == (0, 0, 1, 0)


def test_an_empty_baseline_is_refused_and_nothing_is_sent(tmp_path: Path) -> None:
    fake = Companies([])
    path = tmp_path / "c.json"
    write_json(path, [found("Acme")])
    write_json(tmp_path / "c.pulled.json", [])
    with pytest.raises(SyncError, match="nothing written"):
        sync_supabase.push_companies(client(fake), str(path))
    assert fake.patches() == []


def run_push(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    run_pull(monkeypatch, fake, "--push-companies")


def stage(workdir: Path, rows: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """What the first two steps of the workflow leave in data/: the pulled list and its baseline, then discovery's."""
    sync_supabase.pull_companies(source(Companies(rows)), str(workdir / "data" / "companies.json"))
    write_json(workdir / "data" / "companies.json", records)


def test_main_push_prints_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Path
) -> None:
    rows = [company_row("Acme")]
    stage(workdir, rows, [found("Acme", slug="new"), found("Missing")])
    fake = Companies(rows)
    run_push(monkeypatch, fake)
    out = capsys.readouterr().out
    assert out == "Supabase: 1 companies updated, 1 left alone (changed or removed since the pull), 0 held back\n"
    assert KEY not in out
    assert fake.patches()[0]["headers"]["apikey"] == KEY


def test_main_push_fails_the_step_when_a_company_could_not_be_updated(
    monkeypatch: pytest.MonkeyPatch, workdir: Path
) -> None:
    rows = [company_row("Bad")]
    stage(workdir, rows, [found("Bad", slug="x")])
    with pytest.raises(SystemExit, match="failed for 1 companies"):
        run_push(monkeypatch, Companies(rows, fail_patch_of="Bad"))


def test_main_push_says_what_stopped_it_without_the_key(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> None:
    data = workdir / "data"
    (data / "companies.json").write_text("[]", encoding="utf-8")
    (data / "companies.pulled.json").write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="Supabase push of the companies failed: the pulled list is empty"):
        run_push(monkeypatch, Companies([]))
    (data / "companies.pulled.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"a list file cannot be read \(JSONDecodeError\)"):
        run_push(monkeypatch, Companies([]))
    (data / "companies.pulled.json").unlink()  # the pull step did not run
    with pytest.raises(SystemExit, match=r"cannot be read \(FileNotFoundError\)"):
        run_push(monkeypatch, Companies([]))


def test_the_push_needs_the_secrets_like_every_other_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    with pytest.raises(SystemExit, match="SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is empty"):
        sync_supabase.main(["--push-companies"])


# ---- the pulled file is the list the pipeline reads ----


def test_a_pulled_file_with_a_record_nobody_can_trust_stops_the_run(tmp_path: Path) -> None:
    rows = [
        company_row("Acme"),
        {**company_row("Gone"), "crawl": False, "excluded": {"reason": " ", "date_added": "x"}},
    ]
    path = tmp_path / "companies.json"
    sync_supabase.pull_companies(source(CompanyPages(rows)), str(path))
    with pytest.raises(ExclusionError, match="the reason is blank"):
        load_excluded(str(path))


# ---- a company added with only a name and a domain ----


def test_a_company_added_with_only_a_name_and_a_domain_waits_for_discovery_and_stops_nothing(
    db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table accepts it (every discovered field is null). The pull gives the readers empty ones, so the crawl and
    the dashboard skip it, discover.py finds it, and the push writes the result over the nulls."""
    prepare(workdir)  # the templates the build needs (and a list of its own, which the pull replaces)
    new = company_row("New", status=None)
    rows = [company_row("Zed", ats="fake", slug="z", source="page", careers_url=""), new]
    path = workdir / "data" / "companies.json"
    sync_supabase.pull_companies(source(Companies(rows)), str(path))
    (pulled,) = (r for r in json.loads(path.read_text(encoding="utf-8")) if r["name"] == "New")
    assert pulled == {"name": "New", "domain": "new.test", "crawl": True} | dict.fromkeys(discover.DISCOVERED, "")
    db.close()
    crawled: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: crawled.append(slug) or [])
    run.main()  # selects the companies to crawl: a missing `status` used to raise KeyError here
    assert crawled == ["z"]
    build_dashboard.main()  # and builds the card of the companies without a feed
    assert read_payload(workdir)["covered"] == 1
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    monkeypatch.setattr(
        discover,
        "discover",
        lambda row: {"careers_url": "", "ats": "lever", "slug": "new", "source": "guess", "status": "ok"},
    )
    discover.main()
    fake = Companies(rows)
    assert sync_supabase.push_companies(client(fake), str(path)) == (2, 0, 0, 0)  # discovery (stubbed) changed both
    patches = {p["params"]["name"]: p for p in fake.patches()}
    assert patches["eq.New"]["params"]["ats"] == "is.null"  # the baseline holds the null, so the filter still matches
    assert (new["ats"], new["status"]) == ("lever", "ok")
