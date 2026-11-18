"""The candidate script of PLAN_3.md step D3 (`survey.py --candidates`). Frozen fixtures only: the network is faked."""

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

import survey
from survey import Candidate, PageScan, Row
from tests.conftest import excluded_record, write_json
from tests.fakes import FakeHttpResponse

IL = {"location": "Tel Aviv, Israel"}
US = {"location": "New York, NY"}
ACME = Candidate("Acme", "acme.com", "startup")


class Net:
    """The pages and boards of the fake network: url -> response (None: the request failed)."""

    def __init__(self, pages: dict[str, FakeHttpResponse | None] | None = None) -> None:
        self.pages = pages or {}
        self.asked: list[str] = []

    def get(self, url: str, **_kw: Any) -> FakeHttpResponse | None:
        self.asked.append(url)
        return self.pages.get(url, FakeHttpResponse(status_code=404))


def serve(monkeypatch: pytest.MonkeyPatch, pages: dict[str, FakeHttpResponse | None]) -> Net:
    net = Net(pages)
    monkeypatch.setattr(survey, "get", net.get)
    return net


def feeds(monkeypatch: pytest.MonkeyPatch, **boards: dict[str, list[dict[str, Any]] | None]) -> None:
    """The adapters: ats -> {slug: jobs}; a slug that is not listed has no board (None)."""
    monkeypatch.setattr(
        survey,
        "ADAPTERS",
        {ats: (lambda slug, b=b: b.get(slug)) for ats, b in boards.items()},  # type: ignore[misc]
    )


def home(domain: str, text: str) -> dict[str, FakeHttpResponse | None]:
    return {f"https://www.{domain}/": FakeHttpResponse(text=text, url=f"https://www.{domain}/")}


@pytest.mark.parametrize(
    ("raw", "domain"),
    [
        ("acme.com", "acme.com"),
        (" HTTPS://WWW.Acme.com/careers?x=1 ", "acme.com"),
        ("http://acme.co.il#top", "acme.co.il"),
        ("acme.com.", "acme.com"),
        ("acme", ""),
        ("", ""),
    ],
)
def test_clean_domain(raw: str, domain: str) -> None:
    assert survey.clean_domain(raw) == domain


