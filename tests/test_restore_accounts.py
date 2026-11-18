"""restore_accounts.py: the export turned into one idempotent SQL script, in foreign-key order, that never deletes."""

import json
import re
import runpy
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

import export_accounts
import restore_accounts
from export_accounts import ALL_TABLES, JOB_TABLES, TABLES, Table, render
from restore_accounts import RestoreError, literal, load, script

U1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
U2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OVERRIDDEN = ("crawl_log", "companies")
EMAILS = ("ann@test.example", "bob.o'brien@test.example")


def data() -> dict[str, list[dict[str, Any]]]:
    return {
        "profiles": [{"user_id": U1, "email": EMAILS[0]}, {"user_id": U2, "email": EMAILS[1]}],
        "user_prefs": [{"user_id": U1, "filters": {"terms": ["$restore$ \\ 'q'"]}, "email_settings": {}}],
        "user_jobs": [{"user_id": U1, "company": "Acme", "job_id": "1", "state": "star", "title": "Titre é"}],
        "subscriptions": [{"user_id": U1, "address": EMAILS[0], "unsubscribe_token_hash": "f" * 64}],
        "emailed": [{"user_id": U1, "company": "Acme", "job_id": "1", "emailed_at": "2026-11-02T04:31:00+00:00"}],
        "jobs": [
            {
                "company": "Acme",
                "job_id": "1",
                "title": "Titre é",
                "status": "closed",
                "role_types": ["R&D"],
                "regions": [],
            }
        ],
        "crawl_log": [{"id": 7, "ts": "2026-11-16T04:30:00", "company": "Acme", "ats": "lever", "ok": 1, "n_jobs": 3}],
        "companies": [
            {
                "id": 3,
                "name": "Acme",
                "domain": "acme.example",
                "crawl": True,
                "updated_at": "2026-11-18T10:00:00+00:00",
            },
            {
                "id": 9,
                "name": "Opted Out",
                "crawl": False,
                "excluded": {"reason": "asked to be removed", "date_added": "2026-11-17"},
                "updated_at": "2026-11-17T08:00:00+00:00",
            },
        ],
    }


