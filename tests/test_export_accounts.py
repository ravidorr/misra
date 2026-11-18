"""export_accounts.py: every account table read completely with the service-role key, written as stable JSON files
outside the checkout, counts only on the screen, a loud failure instead of a partial table."""

import json
import re
import runpy
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

import export_accounts
from export_accounts import (
    ALL_TABLES,
    COMPANIES,
    CRAWL_LOG,
    JOB_TABLES,
    JOBS,
    TABLES,
    ExportError,
    Source,
    export,
    render,
)

URL = "https://abc.supabase.co"
KEY = "service-role-key-xyz"
MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
SMALL, BIG, PROFILES = 25, 2500, 2
U1, U2, U3 = (f"{n}{n}{n}{n}{n}{n}{n}{n}-{n}{n}{n}{n}-4{n}{n}{n}-8{n}{n}{n}-{n * 12}" for n in "abc")


class Answer:
    def __init__(self, data: Any, total: str | None, status: int = 200, bad_json: bool = False) -> None:
        self.status_code = status
        self.headers = {} if total is None else {"Content-Range": total}
        self._data, self._bad_json = data, bad_json

    def json(self) -> Any:
        if self._bad_json:
            raise ValueError("not json")
        return self._data


class FakeAccounts:
    """PostgREST for the account tables: ordered pages of `page` rows at most, and a Content-Range with the total."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None, page: int = 1000) -> None:
        self.tables = tables if tables is not None else {}
        self.page = page
        self.calls: list[dict[str, Any]] = []
        self.hook: Callable[[str, int], Answer | None] | None = None

    def request(self, method: str, url: str, **kw: Any) -> Answer:
        self.calls.append({"method": method, "url": url, **kw})
        name = url.rsplit("/", 1)[1]
        offset = kw["params"]["offset"]
        if self.hook and (answer := self.hook(name, offset)):
            return answer
        rows = self.tables.get(name, [])
        window = rows[offset : offset + min(kw["params"]["limit"], self.page)]
        return Answer(window, f"{offset}-{offset + len(window) - 1}/{len(rows)}")


def fixture() -> dict[str, list[dict[str, Any]]]:
    """Rows as PostgREST gives them: every column of the table, null when empty."""
    partial = {
        "profiles": [{"user_id": U2, "email": "bob@test.example"}, {"user_id": U1, "email": "ann@test.example"}],
        "user_prefs": [{"user_id": U1, "filters": {"b": 1, "a": [1]}, "email_settings": {}, "updated_at": "t"}],
        "user_jobs": [
            {"user_id": U1, "company": "Beta", "job_id": "2", "state": "star", "title": "é", "url": None},
            {"user_id": U1, "company": "Acme", "job_id": "9", "state": "star", "title": "x", "url": None},
            {"user_id": U1, "company": "Acme", "job_id": "10", "state": "discard", "title": None, "url": None},
        ],
        "subscriptions": [{"user_id": U1, "address": "ann@test.example", "unsubscribe_token_hash": "f" * 64}],
        "emailed": [],
        "jobs": [
            {"company": "Beta", "job_id": "2", "title": "t", "status": "closed", "role_types": ["R&D"]},
            {"company": "Acme", "job_id": "1", "title": "é", "status": "open", "regions": ["Haifa and the north"]},
        ],
        "crawl_log": [
            {"id": 2, "ts": "2026-11-16T04:30:00", "company": "Acme", "ats": "greenhouse", "ok": 1, "n_jobs": 3},
            {"id": 1, "ts": "2026-11-15T04:30:00", "company": "Acme", "ats": "greenhouse", "ok": 0, "note": "boom"},
        ],
        "companies": [
            {"id": 8, "name": "Opted Out", "crawl": False, "excluded": {"reason": "asked", "date_added": "2026-11-17"}},
            {
                "id": 2,
                "name": "Acme",
                "domain": "acme.example",
                "crawl": True,
                "links": {"global": "https://a.example"},
            },
        ],
    }
    return {
        name: [{column: row.get(column) for column in table.columns} for row in partial[name]]
        for table in ALL_TABLES
        for name in [table.name]
    }


def source(fake: FakeAccounts) -> Source:
    return Source(fake, URL, KEY)  # type: ignore[arg-type]


def test_every_table_is_written_sorted_by_primary_key_with_stable_keys(tmp_path: Path) -> None:
    counts = export(source(FakeAccounts(fixture())), tmp_path / "out")
    assert counts == {
        "profiles": 2,
        "user_prefs": 1,
        "user_jobs": 3,
        "subscriptions": 1,
        "emailed": 0,
        "jobs": 2,
        "crawl_log": 2,
        "companies": 2,
    }
    out = tmp_path / "out"
    assert sorted(path.name for path in out.iterdir()) == sorted(f"{table.name}.json" for table in ALL_TABLES)
    profiles = json.loads((out / "profiles.json").read_text(encoding="utf-8"))
    assert [row["user_id"] for row in profiles] == [U1, U2]
    jobs = json.loads((out / "user_jobs.json").read_text(encoding="utf-8"))
    assert [(row["company"], row["job_id"]) for row in jobs] == [("Acme", "10"), ("Acme", "9"), ("Beta", "2")]
    assert (out / "emailed.json").read_text(encoding="utf-8") == "[]\n"
    assert [row["company"] for row in json.loads((out / "jobs.json").read_text(encoding="utf-8"))] == ["Acme", "Beta"]
    assert [row["id"] for row in json.loads((out / "crawl_log.json").read_text(encoding="utf-8"))] == [1, 2]
    companies = json.loads((out / "companies.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in companies] == [2, 8]
    assert companies[1]["excluded"] == {"reason": "asked", "date_added": "2026-11-17"}  # the opt-outs are in the backup
    prefs = (out / "user_prefs.json").read_text(encoding="utf-8")
    assert '"filters": {"a": [1], "b": 1}' in prefs  # keys sorted at every depth
    assert "é" in (out / "user_jobs.json").read_text(encoding="utf-8")  # not \u-escaped: readable diffs


def test_the_same_data_gives_byte_identical_files_and_a_change_touches_one_line(tmp_path: Path) -> None:
    export(source(FakeAccounts(fixture())), tmp_path / "a")
    export(source(FakeAccounts({name: list(reversed(rows)) for name, rows in fixture().items()})), tmp_path / "b")
    for table in ALL_TABLES:
        assert (tmp_path / "a" / f"{table.name}.json").read_bytes() == (
            tmp_path / "b" / f"{table.name}.json"
        ).read_bytes()
    changed = fixture()
    changed["profiles"][0]["email"] = "bob@other.example"
    export(source(FakeAccounts(changed)), tmp_path / "c")
    before = (tmp_path / "a" / "profiles.json").read_text(encoding="utf-8").splitlines()
    after = (tmp_path / "c" / "profiles.json").read_text(encoding="utf-8").splitlines()
    assert [i for i, (x, y) in enumerate(zip(before, after, strict=True)) if x != y] == [2]


def test_a_numeric_key_sorts_as_a_number(tmp_path: Path) -> None:
    tables = fixture()
    tables["crawl_log"] = [{**tables["crawl_log"][0], "id": n} for n in (10, 9, 100, 1)]
    export(source(FakeAccounts(tables)), tmp_path / "out")
    ids = [row["id"] for row in json.loads((tmp_path / "out" / "crawl_log.json").read_text(encoding="utf-8"))]
    assert ids == [1, 9, 10, 100]  # the pull gives the scratch history this order, which is the insert order


def test_one_row_per_line() -> None:
    text = render([{"b": 1, "a": 2}, {"a": 3}])
    assert text == '[\n  {"a": 2, "b": 1},\n  {"a": 3}\n]\n'
    assert render([]) == "[]\n"


def test_the_files_and_the_directory_are_private_to_the_owner(tmp_path: Path) -> None:
    export(source(FakeAccounts(fixture())), tmp_path / "out")
    assert stat.S_IMODE((tmp_path / "out").stat().st_mode) == export_accounts.DIR_MODE
    for table in ALL_TABLES:
        assert stat.S_IMODE((tmp_path / "out" / f"{table.name}.json").stat().st_mode) == export_accounts.FILE_MODE
    assert not [path for path in (tmp_path / "out").iterdir() if path.name.endswith(".tmp")]


def test_an_old_export_is_replaced_whole_and_a_stale_scratch_file_does_not_block(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / ".profiles.json.tmp").write_text("left behind", encoding="utf-8")
    (out / "profiles.json").write_text("old", encoding="utf-8")
    export(source(FakeAccounts(fixture())), out)
    assert json.loads((out / "profiles.json").read_text(encoding="utf-8"))[0]["user_id"] == U1


def test_pages_are_followed_until_an_empty_one_even_when_the_server_caps_below_the_page_size() -> None:
    rows = [{"user_id": f"{n:08d}-0000-4000-8000-000000000000", "email": f"{n}@t"} for n in range(SMALL)]
    fake = FakeAccounts({"profiles": rows}, page=10)
    got = source(fake).read(TABLES[0])
    assert len(got) == SMALL
    offsets = [call["params"]["offset"] for call in fake.calls]
    assert offsets == [0, 10, 20, SMALL]  # the short third page does not end the read; the empty fourth does


def test_a_real_page_boundary_over_a_thousand_rows(tmp_path: Path) -> None:
    rows = [{"user_id": f"{n:08d}-0000-4000-8000-000000000000", "email": f"{n}@t"} for n in range(BIG)]
    counts = export(source(FakeAccounts({"profiles": rows})), tmp_path / "out")
    assert counts["profiles"] == BIG


def test_the_request_asks_for_everything_in_primary_key_order_with_the_key_in_headers_only() -> None:
    fake = FakeAccounts(fixture())
    source(fake).read(TABLES[2])
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{URL}/rest/v1/user_jobs"
    assert call["params"] == {"select": "*", "order": "user_id.asc,company.asc,job_id.asc", "limit": 1000, "offset": 0}
    assert call["headers"] == {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Prefer": "count=exact"}
    assert call["allow_redirects"] is False
    assert KEY not in json.dumps({"url": call["url"], "params": call["params"]})


def test_a_table_that_cannot_be_read_stops_the_export_and_writes_nothing(tmp_path: Path) -> None:
    fake = FakeAccounts(fixture())
    fake.hook = lambda name, _offset: Answer(None, None, status=500) if name == "subscriptions" else None
    with pytest.raises(ExportError, match=r"reading subscriptions failed: HTTP 500"):
        export(source(fake), tmp_path / "out")
    assert not (tmp_path / "out").exists()  # profiles and the others were read, and none of them was written


def test_a_network_error_names_the_kind_only(tmp_path: Path) -> None:
    class Down:
        def request(self, *_a: Any, **_k: Any) -> Answer:
            raise requests.ConnectionError(f"connection to {URL} with {KEY} failed")

    with pytest.raises(ExportError, match=r"reading profiles failed: ConnectionError$"):
        export(Source(Down(), URL, KEY), tmp_path / "out")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "answer",
    [
        Answer({"not": "a list"}, "0-0/1"),
        Answer([1], "0-0/1"),
        Answer([], None),
        Answer([], "*/x"),
        Answer(None, "0-0/1", bad_json=True),
    ],
)
def test_an_answer_in_the_wrong_shape_is_an_error(answer: Answer, tmp_path: Path) -> None:
    fake = FakeAccounts()
    fake.hook = lambda *_a: answer
    with pytest.raises(ExportError, match=r"reading profiles failed"):
        export(source(fake), tmp_path / "out")


def test_a_row_without_its_primary_key_means_the_schema_changed(tmp_path: Path) -> None:
    tables = fixture()
    tables["user_jobs"][0]["job_id"] = None
    with pytest.raises(ExportError, match=r"user_jobs has a row without job_id"):
        export(source(FakeAccounts(tables)), tmp_path / "out")


def test_a_table_that_changes_under_the_read_is_read_again(tmp_path: Path) -> None:
    fake = FakeAccounts(fixture())
    seen = {"count": 0}

    def hook(name: str, offset: int) -> Answer | None:
        if name == "profiles" and offset == 0 and seen["count"] == 0:
            seen["count"] += 1
            return Answer(fixture()["profiles"], "0-1/3")  # the total says 3, only 2 rows follow
        return None

    fake.hook = hook
    export(source(fake), tmp_path / "out")
    assert seen["count"] == 1
    assert len(json.loads((tmp_path / "out" / "profiles.json").read_text(encoding="utf-8"))) == PROFILES


def test_a_duplicate_primary_key_counts_as_a_change_and_gives_up_after_three_reads(tmp_path: Path) -> None:
    tables = fixture()
    tables["profiles"].append(dict(tables["profiles"][0]))
    fake = FakeAccounts(tables)
    with pytest.raises(ExportError, match=r"profiles kept changing while it was read, 3 times"):
        export(source(fake), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_the_url_must_be_https() -> None:
    with pytest.raises(ExportError, match="https://"):
        Source(FakeAccounts(), "http://abc.supabase.co", KEY)  # type: ignore[arg-type]


def test_a_directory_inside_the_checkout_is_refused(tmp_path: Path) -> None:
    inside = export_accounts.REPO_ROOT / "tests" / "should-not-exist"
    with pytest.raises(ExportError, match="outside it"):
        export(source(FakeAccounts(fixture())), inside)
    with pytest.raises(ExportError, match="outside it"):
        export(source(FakeAccounts(fixture())), export_accounts.REPO_ROOT)
    assert not inside.exists()
    assert export_accounts.outside_repository(tmp_path) == tmp_path.resolve()


def test_the_columns_match_the_migrations() -> None:
    """A column added to an account table must be added to TABLES (and so to the backup and the restore)."""
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
    for table in TABLES:
        block = re.search(rf"create table public\.{table.name} \(\n(.*?)\n\);", sql, flags=re.DOTALL)
        assert block, table.name
        declared = re.findall(r"(?m)^  ([a-z_]+)\s+\S", block[1])
        declared = [name for name in declared if name not in {"primary", "check", "unique", "constraint", "foreign"}]
        assert tuple(declared) == table.columns, table.name
        assert set(table.key) <= set(table.columns)
        later = re.findall(rf"alter table public\.{table.name}\s+add column", sql, flags=re.IGNORECASE)
        assert not later, f"{table.name} gained a column in a later migration: update TABLES"


def test_the_job_history_is_exported_without_the_text_of_the_postings() -> None:
    """`jobs`, `crawl_log` and `companies` columns match the migrations (a new column of `companies` must be added to
    COMPANIES), except the text of the postings, which never leaves Supabase."""
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
    text = {"description", "details", "snippet", "snippet_cut"}
    for table, hidden in ((JOBS, text), (CRAWL_LOG, set()), (COMPANIES, set())):
        block = re.search(rf"create table public\.{table.name} \(\n(.*?)\n\);", sql, flags=re.DOTALL)
        assert block, table.name
        declared = re.findall(r"(?m)^  ([a-z_]+)\s+\S", block[1])
        declared = [name for name in declared if name not in {"primary", "check", "unique", "constraint", "foreign"}]
        added = re.findall(
            rf"alter table public\.{table.name}\s+add column (?:if not exists )?(\w+)", sql, re.IGNORECASE
        )
        assert set(declared) | set(added) == set(table.columns) | hidden, table.name
        assert not hidden & set(table.columns)
    assert not [table.name for table in JOB_TABLES if table.account]


def test_the_job_tables_are_read_by_named_columns_never_by_star() -> None:
    fake = FakeAccounts(fixture())
    source(fake).read(COMPANIES)
    assert fake.calls[-1]["params"]["select"] == ",".join(COMPANIES.columns)
    source(fake).read(JOBS)
    select = fake.calls[-1]["params"]["select"]
    assert select == ",".join(JOBS.columns)
    assert not {"description", "details", "snippet", "snippet_cut"} & set(select.split(","))
    source(fake).read(TABLES[0])
    assert fake.calls[-1]["params"]["select"] == "*"


def test_jobs_without_a_profile_do_not_make_the_export_fail_and_profiles_may_be_empty_beside_them(
    tmp_path: Path,
) -> None:
    tables = {name: rows for name, rows in fixture().items() if name in {"jobs", "crawl_log"}}
    counts = export(source(FakeAccounts(tables)), tmp_path / "out")
    assert (counts["profiles"], counts["jobs"], counts["crawl_log"]) == (0, 2, 2)


def test_the_tables_are_the_account_tables_and_not_the_public_or_counter_tables() -> None:
    assert [table.name for table in TABLES] == ["profiles", "user_prefs", "user_jobs", "subscriptions", "emailed"]
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
    owned = set(re.findall(r"create table public\.(\w+) \([^;]*?references auth\.users", sql, flags=re.DOTALL))
    # `admins` (PLAN_2.md step 1.1) is the one table left out: its only row is the maintainer's user id, inserted by
    # hand again after a restore (supabase/README.md, "The admin check").
    assert "admins" in owned
    assert owned - {"admins"} == {table.name for table in TABLES}, "a table that references auth.users is not backed up"


def test_main_prints_counts_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeAccounts(fixture())
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(export_accounts.requests, "Session", lambda: fake)
    export_accounts.main([str(tmp_path / "out")])
    shown = capsys.readouterr()
    assert shown.out.strip() == (
        "export: profiles 2, user_prefs 1, user_jobs 3, subscriptions 1, emailed 0, "
        "jobs 2, crawl_log 2, companies 2 (13 rows)"
    )
    for secret in (KEY, "ann@test.example", U1, "Acme"):
        assert secret not in shown.out + shown.err


@pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_empty_secrets_are_a_failure_not_a_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str) -> None:
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setenv(missing, "  ")
    with pytest.raises(SystemExit, match="nothing can be backed up"):
        export_accounts.main([str(tmp_path / "out")])


def test_main_exits_non_zero_with_the_error_when_a_table_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeAccounts(fixture())
    fake.hook = lambda *_a: Answer(None, None, status=503)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(export_accounts.requests, "Session", lambda: fake)
    with pytest.raises(SystemExit, match=r"export failed: reading profiles failed: HTTP 503"):
        export_accounts.main([str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_main_needs_exactly_one_directory() -> None:
    with pytest.raises(SystemExit, match="usage"):
        export_accounts.main([])


def test_running_the_module_reads_its_arguments_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "argv", ["export_accounts.py"])
    monkeypatch.delitem(sys.modules, "export_accounts", raising=False)
    with pytest.raises(SystemExit, match="usage"):
        runpy.run_module("export_accounts", run_name="__main__")


def test_a_column_that_is_not_in_tables_fails_the_export_the_day_it_appears(tmp_path: Path) -> None:
    tables = fixture()
    tables["profiles"][0]["nickname"] = "x"
    with pytest.raises(ExportError, match=r"profiles columns differ from TABLES \(nickname\)"):
        export(source(FakeAccounts(tables)), tmp_path / "out")


def test_an_empty_profiles_table_beside_full_ones_means_the_key_is_wrong(tmp_path: Path) -> None:
    tables = fixture()
    tables["profiles"] = []
    with pytest.raises(ExportError, match="key may be wrong"):
        export(source(FakeAccounts(tables)), tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert export(source(FakeAccounts({})), tmp_path / "none")["profiles"] == 0  # all empty: a real empty project


def test_a_sign_in_between_the_reads_makes_the_export_read_everything_again(tmp_path: Path) -> None:
    tables = fixture()
    fake = FakeAccounts(tables)
    late = {"user_id": U3, "email": "late@test.example"}
    reads = {"n": 0}

    def hook(name: str, offset: int) -> Answer | None:
        if name == "user_prefs" and offset == 0 and reads["n"] == 0:
            reads["n"] += 1  # the person signs in after profiles was read and saves prefs before user_prefs is read
            tables["user_prefs"].append({**tables["user_prefs"][0], "user_id": U3})
            tables["user_prefs"].sort(key=lambda row: row["user_id"])
            tables["profiles"].append(late)
        return None

    fake.hook = hook
    export(source(fake), tmp_path / "out")
    profiles = json.loads((tmp_path / "out" / "profiles.json").read_text(encoding="utf-8"))
    assert U3 in [row["user_id"] for row in profiles]


def test_rows_of_users_without_a_profile_that_stay_fail_the_export(tmp_path: Path) -> None:
    tables = fixture()
    tables["emailed"] = [
        {"user_id": U3, "company": "Acme", "job_id": "1", "emailed_at": "t"},
    ]
    with pytest.raises(ExportError, match="without a profile kept appearing"):
        export(source(FakeAccounts(tables)), tmp_path / "out")
    assert not (tmp_path / "out").exists()