def write_csv(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_read_candidates(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "c.csv",
        "\ufeffname,domain,kind\nAcme,acme.com,startup\n,nameless.com,x\nNoDomain,,x\n"
        "Acme Again,www.acme.com,x\nBeta,beta.io,\n",
    )
    candidates, left_out = survey.read_candidates(path)
    assert candidates == [Candidate("Acme", "acme.com", "startup"), Candidate("Beta", "beta.io", "")]
    assert left_out == len("abc")  # nameless, no domain, repeated domain


def test_read_candidates_refuses_a_file_without_the_columns(tmp_path: Path) -> None:
    with pytest.raises(survey.CandidateFileError, match="must name the columns"):
        survey.read_candidates(write_csv(tmp_path / "c.csv", "name,site\nAcme,acme.com\n"))
    with pytest.raises(survey.CandidateFileError, match="must name the columns"):
        survey.read_candidates(write_csv(tmp_path / "empty.csv", ""))


def test_read_candidates_refuses_a_file_it_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(survey.CandidateFileError, match="FileNotFoundError"):
        survey.read_candidates(str(tmp_path / "missing.csv"))
    binary = tmp_path / "b.csv"
    binary.write_bytes(b"\xff\xfe\x00name,domain,kind\n")
    with pytest.raises(survey.CandidateFileError, match="UnicodeDecodeError"):
        survey.read_candidates(str(binary))


def test_split_candidates_counts_and_never_names_the_excluded() -> None:
    companies = [
        {"name": "Known", "domain": "known.com", "crawl": True},
        {"name": "Researched Co", "domain": "https://www.Researched.io/", "crawl": False},
        excluded_record("Gone Inc Ltd") | {"domain": "gone-domain.com"},
    ]
    candidates = [
        Candidate("known", "other.com", ""),  # listed by name, case ignored
        Candidate("Other", "www.known.com".removeprefix("www."), ""),  # listed by domain
        Candidate("Gone Inc", "new.com", ""),  # excluded by name
        Candidate("Renamed", "gone-domain.com", ""),  # excluded by domain
        Candidate("Researched", "researched.io", ""),  # listed by a dirty domain of a researched record
        Candidate("GONE", "newer.com", ""),  # excluded as the same name without Inc and Ltd
        Candidate("Fresh", "fresh.com", "x"),
    ]
    todo, listed, barred = survey.split_candidates(candidates, companies, ["Gone Inc Ltd"])
    assert todo == [Candidate("Fresh", "fresh.com", "x")]
    assert (listed, barred) == (3, 3)


def test_scan_career_pages_finds_a_board_link(monkeypatch: pytest.MonkeyPatch) -> None:
    net = serve(
        monkeypatch, {"https://acme.com/careers": FakeHttpResponse(text="<a href='https://jobs.lever.co/acme'>")}
    )
    scan = survey.scan_career_pages("acme.com")
    assert scan == PageScan("lever", "acme", "https://acme.com/careers")
    assert net.asked[:3] == ["https://www.acme.com/", "https://acme.com/", "https://www.acme.com/careers"]


def test_scan_career_pages_reads_what_a_page_without_a_board_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: dict[str, FakeHttpResponse | None] = {
        "https://www.acme.com/": None,  # a failed request
        "https://acme.com/": FakeHttpResponse(text="nothing", url="https://acme.com/"),
        "https://www.acme.com/careers": FakeHttpResponse(
            text="join https://x.wd3.myworkdayjobs.com/Acme", url="https://w/c"
        ),
        "https://acme.com/careers": FakeHttpResponse(text="again", url="https://other/c"),
        "https://www.acme.com/jobs": FakeHttpResponse(status_code=500),
    }
    serve(monkeypatch, pages)
    scan = survey.scan_career_pages("acme.com")
    assert scan == PageScan(careers_url="https://w/c", hint="workday")


def test_scan_career_pages_stops_at_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: dict[str, FakeHttpResponse | None] = {
        "https://www.acme.com/": FakeHttpResponse(status_code=403),
        "https://acme.com/": FakeHttpResponse(status_code=429),
        "https://acme.com/careers": FakeHttpResponse(text="https://boards.greenhouse.io/acme"),
    }
    net = serve(monkeypatch, pages)
    assert survey.scan_career_pages("acme.com") == PageScan(blocked=True)
    assert net.asked == ["https://www.acme.com/"]  # one refusal ends the scan


def test_scan_career_pages_sees_an_oracle_board(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, home("acme.com", "https://x.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/A"))
    assert survey.scan_career_pages("acme.com").hint == "oraclehcm"


@pytest.mark.parametrize(
    ("ats", "url", "body", "expected"),
    [
        ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme", {"name": "Acme Inc"}, "Acme Inc"),
        ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme", {}, ""),
        ("workable", "https://apply.workable.com/api/v1/widget/accounts/acme", {"name": "Acme"}, "Acme"),
        ("smartrecruiters", "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=1", {"content": []}, ""),
        (
            "smartrecruiters",
            "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=1",
            {"content": [{"company": {"name": "Acme SR"}}]},
            "Acme SR",
        ),
        (
            "smartrecruiters",
            "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=1",
            {"content": [{}]},
            "",
        ),
    ],
)
def test_board_name_from_json(
    monkeypatch: pytest.MonkeyPatch, ats: str, url: str, body: dict[str, Any], expected: str
) -> None:
    serve(monkeypatch, {url: FakeHttpResponse(data=body)})
    assert survey.board_name(ats, "acme") == expected


def test_board_name_from_a_page_title(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(
        monkeypatch,
        {
            "https://jobs.lever.co/acme": FakeHttpResponse(text="<html><title>Acme &amp; Sons</title>"),
            "https://jobs.ashbyhq.com/acme": FakeHttpResponse(text="<title>Acme Jobs</title>"),
            "https://jobs.ashbyhq.com/bare": FakeHttpResponse(text="<p>no title</p>"),
        },
    )
    assert survey.board_name("lever", "acme") == "Acme & Sons"
    assert survey.board_name("ashby", "acme") == "Acme"
    assert survey.board_name("ashby", "bare") == ""


def test_board_name_is_empty_when_the_board_does_not_answer_or_is_not_one(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(
        monkeypatch,
        {
            "https://boards-api.greenhouse.io/v1/boards/acme": None,
            "https://apply.workable.com/api/v1/widget/accounts/acme": FakeHttpResponse(status_code=404),
            "https://jobs.lever.co/acme": None,
            "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=1": FakeHttpResponse(json_error=True),
        },
    )
    for ats in ("greenhouse", "workable", "lever", "smartrecruiters", "comeet"):
        assert survey.board_name(ats, "acme") == ""


@pytest.mark.parametrize(
    ("candidate", "board", "expected"),
    [
        ("Acme", "Acme", "yes"),
        ("Acme Ltd.", "ACME, Inc", "yes"),
        ("Check Point", "Checkpoint", "yes"),
        ("Wiz", "Wiz Security", "yes"),
        ("Acme Labs", "Acme", "yes"),
        ("air", "Airbnb", "no"),  # the `air` mistake: a different company on the guessed slug
        ("Acme", "Beta", "no"),
        ("Acme", "", "unknown"),
        ("Acme", "   ", "unknown"),
        ("אקמה", "Acme", "no"),
        ("Acme", "Inc", "no"),
    ],
)
def test_name_match(candidate: str, board: str, expected: str) -> None:
    assert survey.name_match(candidate, board) == expected


def test_probe_keeps_a_board_the_career_page_links_to(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, home("acme.com", "https://jobs.lever.co/acme-hq"))
    feeds(monkeypatch, lever={"acme-hq": [IL, IL, US]})
    row = survey.probe(ACME)
    assert row == Row(ACME, "lever", "acme-hq", 2, "", "page", "no", "keep", "")


def test_probe_flags_the_runner_risk_of_a_board_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, home("acme.com", "https://acme.bamboohr.com/careers"))
    feeds(monkeypatch, bamboohr={"acme": [IL]})
    assert survey.probe(ACME).runner_risk == "no"
    assert survey._risk("workday") == "yes"
    assert survey._risk("oraclehcm") == "yes"


def test_probe_skips_a_linked_board_that_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, home("acme.com", "https://jobs.lever.co/acme"))
    feeds(monkeypatch, lever={})
    assert survey.probe(ACME).why == "feed_failed"

    def broken(_slug: str) -> list[dict[str, Any]]:
        raise ValueError("not json")

    monkeypatch.setattr(survey, "ADAPTERS", {"lever": broken})
    row = survey.probe(ACME)
    assert (row.verdict, row.why, row.ats) == ("skip", "feed_failed", "lever")


def test_probe_skips_a_linked_board_without_israel_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, home("acme.com", "https://jobs.lever.co/acme"))
    feeds(monkeypatch, lever={"acme": [US]})
    row = survey.probe(ACME)
    assert (row.verdict, row.why, row.israel_jobs) == ("skip", "no_israel_jobs", 0)