def write_export(directory: Path, content: dict[str, list[dict[str, Any]]] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in (content or data()).items():
        (directory / f"{name}.json").write_text(render(rows), encoding="utf-8")
    return directory


def loaded(directory: Path) -> dict[str, list[dict[str, Any]]]:
    return {table.name: load(directory, table) for table in ALL_TABLES}


def test_the_script_is_one_transaction_that_inserts_in_foreign_key_order_and_never_deletes(tmp_path: Path) -> None:
    text = script(loaded(write_export(tmp_path / "e")))
    assert text.startswith("-- Restore of the Misra account tables")
    assert text.count("begin;") == text.count("commit;") == 1
    assert text.index("begin;") < text.index("insert into") < text.rindex("insert into") < text.index("commit;")
    inserted = re.findall(r"insert into public\.(\w+) ", text)
    assert inserted == [
        "profiles",
        "user_prefs",
        "user_jobs",
        "subscriptions",
        "emailed",
        "jobs",
        "crawl_log",
        "companies",
    ]
    assert text.count("on conflict do nothing;") == len(ALL_TABLES)
    lowered = text.lower()
    for forbidden in ("delete ", "truncate", "drop ", "do update", "update public"):
        assert forbidden not in lowered, forbidden


def test_the_tables_own_triggers_are_off_for_the_inserts_and_on_again_before_the_commit(tmp_path: Path) -> None:
    text = script(loaded(write_export(tmp_path / "e")))
    off = [m.start() for m in re.finditer(r"disable trigger user;", text)]
    on = [m.start() for m in re.finditer(r"enable trigger user;", text)]
    assert len(off) == len(on) == len(ALL_TABLES)
    assert max(off) < text.index("insert into") and min(on) > text.rindex("insert into public.companies")
    assert max(on) < text.index("commit;")
    for table in ALL_TABLES:
        assert f"alter table public.{table.name} disable trigger user;" in text
        assert f"alter table public.{table.name} enable trigger user;" in text


def test_every_column_is_named_and_values_go_through_the_database_types(tmp_path: Path) -> None:
    text = script(loaded(write_export(tmp_path / "e")))
    for table in TABLES:
        columns = ", ".join(table.columns)
        qualified = ", ".join(f"r.{column}" for column in table.columns)
        assert f"insert into public.{table.name} ({columns})\nselect {qualified} from json_populate_recordset(" in text
        assert f"null::public.{table.name}" in text
    for table in JOB_TABLES:  # no user: every row goes in, and nothing asks auth.users
        columns = ", ".join(table.columns)
        assert f"select {columns} from json_populate_recordset(null::public.{table.name}," in text


def test_a_value_that_looks_like_the_quote_tag_cannot_end_the_text(tmp_path: Path) -> None:
    text = script(loaded(write_export(tmp_path / "e")))
    assert "$restorex$" in text  # the prefs hold the characters $restore$, so the tag grew by one letter
    body = re.search(r"\$(restorex+)\$(.*?)\$\1\$::json", text, flags=re.DOTALL)
    assert body
    assert json.loads(body[2]) == data()["user_prefs"]
    assert literal([]) == "$restore$[]$restore$"


def test_quotes_and_non_ascii_survive_in_the_json_text(tmp_path: Path) -> None:
    text = script(loaded(write_export(tmp_path / "e")))
    assert "bob.o'brien@test.example" in text
    assert "é" not in text  # escaped, so no encoding of the file can change it
    assert "Titre \\u00e9" in text


def test_the_empty_export_gives_a_script_that_inserts_nothing(tmp_path: Path) -> None:
    empty = {table.name: [] for table in ALL_TABLES}
    text = script(loaded(write_export(tmp_path / "e", empty)))
    assert text.count("$restore$[]$restore$") == len(ALL_TABLES)


def test_without_the_auth_users_step_only_the_rows_of_people_who_still_have_a_sign_in_row_are_restored(
    tmp_path: Path,
) -> None:
    """The safe default (restoring into the live project): the database itself skips every exported user id that is no
    longer in auth.users, so an account deleted after the backup is not brought back and no email is looked at."""
    content = loaded(write_export(tmp_path / "e"))
    text = script(content)
    check = "where exists (select 1 from auth.users u where u.id = r.user_id)\non conflict do nothing;\n"
    assert text.count(check) == len(TABLES)  # every account table, no exception (the job tables have no user)
    assert "insert into auth.users" not in text  # never creates a sign-in row in this mode
    created = script(content, with_auth_users=True)
    assert "from auth.users u" not in created  # a fresh project: the rows are created first, then all are restored
    assert created.count("on conflict do nothing;") == len(ALL_TABLES) + 1


def test_the_auth_users_step_is_optional_comes_first_and_only_adds_missing_rows(tmp_path: Path) -> None:
    content = loaded(write_export(tmp_path / "e"))
    assert "insert into auth.users" not in script(content)
    text = script(content, with_auth_users=True)
    assert text.count("insert into auth.users") == 1
    assert text.index("insert into auth.users") < text.index("insert into public.profiles")
    assert text.index("insert into auth.users") > text.index("disable trigger user")
    step = text[text.index("insert into auth.users") : text.index("insert into public.profiles")]
    assert step.rstrip().endswith("on conflict do nothing;")
    assert "bob.o'brien@test.example" in step
    assert "user_prefs" not in step and "subscriptions" not in step  # profiles only: an id and an email


def test_load_rejects_what_cannot_be_restored_faithfully(tmp_path: Path) -> None:
    table = TABLES[0]
    directory = tmp_path / "e"
    directory.mkdir()
    with pytest.raises(RestoreError, match=r"profiles.json cannot be read as JSON"):
        load(directory, table)
    (directory / "profiles.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RestoreError, match=r"cannot be read as JSON"):
        load(directory, table)
    (directory / "profiles.json").write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(RestoreError, match=r"not a list of rows"):
        load(directory, table)
    (directory / "profiles.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(RestoreError, match=r"not a list of rows"):
        load(directory, table)
    (directory / "profiles.json").write_text(
        json.dumps([{"user_id": U1, "email": "a", "nickname": "x"}]), encoding="utf-8"
    )
    with pytest.raises(RestoreError, match=r"columns nickname that the restore does not know"):
        load(directory, table)
    (directory / "profiles.json").write_text(json.dumps([{"email": "a"}]), encoding="utf-8")
    with pytest.raises(RestoreError, match=r"row without user_id"):
        load(directory, table)


def test_a_missing_table_file_stops_the_script_from_being_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_export(tmp_path / "e")
    (directory / "emailed.json").unlink()
    target = tmp_path / "restore.sql"
    with pytest.raises(SystemExit, match=r"restore failed: emailed.json cannot be read as JSON"):
        restore_accounts.main([str(directory), str(target)])
    assert not target.exists()


def test_main_writes_a_private_script_and_prints_counts_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = write_export(tmp_path / "e")
    target = tmp_path / "restore.sql"
    nobody = tmp_path / "nobody.txt"
    nobody.write_text("", encoding="utf-8")
    restore_accounts.main([str(directory), str(target), "--create-auth-users", f"--exclude-user-ids={nobody}"])
    assert stat.S_IMODE(target.stat().st_mode) == export_accounts.FILE_MODE
    assert "insert into auth.users" in target.read_text(encoding="utf-8")
    shown = capsys.readouterr().out
    assert shown.strip() == (
        "restore script: profiles 2, user_prefs 1, user_jobs 1, subscriptions 1, emailed 1, "
        "jobs 1, crawl_log 1, companies 2"
    )
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".tmp")]
    for private in (*EMAILS, U1, "Acme"):
        assert private not in shown


def test_the_script_is_replaced_whole_when_it_already_exists(tmp_path: Path) -> None:
    directory = write_export(tmp_path / "e")
    target = tmp_path / "restore.sql"
    (tmp_path / ".restore.sql.tmp").write_text("stale", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    restore_accounts.main([str(directory), str(target)])
    assert target.read_text(encoding="utf-8").startswith("-- Restore")


def test_a_script_inside_the_checkout_is_refused(tmp_path: Path) -> None:
    directory = write_export(tmp_path / "e")
    inside = export_accounts.REPO_ROOT / "restore-should-not-exist.sql"
    with pytest.raises(SystemExit, match="outside it"):
        restore_accounts.main([str(directory), str(inside)])
    assert not inside.exists()


@pytest.mark.parametrize("args", [[], ["only-one"], ["a", "b", "c"]])
def test_main_needs_a_directory_and_a_file(args: list[str]) -> None:
    with pytest.raises(SystemExit, match="usage"):
        restore_accounts.main(args)


def test_running_the_module_reads_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["restore_accounts.py"])
    monkeypatch.delitem(sys.modules, "restore_accounts", raising=False)
    with pytest.raises(SystemExit, match="usage"):
        runpy.run_module("restore_accounts", run_name="__main__")


def test_a_table_the_export_writes_is_a_table_the_restore_reads() -> None:
    assert all(isinstance(table, Table) for table in TABLES)
    assert restore_accounts.TABLES is export_accounts.TABLES
    assert restore_accounts.ALL_TABLES is export_accounts.ALL_TABLES


def test_people_who_left_after_the_backup_are_left_out_of_every_table(tmp_path: Path) -> None:
    directory = write_export(tmp_path / "e")
    ids = tmp_path / "gone.txt"
    ids.write_text(f"# deleted since\n\n{U2}\n", encoding="utf-8")
    target = tmp_path / "restore.sql"
    restore_accounts.main([str(directory), str(target), f"--exclude-user-ids={ids}"])
    text = target.read_text(encoding="utf-8")
    assert EMAILS[1] not in text and EMAILS[0] in text
    assert restore_accounts.read_ids(ids) == {U2}


def test_leave_out_filters_all_tables() -> None:
    kept = restore_accounts.leave_out(data(), {U1})
    assert [row["user_id"] for row in kept["profiles"]] == [U2]
    assert not any(kept[name] for name in kept if name not in {"profiles", "jobs", "crawl_log", "companies"})
    assert (len(kept["jobs"]), len(kept["crawl_log"]), len(kept["companies"])) == (1, 1, 2)  # no user: stays whole


def test_an_unreadable_id_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RestoreError, match="cannot be read"):
        restore_accounts.read_ids(tmp_path / "missing.txt")
    directory = write_export(tmp_path / "e")
    missing = tmp_path / "missing.txt"
    with pytest.raises(SystemExit, match=r"restore failed: missing\.txt cannot be read"):
        restore_accounts.main([str(directory), str(tmp_path / "r.sql"), f"--exclude-user-ids={missing}"])


def test_creating_sign_in_rows_needs_the_list_of_people_who_left(tmp_path: Path) -> None:
    directory = write_export(tmp_path / "e")
    target = tmp_path / "restore.sql"
    with pytest.raises(SystemExit, match="needs --exclude-user-ids"):
        restore_accounts.main([str(directory), str(target), "--create-auth-users"])
    assert not target.exists()
    empty = tmp_path / "none.txt"
    empty.write_text("", encoding="utf-8")
    restore_accounts.main([str(directory), str(target), "--create-auth-users", f"--exclude-user-ids={empty}"])
    assert "insert into auth.users" in target.read_text(encoding="utf-8")


def test_pausing_marks_active_subscriptions_unsubscribed_without_filters(tmp_path: Path) -> None:
    content = data()
    content["subscriptions"][0]["filters"] = {"levels": ["Intern"]}
    content["subscriptions"].append(
        {"user_id": U2, "address": "b@t.example", "unsubscribed_at": "2026-11-01T00:00:00+00:00", "filters": {}}
    )
    restore_accounts.pause(content, "2026-11-16T00:00:00+00:00")
    assert content["subscriptions"][0]["unsubscribed_at"] == "2026-11-16T00:00:00+00:00"
    assert content["subscriptions"][0]["filters"] == {}
    assert content["subscriptions"][1]["unsubscribed_at"] == "2026-11-01T00:00:00+00:00"  # already unsubscribed: kept
    directory = write_export(tmp_path / "e")
    target = tmp_path / "restore.sql"
    restore_accounts.main([str(directory), str(target), "--pause-subscriptions"])
    assert '"unsubscribed_at": "20' in target.read_text(encoding="utf-8")