def test_probe_guesses_in_the_order_of_discover_and_checks_the_name(monkeypatch: pytest.MonkeyPatch) -> None:
    net = serve(
        monkeypatch, {"https://boards-api.greenhouse.io/v1/boards/acme": FakeHttpResponse(data={"name": "Acme Inc"})}
    )
    feeds(monkeypatch, greenhouse={"acme": [IL]}, lever={"acme": [IL]})
    row = survey.probe(ACME)
    assert row == Row(ACME, "greenhouse", "acme", 1, "Acme Inc", "yes", "no", "keep", "")
    assert not any("lever" in url for url in net.asked)  # the first board that matches ends the search


def test_probe_goes_on_after_a_board_with_another_name(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(
        monkeypatch,
        {
            "https://boards-api.greenhouse.io/v1/boards/acme": FakeHttpResponse(data={"name": "Dental Acme"}),
            "https://jobs.lever.co/acme": FakeHttpResponse(text="<title>Acme</title>"),
        },
    )
    feeds(monkeypatch, greenhouse={"acme": [IL]}, lever={"acme": [IL, IL]})
    row = survey.probe(ACME)
    assert (row.ats, row.israel_jobs, row.name_match, row.verdict) == ("lever", 2, "yes", "keep")


def test_probe_skips_a_guessed_board_of_another_company(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {"https://boards-api.greenhouse.io/v1/boards/air": FakeHttpResponse(data={"name": "Airbnb"})})
    feeds(monkeypatch, greenhouse={"air": [IL]})
    row = survey.probe(Candidate("air", "air.com", "startup"))
    assert (row.verdict, row.why, row.name_match, row.board_name) == ("skip", "name_mismatch", "no", "Airbnb")


def test_probe_skips_a_guessed_board_with_no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    feeds(monkeypatch, greenhouse={"acme": [IL]})
    row = survey.probe(ACME)
    assert (row.verdict, row.why, row.name_match) == ("skip", "name_unknown", "unknown")


def test_probe_ignores_a_guess_without_israel_jobs_or_with_an_unreadable_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})

    def broken(_slug: str) -> list[dict[str, Any]]:
        raise KeyError("x")

    monkeypatch.setattr(survey, "ADAPTERS", {"greenhouse": lambda _s: [US], "lever": broken, "ashby": lambda _s: []})
    monkeypatch.setattr(survey, "PROBE_ORDER", ["greenhouse", "lever", "ashby"])
    assert survey.probe(ACME) == Row(ACME, why="not_found")