def test_a_crawl_log_row_keeps_its_id_and_the_sequence_moves_past_it(tmp_path: Path) -> None:
    text = script(loaded(write_export(tmp_path / "e")))
    insert = text[text.index("insert into public.crawl_log") :]
    assert insert.startswith(
        "insert into public.crawl_log (id, ts, company, ats, ok, n_jobs, note) overriding system value\n"
    )
    assert insert.index("on conflict do nothing;") < insert.index(
        "select setval(pg_get_serial_sequence('public.crawl_log'"
    )
    assert text.count("overriding system value") == len(OVERRIDDEN)  # `jobs` has no generated id
    assert "setval(pg_get_serial_sequence('public.jobs'" not in text


def test_the_company_list_keeps_its_ids_and_the_sequence_moves_past_the_largest(tmp_path: Path) -> None:
    """`companies.id` is `generated always`: the restore gives it explicitly, so a second run is a no-op, and the
    editor's later inserts do not collide with a restored row."""
    text = script(loaded(write_export(tmp_path / "e")))
    assert (
        "insert into public.companies (" + ", ".join(export_accounts.COMPANIES.columns) + ") overriding system value\n"
        in text
    )
    assert (
        "select setval(pg_get_serial_sequence('public.companies', 'id'), (select max(id) from public.companies));"
        in text
    )
    assert (
        "overriding system value"
        not in text.split("insert into public.jobs")[1].split("insert into public.crawl_log")[0]
    )
    assert text.count("overriding system value") == len(OVERRIDDEN)  # crawl_log and companies, no other table


def test_an_excluded_company_is_restored_whole_and_the_checks_and_triggers_are_not_in_the_way(tmp_path: Path) -> None:
    """The record of a legal opt-out survives a restore with its reason and date, and the restore writes it with the
    table's triggers off (the 200-excluded limit, `updated_at` forced to now, the admin log) and its checks intact."""
    text = script(loaded(write_export(tmp_path / "e")))
    body = re.search(r"into public\.companies .*?(\$restore\w*\$)(.*?)\1::json", text, flags=re.DOTALL)
    assert body
    rows = {row["id"]: row for row in json.loads(body[2])}
    assert rows[9]["excluded"] == {"reason": "asked to be removed", "date_added": "2026-11-17"}
    assert rows[9]["updated_at"] == "2026-11-17T08:00:00+00:00"
    assert text.index("alter table public.companies disable trigger user;") < text.index("insert into public.companies")
    assert "alter table public.companies disable trigger all" not in text  # the checks are never switched off
    assert "excluded" not in text.split("insert into public.companies")[0]


def test_the_frozen_company_list_survives_export_and_restore(tmp_path: Path) -> None:
    """Round trip: every record of the frozen list, as the table holds it, goes through the export files and into the
    restore script unchanged (a null is a key the file leaves out, so it comes back as null)."""
    frozen = json.loads((Path(__file__).parent / "fixtures" / "companies.json").read_text(encoding="utf-8"))
    columns = export_accounts.COMPANIES.columns
    rows = [
        {column: record.get(column) for column in columns if column != "id"}
        | {"id": n, "updated_at": "2026-11-18T00:00:00+00:00"}
        for n, record in enumerate(frozen, start=1)
    ]
    rows.append(
        dict.fromkeys(columns)
        | {
            "id": 999,
            "name": "Opted Out",
            "crawl": False,
            "excluded": {"reason": "asked", "date_added": "2026-11-17"},
            "updated_at": "2026-11-18T00:00:00+00:00",
        }
    )
    empty = {table.name: [] for table in ALL_TABLES}
    directory = write_export(tmp_path / "e", empty | {"companies": rows})
    restored = load(directory, export_accounts.COMPANIES)
    assert restored == rows
    assert len(restored) == len(frozen) + 1
    text = script(loaded(directory))
    body = re.search(r"into public\.companies .*?(\$restore\w*\$)(.*?)\1::json", text, flags=re.DOTALL)
    assert body
    assert json.loads(body[2]) == rows