def test_probe_needs_a_yes_for_a_workday_hint_and_a_page_only(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, home("acme.com", "https://acme.wd3.myworkdayjobs.com/Acme"))
    row = survey.probe(ACME)
    assert (row.ats, row.verdict, row.runner_risk, row.israel_jobs) == ("workday", "needs-yes", "yes", None)
    serve(monkeypatch, {"https://acme.com/careers": FakeHttpResponse(text="Join us", url="https://acme.com/careers")})
    feeds(monkeypatch)
    row = survey.probe(ACME)
    assert (row.ats, row.verdict, row.runner_risk) == ("browser", "needs-yes", "no")


def test_probe_records_a_blocked_host(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {"https://www.acme.com/": FakeHttpResponse(status_code=403)})
    feeds(monkeypatch)
    assert survey.probe(ACME) == Row(ACME, why="blocked")
    serve(monkeypatch, {})
    monkeypatch.setattr(survey, "robots_blocked", lambda: "www.acme.com")
    assert survey.probe(ACME) == Row(ACME, why="blocked")
    monkeypatch.setattr(survey, "robots_blocked", lambda: "")
    assert survey.probe(ACME) == Row(ACME, why="not_found")


def test_a_refusal_of_a_board_host_is_not_a_blocked_company(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})
    feeds(monkeypatch)
    state = {"calls": 0}

    def refused_after_the_scan() -> str:
        state["calls"] += 1
        return "" if state["calls"] == 1 else "api.smartrecruiters.com"

    monkeypatch.setattr(survey, "robots_blocked", refused_after_the_scan)
    assert survey.probe(ACME).why == "not_found"


def test_format_table_summary_and_keep_records() -> None:
    kept = Row(ACME, "lever", "acme", 3, "Acme", "yes", "no", "keep", "")
    rows = [kept, Row(Candidate("Beta", "beta.io", "x" * 5), "browser", verdict="needs-yes", why="career page only")]
    table = survey.format_table(rows).splitlines()
    assert table[0].split() == list(survey.TABLE_HEADER)
    assert table[1].split() == ["Acme", "acme.com", "lever", "acme", "3", "Acme", "yes", "no", "keep"]
    assert len(table) == len(rows) + 1
    long_name = Row(Candidate("N" * 50, "n.com", ""))
    assert "N" * 33 not in survey.format_table([long_name])
    text = survey.summary([*rows, Row(ACME, why="not_found")], listed=2, barred=1, left_out=4)
    assert "Probed: 3" in text
    assert "2 already in the list, 1 excluded (a count only), 4 without a name" in text
    assert "Verdicts (3):\n  keep: 1\n  needs-yes: 1\n  skip: 1" in text
    assert "Why not keep (2)" in text
    assert "Why not keep" not in survey.summary([kept], 0, 0, 0)
    assert survey.keep_records(rows) == [
        {
            "name": "Acme",
            "domain": "acme.com",
            "kind": "startup",
            "ats": "lever",
            "slug": "acme",
            "israel_jobs": 3,
            "runner_risk": "no",
        }
    ]


def test_row_cells_for_a_probe_without_a_count() -> None:
    assert Row(ACME).cells()[4] == ""


# the command


def setup_run(workdir: Path, csv_text: str, extra: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    companies = workdir / "data" / "companies.json"
    write_json(
        companies,
        [
            {"name": "Known", "domain": "known.com", "crawl": True},
            excluded_record("Secret Gone Corp") | {"domain": "secret-gone.com"},
            *(extra or []),
        ],
    )
    return str(companies), write_csv(workdir / "candidates.csv", csv_text)


CSV = (
    "name,domain,kind\n"
    "Known,known.com,startup\n"
    "Secret Gone Corp,secret-gone.com,startup\n"
    "Acme,acme.com,startup\n"
    "Beta,beta.io,startup\n"
)


def test_candidates_main_prints_the_table_and_writes_the_keep_file(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    companies, csv_path = setup_run(workdir, CSV)
    serve(monkeypatch, {**home("acme.com", "https://jobs.lever.co/acme")})
    feeds(monkeypatch, lever={"acme": [IL]})
    out_path = workdir / "keep.json"
    code = survey.candidates_main(["--candidates", csv_path, "--keep", str(out_path)], companies)
    captured = capsys.readouterr()
    assert code == 0
    assert "Acme" in captured.out
    assert "Beta" in captured.out  # probed: nothing found
    assert "Known" not in captured.out
    assert "Secret Gone" not in captured.out + captured.err
    assert "secret-gone" not in captured.out + captured.err
    assert "Probed: 2" in captured.out
    assert "1 already in the list, 1 excluded (a count only)" in captured.out
    assert "probed 2/2" in captured.err
    assert [r["name"] for r in json.loads(out_path.read_text(encoding="utf-8"))] == ["Acme"]


def test_candidates_main_limit_and_no_keep_file(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    companies, csv_path = setup_run(workdir, CSV)
    serve(monkeypatch, {})
    feeds(monkeypatch)
    assert survey.candidates_main(["--candidates", csv_path, "--limit", "1"], companies) == 0
    out = capsys.readouterr().out
    assert "Probed: 1" in out
    assert "Beta" not in out
    for number in ("-3", "0"):
        assert survey.candidates_main(["--candidates", csv_path, "--limit", number], companies) == 0
        assert "Probed: 0" in capsys.readouterr().out
    assert not (workdir / "keep.json").exists()


def test_candidates_main_stops_when_the_list_is_missing(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    csv_path = write_csv(workdir / "c.csv", CSV)
    code = survey.candidates_main(["--candidates", csv_path], str(workdir / "nope.json"))
    err = capsys.readouterr().err
    assert code == 1
    assert "sync_supabase.py --pull" in err
    assert "SUPABASE_SERVICE_ROLE_KEY" in err
    assert "never paste the key" in err


def test_candidates_main_stops_on_a_list_or_a_file_it_cannot_trust(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    companies, csv_path = setup_run(workdir, CSV, extra=[excluded_record("Secret Gone Corp")])  # a name twice
    assert survey.candidates_main(["--candidates", csv_path], companies) == 1
    err = capsys.readouterr().err
    assert "stopped:" in err
    assert "twice" in err
    assert "Secret Gone" not in err
    companies, _ = setup_run(workdir, CSV)
    assert survey.candidates_main(["--candidates", str(workdir / "missing.csv")], companies) == 1
    assert "FileNotFoundError" in capsys.readouterr().err


def test_candidates_main_warns_when_no_excluded_record_is_visible(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    companies = workdir / "data" / "companies.json"
    write_json(companies, [{"name": "Known", "domain": "known.com", "crawl": True}])
    csv_path = write_csv(workdir / "c.csv", "name,domain,kind\nKnown,known.com,x\n")
    assert survey.candidates_main(["--candidates", csv_path], str(companies)) == 0
    captured = capsys.readouterr()
    assert "service-role key" in captured.err
    assert "service-role key" in captured.out


def test_candidates_main_needs_the_file_argument() -> None:
    with pytest.raises(SystemExit) as stop:
        survey.candidates_main([])
    assert stop.value.code == int("2")  # argparse's exit code for a usage error


def test_candidates_as_script(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, csv_path = setup_run(workdir, "name,domain,kind\nKnown,known.com,x\n")
    monkeypatch.setattr("sys.argv", ["survey.py", "--candidates", csv_path])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("survey", run_name="__main__")
    assert stop.value.code == 0
    assert "Probed: 0" in capsys.readouterr().out
