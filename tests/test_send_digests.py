"""send_digests.py (PLAN.md step 6.3): who is emailed, what is in the email, and that one failure never stops the rest.
Everything runs against fakes of Supabase (PostgREST) and Resend: no test reaches the network."""

import runpy
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import requests

import classify
import regions
import send_digests
from send_digests import (
    DigestError,
    Filters,
    Job,
    Resend,
    Services,
    Subscriber,
    Supabase,
    build_payload,
    matches,
    parse_filters,
    parse_job,
    parse_subscriber,
    parse_time,
    run,
    select_jobs,
    unsubscribe_token,
    unsubscribe_url,
)
from tests.fakes import FakeHttpResponse

REPO = Path(__file__).resolve().parents[1]
URL = "https://abc.supabase.co"
SECRET = "unsubscribe-test-secret-0123456789"
SERVICE_KEY = "service-role-key-xyz"
RESEND_KEY = "re_test_key_value"
ANN = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"
CAROL = "33333333-3333-4333-8333-333333333333"
MONDAY, SUNDAY = date(2030, 1, 7), date(2030, 1, 6)
TWO, MAX_KEY, HTTP_REDIRECT = 2, 64, 300
CONFIRMED = "2030-01-02T09:00:00+00:00"
BEFORE, AFTER = "2030-01-01T04:30:00", "2030-01-05T04:30:00"  # crawl times: before and after CONFIRMED
FIRST_RUN = "2029-12-01T04:30:00"  # the run that first saw the companies (their baseline)


def job_row(company: str, job_id: str, title: str, location: str = "Tel Aviv, Israel", **kw: Any) -> dict[str, Any]:
    """A row of the Supabase `jobs` table as the sync writes it (level, role types, regions: the real classifiers)."""
    place = regions.place(location)
    row: dict[str, Any] = {
        "company": company,
        "job_id": job_id,
        "title": title,
        "team": "Team",
        "location": location,
        "url": f"https://jobs.test/{company}/{job_id}",
        "status": "open",
        "first_seen": AFTER,
        "level": classify.level(title),
        "role_types": classify.role_types(title),
        "regions": list(place.regions),
        "locations": list(place.locations),
    }
    return {**row, **kw}


def subscription(user_id: str, address: str, **kw: Any) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "address": address,
        "frequency": "daily",
        "filters": {},
        "consent_confirmed_at": CONFIRMED,
        "unsubscribed_at": None,
        **kw,
    }


class FakeBackend:
    """The three tables of the digest (subscriptions, jobs, emailed) behind the `request` method of a requests session,
    answering the PostgREST queries send_digests.py makes: the filters it uses, paging, and an insert that ignores
    duplicates. `fail` names a (method, table) that answers HTTP 500; `raises` is raised by it (a network error)."""

    def __init__(self, page: int = 1000) -> None:
        self.subscriptions: list[dict[str, Any]] = []
        self.jobs: list[dict[str, Any]] = [
            job_row(c, "base", "Baseline Role", first_seen=FIRST_RUN) for c in ("Acme", "Beta", "Gamma")
        ]
        self.emailed: list[dict[str, str]] = []
        self.page = page
        self.calls: list[dict[str, Any]] = []
        self.fail: tuple[str, str] | None = None
        self.raises: tuple[str, str, Exception] | None = None
        self.bad_answer: tuple[str, Any] | None = None
        self.text_hits: dict[str, list[list[str]]] = {}  # the keys the full-text search finds, by query
        self.searched: list[str] = []  # the queries it was asked

    def request(self, method: str, url: str, **kw: Any) -> FakeHttpResponse:
        self.calls.append({"method": method, "url": url, **kw})
        table = url.rsplit("/", 1)[1]
        if self.raises and self.raises[:2] == (method, table):
            raise self.raises[2]
        if self.fail == (method, table):
            return FakeHttpResponse(status_code=500, text='{"message":"secret detail ann@example.test"}')
        if table == "search_job_keys":  # the database function: one dict in (not rows to insert), keys out
            assert method == "POST" and set(kw["json"]) == {"p_query"} and "Prefer" not in kw["headers"]
            self.searched.append(kw["json"]["p_query"])
            if self.bad_answer and self.bad_answer[0] == table:
                return FakeHttpResponse(data=self.bad_answer[1], json_error=self.bad_answer[1] is None)
            return FakeHttpResponse(data=self.text_hits.get(kw["json"]["p_query"], []))
        if method == "POST":
            for row in kw["json"]:
                if row not in self.emailed:  # resolution=ignore-duplicates
                    self.emailed.append(row)
            return FakeHttpResponse(status_code=201)
        if self.bad_answer and self.bad_answer[0] == table:
            return FakeHttpResponse(data=self.bad_answer[1], json_error=self.bad_answer[1] is None)
        params = kw["params"]
        rows: list[dict[str, Any]] = {"subscriptions": self.subscriptions, "jobs": self.jobs, "emailed": self.emailed}[
            table
        ]
        rows = [row for row in rows if self.keeps(row, params)]
        window = rows[params["offset"] : params["offset"] + min(params["limit"], self.page)]
        return FakeHttpResponse(data=window)

    @staticmethod
    def keeps(row: dict[str, Any], params: dict[str, Any]) -> bool:
        if params.get("consent_confirmed_at") == "not.is.null" and row["consent_confirmed_at"] is None:
            return False
        if params.get("unsubscribed_at") == "is.null" and row["unsubscribed_at"] is not None:
            return False
        wanted = params.get("user_id", "")
        return not wanted.startswith("eq.") or row["user_id"] == wanted.removeprefix("eq.")


class FakeResend:
    """The Resend API behind `request`: records every email, answers with the next status of `statuses` (else 200)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.statuses: list[int] = []
        self.refuse: Callable[[dict[str, Any]], int] = lambda _payload: 200  # status per email
        self.raises: Exception | None = None

    def request(self, method: str, url: str, **kw: Any) -> FakeHttpResponse:
        assert (method, url) == ("POST", send_digests.RESEND_URL)
        if self.raises:
            raise self.raises
        status = self.statuses.pop(0) if self.statuses else self.refuse(kw["json"])
        if status < HTTP_REDIRECT:
            self.sent.append(kw["json"])
            self.headers.append(kw["headers"])
        return FakeHttpResponse(status_code=status, text='{"message":"echo ann@example.test"}')


class Rig:
    def __init__(self) -> None:
        self.db = FakeBackend()
        self.mail = FakeResend()
        self.pauses: list[float] = []

    def client(self) -> Supabase:
        return Supabase(self.db, URL, SERVICE_KEY)  # type: ignore[arg-type]

    def go(self, today: date = MONDAY, dry_run: bool = False) -> send_digests.Summary:
        resend = Resend(self.mail, RESEND_KEY, self.pauses.append)  # type: ignore[arg-type]
        return run(Services(self.client(), resend, URL, SECRET, self.pauses.append), today, dry_run)


@pytest.fixture
def rig() -> Rig:
    return Rig()


def subjects(rig: Rig) -> list[str]:
    return [email["subject"] for email in rig.mail.sent]


# ---- who is emailed ------------------------------------------------------------------------------


def test_a_confirmed_subscriber_gets_the_matching_jobs_that_appeared_after_confirming(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [
        job_row("Acme", "new", "Product Manager"),
        job_row("Beta", "old", "Product Manager", first_seen=BEFORE),  # before the confirmation: the backlog
        job_row("Gamma", "closed", "Product Manager", status="closed"),
        job_row("Gamma", "abroad", "Product Manager", location="London, UK"),
    ]
    summary = rig.go()
    assert (summary.due, summary.sent, summary.jobs_sent, summary.failed) == (1, 1, 1, 0)
    (email,) = rig.mail.sent
    assert email["to"] == ["ann@example.test"]
    assert "Acme - Product Manager (Tel Aviv, Israel)\nhttps://jobs.test/Acme/new" in email["text"]
    assert "Beta" not in email["text"] and "London" not in email["text"] and "Baseline" not in email["text"]


def test_a_job_first_seen_exactly_at_the_confirmation_is_not_after_it(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "a@example.test", consent_confirmed_at="2030-01-05T04:30:00+00:00")]
    rig.db.jobs += [job_row("Acme", "same", "Product Manager", first_seen="2030-01-05T04:30:00")]
    assert rig.go().sent == 0


def test_the_confirmation_time_is_compared_as_utc_whatever_zone_it_is_written_in(rig: Rig) -> None:
    # 06:00 in Israel (+02:00) is 04:00 UTC: a crawl at 04:30 UTC is after it, one at 03:30 UTC before it.
    rig.db.subscriptions = [subscription(ANN, "a@example.test", consent_confirmed_at="2030-01-05T06:00:00+02:00")]
    rig.db.jobs += [
        job_row("Acme", "late", "Product Manager", first_seen="2030-01-05T04:30:00"),
        job_row("Acme", "early", "Product Manager Intern", first_seen="2030-01-05T03:30:00"),
    ]
    rig.go()
    assert "Acme/late" in rig.mail.sent[0]["text"] and "Acme/early" not in rig.mail.sent[0]["text"]


def test_nobody_who_has_not_confirmed_or_has_unsubscribed_is_emailed(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "ann@example.test"),
        subscription(BOB, "bob@example.test", consent_confirmed_at=None),
        subscription(CAROL, "carol@example.test", unsubscribed_at="2030-01-04T00:00:00+00:00", filters={}),
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    summary = rig.go()
    assert (summary.due, summary.sent) == (1, 1)
    assert [email["to"] for email in rig.mail.sent] == [["ann@example.test"]]


def test_no_email_when_nothing_is_new_and_the_database_is_not_written(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    summary = rig.go()
    assert (summary.due, summary.nothing_new, summary.sent) == (1, 1, 0)
    assert rig.mail.sent == [] and rig.db.emailed == []
    assert [c["method"] for c in rig.db.calls if c["method"] == "POST"] == []


def test_no_subscriber_means_no_job_read_and_no_email(rig: Rig) -> None:
    summary = rig.go()
    assert (summary.due, summary.sent, summary.failed) == (0, 0, 0)
    assert [c["url"].rsplit("/", 1)[1] for c in rig.db.calls] == ["subscriptions"]


def test_a_job_is_never_emailed_twice(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    assert rig.go().sent == 1
    assert rig.db.emailed == [{"user_id": ANN, "company": "Acme", "job_id": "new"}]
    assert rig.go().sent == 0 and len(rig.mail.sent) == 1  # the second run finds nothing new
    rig.db.jobs += [job_row("Acme", "newer", "Product Manager 2")]
    assert rig.go().sent == 1
    assert "newer" in rig.mail.sent[1]["text"] and "Acme/new\n" not in rig.mail.sent[1]["text"]


def test_what_was_emailed_to_one_person_is_still_new_to_another(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test"), subscription(BOB, "bob@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.db.emailed = [{"user_id": ANN, "company": "Acme", "job_id": "new"}]
    summary = rig.go()
    assert (summary.sent, summary.nothing_new) == (1, 1)
    assert rig.mail.sent[0]["to"] == ["bob@example.test"]


def test_a_company_seen_for_the_first_time_is_a_silent_baseline_not_a_flood(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [
        job_row("Newco", "1", "Product Manager", first_seen=AFTER),  # Newco's first crawl: all of this is baseline
        job_row("Newco", "2", "Product Manager Intern", first_seen=AFTER, status="closed"),
        job_row("Acme", "later", "Product Manager"),  # a company already known: a real new job
    ]
    run_ = rig.go()
    assert run_.jobs_sent == 1
    assert "Acme/later" in rig.mail.sent[0]["text"] and "Newco" not in rig.mail.sent[0]["text"]
    rig.db.jobs.append(job_row("Newco", "3", "Product Manager", first_seen="2030-01-06T04:30:00"))
    rig.go(today=date(2030, 1, 8))
    assert "Newco/3" in rig.mail.sent[1]["text"]  # a later job of that company is new


# ---- the filters (FR-9: level and role type are separate) ----------------------------------------


FILTER_JOBS = [
    ("pm", "Product Manager"),
    ("pm-intern", "Product Manager Intern"),
    ("pm-junior", "Junior Product Manager"),
    ("dev-intern", "Software Engineer Intern"),
    ("dev-junior", "Junior Backend Developer"),
    ("design", "Product Designer"),
    ("sales", "Account Executive"),
]


def emailed_ids(rig: Rig, filters: dict[str, Any]) -> set[str]:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test", filters=filters)]
    rig.db.jobs = [job_row("Acme", "base", "Baseline Role", first_seen=FIRST_RUN)] + [
        job_row("Acme", job_id, title) for job_id, title in FILTER_JOBS
    ]
    rig.mail.sent.clear()
    rig.db.emailed.clear()
    rig.go()
    text = rig.mail.sent[0]["text"] if rig.mail.sent else ""
    return {job_id for job_id, _title in FILTER_JOBS if f"Acme/{job_id}\n" in text + "\n"}


def test_product_with_intern_and_junior_gives_only_the_entry_level_product_roles(rig: Rig) -> None:
    got = emailed_ids(rig, {"role_types": ["Product"], "levels": ["Intern", "Junior"]})
    assert got == {"pm-intern", "pm-junior"}  # the old digest also sent the Intern and Junior roles of every field


def test_product_with_every_level_gives_product_roles_at_any_seniority(rig: Rig) -> None:
    got = emailed_ids(rig, {"role_types": ["Product"], "levels": ["Intern", "Junior", "Not specified"]})
    assert got == {"pm", "pm-intern", "pm-junior", "design"}  # Product Designer is Product and Design
    assert emailed_ids(rig, {"role_types": ["Product"]}) == got  # no level chosen means every level


def test_levels_alone_and_role_types_alone(rig: Rig) -> None:
    assert emailed_ids(rig, {"levels": ["Intern"]}) == {"pm-intern", "dev-intern"}
    assert emailed_ids(rig, {"role_types": ["Design"]}) == {"design"}
    assert emailed_ids(rig, {"role_types": ["Sales & BD", "Design"]}) == {"sales", "design"}


def test_nothing_chosen_means_every_job_in_israel_and_the_other_role_type_is_never_dropped(rig: Rig) -> None:
    everything = {job_id for job_id, _ in FILTER_JOBS}
    assert emailed_ids(rig, {}) == everything
    assert emailed_ids(rig, {"regions": [], "levels": [], "role_types": [], "keywords": ""}) == everything
    rig.db.subscriptions = [subscription(ANN, "ann@example.test", filters={"role_types": ["Other"]})]
    rig.db.jobs.append(job_row("Acme", "misc", "Office Dog Trainer"))
    rig.mail.sent.clear()
    rig.go()
    assert "Acme/misc" in rig.mail.sent[0]["text"] and "Acme/pm\n" not in rig.mail.sent[0]["text"]


def test_regions_and_keywords_follow_the_dashboard_rules(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "ann@example.test", filters={"regions": ["Haifa and the north"], "keywords": "ACME  manager"})
    ]
    rig.db.jobs += [
        job_row("Acme", "haifa", "Product Manager", location="Haifa, Israel"),
        job_row("Acme", "tlv", "Product Manager", location="Tel Aviv, Israel"),
        job_row("Beta", "haifa2", "Product Manager", location="Haifa, Israel"),  # keyword "acme" is not in it
        job_row("Acme", "haifa3", "Product Analyst", location="Haifa, Israel"),  # keyword "manager" is not in it
    ]
    rig.go()
    text = rig.mail.sent[0]["text"]
    assert "Acme/haifa\n" in text
    assert all(f"/{other}\n" not in text for other in ("tlv", "haifa2", "haifa3"))


def test_a_job_with_a_place_in_israel_but_no_region_counts_as_in_israel(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "odd", "Product Manager", location="Israel, Ein Gedi Resort")]
    rig.go()
    assert "Acme/odd" in rig.mail.sent[0]["text"]


def test_unknown_jobs_follow_a_chosen_region_only_when_unknown_is_chosen(rig: Rig) -> None:
    """A job whose place is Unknown is in Israel for the digest: a subscriber with no region chosen gets it, and one who
    picked regions gets it only by picking Unknown."""
    rig.db.jobs += [
        job_row("Acme", "nowhere", "Product Manager", location=""),
        job_row("Acme", "hebrew", "Product Manager", location="גוש דן"),
        job_row("Acme", "haifa", "Product Manager", location="Haifa, Israel"),
    ]
    assert emailed_ids_of(rig, {}) >= {"nowhere", "hebrew", "haifa"}
    assert emailed_ids_of(rig, {"regions": ["Haifa and the north"]}) == {"haifa"}
    assert emailed_ids_of(rig, {"regions": ["Unknown"]}) == {"nowhere", "hebrew"}
    assert emailed_ids_of(rig, {"regions": ["Unknown", "Haifa and the north"]}) == {"nowhere", "hebrew", "haifa"}


def emailed_ids_of(rig: Rig, filters: dict[str, Any]) -> set[str]:
    """The ids of the Acme jobs of this test that one subscriber with these filters is emailed."""
    rig.db.subscriptions = [subscription(ANN, "ann@example.test", filters=filters)]
    rig.mail.sent.clear()
    rig.db.emailed.clear()
    rig.go()
    text = rig.mail.sent[0]["text"] if rig.mail.sent else ""
    return {job_id for job_id in ("nowhere", "hebrew", "haifa") if f"Acme/{job_id}\n" in text + "\n"}


def test_matches_unit() -> None:
    job = Job(
        "Acme",
        "1",
        "PM",
        "Growth",
        "Tel Aviv",
        "u",
        datetime(2030, 1, 5, tzinfo=UTC),
        "Junior",
        ("Product",),
        ("Center",),
        (),
    )
    assert matches(job, Filters())
    assert matches(
        job, Filters(regions=frozenset({"Center", "South"}), levels=frozenset({"Junior"}), terms=("growth",))
    )
    assert not matches(job, Filters(regions=frozenset({"South"})))
    assert not matches(job, Filters(levels=frozenset({"Intern"})))
    assert not matches(job, Filters(role_types=frozenset({"Design"})))
    assert not matches(job, Filters(terms=("growth", "nope")))
    other = Job(
        "Acme",
        "2",
        "x",
        "",
        "",
        "",
        datetime(2030, 1, 5, tzinfo=UTC),
        "Not specified",
        (),
        (),
        ("Israel, city not given",),
    )
    assert matches(other, Filters(role_types=frozenset({"Other"})))
    assert not matches(other, Filters(role_types=frozenset({"Product"})))


# ---- daily and weekly ----------------------------------------------------------------------------


def test_weekly_is_one_summary_on_sunday_and_daily_every_day(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "daily@example.test"),
        subscription(BOB, "weekly@example.test", frequency="weekly"),
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    weekdays = [date(2030, 1, 7 + i) for i in range(7)]  # Monday to Sunday
    assert weekdays[-1].weekday() == send_digests.WEEKLY_DAY
    for position, day in enumerate(weekdays[:-1]):
        summary = rig.go(today=day)
        assert summary.not_due == 1, day  # the weekly one waits
        assert summary.due == 1
        rig.db.emailed.clear()  # let the daily subscriber be emailed again each day for this test
        assert len(rig.mail.sent) == position + 1
    summary = rig.go(today=weekdays[-1])
    assert (summary.due, summary.not_due, summary.sent) == (2, 0, 2)
    weekly = [email for email in rig.mail.sent if email["to"] == ["weekly@example.test"]]
    assert len(weekly) == 1 and weekly[0]["subject"].startswith("Misra weekly: ")
    assert "this weekly email because" in weekly[0]["text"]


def test_a_weekly_summary_gathers_what_appeared_during_the_week_and_is_empty_on_a_quiet_week(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(BOB, "weekly@example.test", frequency="weekly")]
    rig.db.jobs += [
        job_row("Acme", "mon", "Product Manager", first_seen="2030-01-07T04:30:00"),
        job_row("Beta", "thu", "Product Manager Intern", first_seen="2030-01-10T04:30:00"),
        job_row("Beta", "gone", "Product Manager", first_seen="2030-01-08T04:30:00", status="closed"),
    ]
    rig.go(today=date(2030, 1, 13))
    assert rig.mail.sent[0]["subject"] == "Misra weekly: 2 new matching roles"
    assert rig.go(today=date(2030, 1, 20)).sent == 0  # nothing new next Sunday: no email


# ---- failures ------------------------------------------------------------------------------------


def test_one_failure_never_stops_the_others_and_the_failed_user_is_not_recorded(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "ann@example.test"),
        subscription(BOB, "bad@example.test"),
        subscription(CAROL, "carol@example.test"),
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.mail.refuse = lambda payload: 422 if payload["to"] == ["bad@example.test"] else 200
    summary = rig.go()
    assert (summary.sent, summary.failed, summary.jobs_sent) == (2, 1, 2)
    assert [email["to"][0] for email in rig.mail.sent] == ["ann@example.test", "carol@example.test"]
    assert {row["user_id"] for row in rig.db.emailed} == {ANN, CAROL}  # Bob's job is tried again next run
    assert summary.failures == ["subscriber 2 of 3: Resend answered HTTP 422"]


def test_nothing_is_recorded_when_resend_does_not_accept_the_email(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    for failure in (
        lambda: setattr(rig.mail, "statuses", [500]),
        lambda: setattr(rig.mail, "raises", requests.ConnectionError("x")),
    ):
        failure()
        summary = rig.go()
        assert (summary.sent, summary.failed) == (0, 1)
        assert rig.db.emailed == []
        rig.mail.raises = None
    assert rig.go().sent == 1  # the next run delivers it


def test_a_recording_failure_after_the_send_is_a_failure_that_names_what_happened(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test"), subscription(BOB, "bob@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.db.fail = ("POST", "emailed")
    summary = rig.go()
    assert (summary.sent, summary.failed) == (2, 2)  # both were sent; neither could be recorded
    assert (
        summary.failures[0] == "subscriber 1 of 2: the email was sent but not recorded (POST emailed failed: HTTP 500)"
    )


def test_an_unexpected_error_for_one_user_is_counted_and_the_others_go_on(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test"), subscription(BOB, "bob@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.mail.refuse = lambda payload: (
        (_ for _ in ()).throw(KeyError("secret ann@example.test")) if payload["to"] == ["ann@example.test"] else 200
    )
    summary = rig.go()
    assert (summary.sent, summary.failed) == (1, 1)
    assert summary.failures == ["subscriber 1 of 2: KeyError"]  # the type only: the message could hold an address


def test_a_malformed_subscription_is_skipped_and_counted(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "ann@example.test", filters={"levels": "Intern"}),
        subscription(BOB, "bob@example.test"),
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    summary = rig.go()
    assert (summary.sent, summary.failed) == (1, 1)
    assert summary.failures == ["a subscription was skipped: the saved levels filter is not a list of names"]


def test_a_broken_job_row_is_skipped_and_counted_and_never_stops_the_digest(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [
        job_row("Acme", "new", "Product Manager"),
        job_row("Acme", "bad", "Product Manager", first_seen=None),
    ]
    summary = rig.go()
    assert (summary.sent, summary.failed) == (1, 1)
    assert summary.failures == ["a job was skipped: a timestamp is missing"]


def test_consent_withdrawn_or_the_address_changed_during_the_run_is_honoured_before_the_send(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "ann@example.test"),
        subscription(BOB, "bob@example.test"),
        subscription(CAROL, "carol@example.test"),
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]

    def ann_sends(_payload: dict[str, Any]) -> int:
        bob, carol = rig.db.subscriptions[1], rig.db.subscriptions[2]
        bob["unsubscribed_at"] = "2030-01-07T05:00:00+00:00"  # Bob unsubscribes while Ann's email goes out
        carol["address"] = "carol-new@example.test"  # Carol changes her address (it must be confirmed again)
        carol["consent_confirmed_at"] = None
        return 200

    rig.mail.refuse = ann_sends
    summary = rig.go()
    assert (summary.sent, summary.withdrawn, summary.failed) == (1, 2, 0)
    assert [email["to"] for email in rig.mail.sent] == [["ann@example.test"]]
    assert {row["user_id"] for row in rig.db.emailed} == {ANN}


def test_an_address_changed_but_already_confirmed_again_is_a_different_consent_and_waits_for_the_next_run(
    rig: Rig,
) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test"), subscription(BOB, "bob@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]

    def ann_sends(_payload: dict[str, Any]) -> int:
        rig.db.subscriptions[1]["address"] = "bob-new@example.test"
        return 200

    rig.mail.refuse = ann_sends
    assert (rig.go().sent, rig.go().sent) == (1, 1)
    assert [email["to"][0] for email in rig.mail.sent] == ["ann@example.test", "bob-new@example.test"]


def test_an_unsubscribe_during_the_send_leaves_no_emailed_rows_behind(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]

    def ann_unsubscribes(_payload: dict[str, Any]) -> int:
        rig.db.subscriptions[0]["unsubscribed_at"] = "2030-01-07T05:00:00+00:00"
        return 200

    rig.mail.refuse = ann_unsubscribes
    summary = rig.go()
    assert (summary.sent, summary.failed) == (1, 0)  # it was already on its way
    assert rig.db.emailed == []


def test_a_subscription_that_is_still_valid_is_emailed_and_recorded(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    summary = rig.go()
    assert (summary.sent, summary.withdrawn) == (1, 0)
    assert len(rig.db.emailed) == 1


def test_a_429_is_retried_once_after_a_pause_and_the_sends_are_spaced(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "a@example.test"), subscription(BOB, "b@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.mail.statuses = [429, 200, 200]
    summary = rig.go()
    assert (summary.sent, summary.failed) == (2, 0)
    assert rig.pauses == [send_digests.RETRY_SECONDS, send_digests.SEND_INTERVAL]
    rig.db.emailed.clear()
    rig.mail.statuses = [429, 429]
    assert rig.go().failed == 2 - 1  # the first user fails after two 429s; the second then succeeds


def test_reading_the_subscribers_or_the_jobs_failing_stops_the_run(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.fail = ("GET", "subscriptions")
    with pytest.raises(DigestError, match=r"GET subscriptions failed: HTTP 500$"):
        rig.go()
    rig.db.fail = ("GET", "jobs")
    with pytest.raises(DigestError, match=r"GET jobs failed: HTTP 500$"):
        rig.go()
    assert rig.mail.sent == []


def test_reading_what_was_emailed_failing_skips_that_user_not_the_run(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.db.fail = ("GET", "emailed")
    summary = rig.go()
    assert (summary.sent, summary.failed) == (0, 1)  # without the list the job could be sent twice, so nothing is sent
    assert rig.mail.sent == []


# ---- the email -----------------------------------------------------------------------------------


def test_dry_run_sends_and_records_nothing(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test"), subscription(BOB, "bob@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    summary = rig.go(dry_run=True)
    assert (summary.sent, summary.jobs_sent, summary.failed) == (0, 2, 0)
    assert rig.mail.sent == [] and rig.db.emailed == [] and rig.pauses == []
    assert send_digests.report(summary, True).endswith("would send 2 jobs, 0 failed")


def test_every_email_has_the_one_click_headers_the_reply_to_the_project_from_and_the_footer(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(ANN, "ann@example.test"),
        subscription(BOB, "bob@example.test", frequency="weekly"),
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.go(today=SUNDAY)
    assert len(rig.mail.sent) == TWO
    for email, user_id, frequency in zip(rig.mail.sent, (ANN, BOB), ("daily", "weekly"), strict=True):
        url = f"{URL}/functions/v1/unsubscribe?t={unsubscribe_token(SECRET, user_id)}"
        assert email["headers"] == {
            "List-Unsubscribe": f"<{url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
        assert email["from"] == "Misra <digest@send.avidor.org>"
        assert email["reply_to"] == "raanan@avidor.org"
        assert email["html"].startswith("<!doctype html>") and url in email["html"]  # multipart: text and HTML
        assert email["text"].endswith(send_digests.render_footer(frequency, url))
        assert f"Unsubscribe in one click (no sign-in needed):\n{url}\n" in email["text"]
        assert f"this {frequency} email because you confirmed" in email["text"]


def test_the_email_has_job_listings_only_never_coverage_gaps(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.go()
    text = rig.mail.sent[0]["text"].lower()
    for word in ("coverage", "crawl", "not crawled", "gap", "robots"):
        assert word not in text
    assert text.startswith("1 new role matching your filters:\n\nacme - product manager (tel aviv, israel)\nhttps://")
    assert rig.mail.sent[0]["subject"] == "Misra: 1 new matching role"


def test_a_job_without_a_place_or_a_link_still_reads_well() -> None:
    subscriber = Subscriber(ANN, "a@example.test", "daily", datetime(2030, 1, 1, tzinfo=UTC), Filters())
    jobs = [
        Job(
            "Acme", "1", "Role  One", "", "", "", datetime(2030, 1, 5, tzinfo=UTC), "Not specified", (), (), ("Israel",)
        ),
        Job("Beta", "2", "Role Two", "", "Haifa", "https://x.test/2", datetime(2030, 1, 5, tzinfo=UTC), "", (), (), ()),
    ]
    payload = build_payload(subscriber, jobs, "https://u.test/?t=x")
    assert payload["subject"] == "Misra: 2 new matching roles"
    assert payload["text"].startswith(
        "2 new roles matching your filters:\n\nAcme - Role  One\n\nBeta - Role Two (Haifa)\nhttps://x.test/2\n\n---\n"
    )


def test_the_idempotency_key_depends_on_the_user_and_the_jobs(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test"), subscription(BOB, "bob@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.go()
    keys = [headers["Idempotency-Key"] for headers in rig.mail.headers]
    assert len(set(keys)) == TWO
    assert all(key.startswith("digest-") and len(key) <= MAX_KEY for key in keys)
    assert rig.mail.headers[0]["Authorization"] == f"Bearer {RESEND_KEY}"


def test_the_service_key_goes_only_in_headers_to_supabase_and_never_to_resend(rig: Rig) -> None:
    rig.db.subscriptions = [subscription(ANN, "ann@example.test")]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.go()
    assert rig.db.calls and all(call["headers"]["apikey"] == SERVICE_KEY for call in rig.db.calls)
    assert all(SERVICE_KEY not in str(call["params"]) + str(call["json"]) for call in rig.db.calls)
    assert all(
        SERVICE_KEY not in str(email) and SERVICE_KEY not in str(h)
        for email, h in zip(rig.mail.sent, rig.mail.headers, strict=True)
    )
    assert all(call["allow_redirects"] is False for call in rig.db.calls)


# ---- the texts of supabase/email-texts.md --------------------------------------------------------


def email_texts() -> str:
    return (REPO / "supabase" / "email-texts.md").read_text(encoding="utf-8")


def block(section: str) -> str:
    """The text inside the code fence that follows the `## ` heading of a section of email-texts.md."""
    body = email_texts().split(f"## {section}", 1)[1]
    return body.split("```text\n", 1)[1].split("```", 1)[0]


def test_the_digest_footer_is_exactly_the_text_in_email_texts() -> None:
    assert block("Digest footer") == send_digests.FOOTER
    filled = (
        block("Digest footer").replace("{{schedule}}", "weekly").replace("{{unsubscribe_url}}", "https://u.test/?t=x")
    )
    assert send_digests.render_footer("weekly", "https://u.test/?t=x") == filled


def test_the_headers_the_sender_and_the_reply_to_are_exactly_the_texts_in_email_texts() -> None:
    headers = block("Headers of every digest email")
    url = unsubscribe_url("https://dncodqeqpoqwcxdctzco.supabase.co", SECRET, ANN)
    token = unsubscribe_token(SECRET, ANN)
    expected = headers.replace("{{token}}", token)
    assert expected == f"List-Unsubscribe: <{url}>\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\n"
    payload = build_payload(
        Subscriber(ANN, "a@example.test", "daily", datetime(2030, 1, 1, tzinfo=UTC), Filters()), [], url
    )
    assert "\n".join(f"{name}: {value}" for name, value in payload["headers"].items()) + "\n" == expected
    assert f"`{send_digests.FROM}`" in email_texts() and "Reply-To: raanan@avidor.org" in email_texts()
    assert payload["from"] == send_digests.FROM and payload["reply_to"] == send_digests.REPLY_TO


def test_the_digest_subject_and_the_list_are_in_email_texts() -> None:
    section = email_texts().split("## Digest subject and list", 1)[1].split("\n## ", 1)[0]
    assert "Subject (daily): `Misra: {{count}} new matching {{roles}}`" in section
    assert "Subject (weekly): `Misra weekly: {{count}} new matching {{roles}}`" in section
    text = block("Digest subject and list")
    subject, body = send_digests.render_digest(
        "daily",
        [Job("Acme", "1", "PM", "", "Haifa", "https://x.test/1", datetime(2030, 1, 1, tzinfo=UTC), "", (), (), ())],
        "U",
    )
    expected = (
        text.replace("{{count}}", "1")
        .replace("{{roles}}", "role")
        .replace("{{company}}", "Acme")
        .replace("{{title}}", "PM")
        .replace("{{location}}", "Haifa")
        .replace("{{url}}", "https://x.test/1")
    )
    assert body.startswith(expected)
    assert subject == "Misra: 1 new matching role"


# ---- the unsubscribe token -----------------------------------------------------------------------


def test_the_unsubscribe_token_is_the_id_and_an_hmac_and_matches_the_edge_function_vector() -> None:
    # The same vector is pinned in test/unsubscribe.test.ts, which checks it with the Edge Function's own code.
    vector = "11111111-1111-4111-8111-111111111111.3ee0be7f8d9d03d8e80efd19d45c303cf23278727854c5d5a88f1694bcbc0f88"
    assert unsubscribe_token(SECRET, ANN) == vector
    assert unsubscribe_token(SECRET, BOB) != unsubscribe_token(SECRET, ANN)
    assert unsubscribe_token("another-secret-0123456789abcdefghij", ANN) != vector
    assert unsubscribe_url(URL + "/", SECRET, ANN) == f"{URL}/functions/v1/unsubscribe?t={vector}"


# ---- parsing -------------------------------------------------------------------------------------


def test_parse_time_reads_naive_as_utc_and_converts_zones() -> None:
    assert parse_time("2030-01-05T04:30:00") == datetime(2030, 1, 5, 4, 30, tzinfo=UTC)
    assert parse_time("2030-01-05T06:30:00+02:00") == datetime(2030, 1, 5, 4, 30, tzinfo=UTC)
    assert parse_time("2030-01-05T04:30:00.123456Z") == datetime(2030, 1, 5, 4, 30, 0, 123456, tzinfo=UTC)
    for bad in (None, 5, "yesterday", ""):
        with pytest.raises(DigestError):
            parse_time(bad)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "not an object"),
        (None, "not an object"),
        ({"keywords": 5}, "keywords are not text"),
        ({"regions": "Center"}, "regions filter is not a list"),
        ({"role_types": [1]}, "role types filter is not a list"),
    ],
)
def test_parse_filters_refuses_what_is_not_the_saved_shape(raw: object, message: str) -> None:
    with pytest.raises(DigestError, match=message):
        parse_filters(raw)


def test_parse_filters_reads_the_four_filters_and_ignores_the_missing_ones() -> None:
    assert parse_filters({}) == Filters()
    assert parse_filters(
        {"regions": ["Center"], "levels": ["Intern"], "role_types": ["Product"], "keywords": " Growth  PM "}
    ) == Filters(frozenset({"Center"}), frozenset({"Intern"}), frozenset({"Product"}), ("growth", "pm"))


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": None},
        {"address": ""},
        {"address": 5},
        {"frequency": "hourly"},
        {"consent_confirmed_at": None},
        {"filters": "x"},
    ],
)
def test_parse_subscriber_refuses_a_broken_row(change: dict[str, Any]) -> None:
    with pytest.raises(DigestError):
        parse_subscriber({**subscription(ANN, "ann@example.test"), **change})


def test_parse_job_cleans_text_and_tolerates_missing_columns() -> None:
    job = parse_job(
        {
            "company": " Acme ",
            "job_id": 7,
            "title": "A\n B",
            "first_seen": AFTER,
            "role_types": "bad",
            "regions": ["Center", 3],
        }
    )
    assert (job.company, job.job_id, job.title, job.team, job.url) == ("Acme", "7", "A B", "", "")
    assert (job.role_types, job.regions, job.locations) == ((), ("Center",), ())


def test_select_jobs_orders_by_company_then_title() -> None:
    subscriber = Subscriber(ANN, "a@example.test", "daily", datetime(2030, 1, 1, tzinfo=UTC), Filters())
    seen = datetime(2030, 1, 5, tzinfo=UTC)
    jobs = [
        Job(c, i, t, "", "", "", seen, "", (), ("Center",), ())
        for c, i, t in (("beta", "1", "B"), ("Acme", "2", "z"), ("Acme", "3", "A"))
    ]
    assert [(j.company, j.title) for j in select_jobs(subscriber, jobs, set())] == [
        ("Acme", "A"),
        ("Acme", "z"),
        ("beta", "B"),
    ]


# ---- the two clients -----------------------------------------------------------------------------


def test_supabase_needs_https_and_keeps_error_text_to_the_status(rig: Rig) -> None:
    with pytest.raises(DigestError, match="https://"):
        Supabase(rig.db, "http://abc.supabase.co", SERVICE_KEY)  # type: ignore[arg-type]
    rig.db.fail = ("GET", "jobs")
    with pytest.raises(DigestError) as caught:
        rig.client().jobs()
    assert str(caught.value) == "GET jobs failed: HTTP 500"  # the body held an address
    rig.db.fail = None
    rig.db.raises = ("GET", "jobs", requests.ConnectionError("https://abc.supabase.co?apikey=service-role-key-xyz"))
    with pytest.raises(DigestError) as caught:
        rig.client().jobs()
    assert str(caught.value) == "GET jobs failed: ConnectionError"


def test_reads_page_through_a_server_that_caps_a_response_below_the_page_size(rig: Rig) -> None:
    rig.db.page = 3
    rig.db.jobs += [job_row("Acme", f"j{i}", "Product Manager") for i in range(10)]
    assert len(rig.client().jobs()) == 3 + 10
    offsets = [call["params"]["offset"] for call in rig.db.calls]
    assert offsets == [0, 3, 6, 9, 12, 13]


def test_a_read_that_is_not_a_list_or_not_json_is_an_error(rig: Rig) -> None:
    rig.db.bad_answer = ("jobs", {"message": "x"})
    with pytest.raises(DigestError, match="something other than a list"):
        rig.client().jobs()
    rig.db.bad_answer = ("jobs", None)
    with pytest.raises(DigestError, match="not JSON"):
        rig.client().jobs()


def test_resend_error_text_is_the_status_only(rig: Rig) -> None:
    rig.mail.statuses = [403]
    resend = Resend(rig.mail, RESEND_KEY)  # type: ignore[arg-type]
    with pytest.raises(DigestError) as caught:
        resend.send({"to": ["ann@example.test"]}, "k")
    assert str(caught.value) == "Resend answered HTTP 403"
    rig.mail.raises = requests.Timeout("https://api.resend.com re_test_key_value")
    with pytest.raises(DigestError) as caught:
        resend.send({}, "k")
    assert str(caught.value) == "Resend failed: Timeout"


# ---- main ----------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, rig: Rig) -> Rig:
    for name, value in zip(send_digests.SECRETS, (URL, SERVICE_KEY, RESEND_KEY, SECRET), strict=True):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["send_digests.py"])
    monkeypatch.setattr(requests, "Session", lambda: Combined(rig))
    monkeypatch.setattr(send_digests.time, "sleep", lambda _s: None)
    return rig


class Combined:
    """One requests session standing in for both services, told apart by URL."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def request(self, method: str, url: str, **kw: Any) -> FakeHttpResponse:
        return (self.rig.mail if url == send_digests.RESEND_URL else self.rig.db).request(method, url, **kw)


def test_main_sends_and_prints_counts_only(env: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    env.db.subscriptions = [subscription(ANN, "ann@example.test")]
    env.db.jobs += [job_row("Acme", "new", "Product Manager")]
    send_digests.main()
    out = capsys.readouterr()
    assert (
        out.out.strip()
        == "digests: 1 due, 0 not due today, 0 with nothing new, 0 withdrawn, sent 1 jobs in 1 emails, 0 failed"
    )
    for private in ("ann@example.test", ANN, SERVICE_KEY, RESEND_KEY, SECRET, "Product Manager"):
        assert private not in out.out + out.err


def test_main_dry_run_changes_nothing(
    env: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["send_digests.py", "--dry-run"])
    env.db.subscriptions = [subscription(ANN, "ann@example.test")]
    env.db.jobs += [job_row("Acme", "new", "Product Manager")]
    send_digests.main()
    assert "would send 1 jobs" in capsys.readouterr().out
    assert env.mail.sent == [] and env.db.emailed == []


def test_main_exits_non_zero_after_all_users_when_one_failed_and_never_prints_the_address(
    env: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    env.db.subscriptions = [subscription(ANN, "bad@example.test"), subscription(BOB, "bob@example.test")]
    env.db.jobs += [job_row("Acme", "new", "Product Manager")]
    env.mail.refuse = lambda payload: 422 if payload["to"] == ["bad@example.test"] else 200
    with pytest.raises(SystemExit) as caught:
        send_digests.main()
    assert caught.value.code == 1
    out = capsys.readouterr()
    assert "sent 1 jobs in 1 emails, 1 failed" in out.out
    assert "::error::subscriber 1 of 2: Resend answered HTTP 422" in out.out
    assert "example.test" not in out.out + out.err


def test_main_stops_with_a_clear_message_when_reading_fails(env: Rig) -> None:
    env.db.fail = ("GET", "subscriptions")
    with pytest.raises(SystemExit, match="digests failed: GET subscriptions failed: HTTP 500"):
        send_digests.main()


def test_main_skips_when_every_secret_is_empty_and_names_the_empty_ones_otherwise(
    env: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RESEND_API_KEY", " ")
    with pytest.raises(SystemExit, match=r"these are empty: RESEND_API_KEY$"):
        send_digests.main()
    for name in send_digests.SECRETS:
        monkeypatch.setenv(name, "")
    send_digests.main()
    assert "skipping the digests" in capsys.readouterr().out
    assert env.db.calls == [] and env.mail.sent == []


def test_main_refuses_a_short_unsubscribe_secret(env: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "short")
    with pytest.raises(SystemExit, match="at least 32 characters"):
        send_digests.main()
    assert env.db.calls == []


def test_running_the_file_as_a_script_runs_main(env: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_module("send_digests", run_name="__main__")
    assert "digests: 0 due" in capsys.readouterr().out


# ---- keywords in the full text of the posting (step 7.3) -----------------------------------------


def keyword_sub(user_id: str, keywords: str) -> dict[str, Any]:
    return subscription(user_id, f"{user_id[:1]}@example.test", filters={"keywords": keywords})


def test_a_job_whose_full_text_has_the_keywords_is_emailed_though_its_title_does_not(rig: Rig) -> None:
    rig.db.subscriptions = [keyword_sub(ANN, "Rust  rockets")]
    rig.db.jobs += [
        job_row("Acme", "title", "Rust rockets engineer"),  # in the title: no search needed for it
        job_row("Acme", "text", "Software Engineer"),  # only in the posting
        job_row("Acme", "no", "Office Dog Trainer"),
    ]
    rig.db.text_hits = {"rust rockets": [["Acme", "text"], ["Acme", "gone"]]}  # the words, one space apart
    summary = rig.go()
    assert rig.db.searched == ["rust rockets"]
    assert (summary.sent, summary.jobs_sent, summary.failed) == (1, 2, 0)
    text = rig.mail.sent[0]["text"]
    assert "Acme/title\n" in text and "Acme/text\n" in text and "Acme/no\n" not in text


def test_the_full_text_keys_do_not_get_past_the_other_filters(rig: Rig) -> None:
    rig.db.subscriptions = [
        subscription(
            ANN,
            "ann@example.test",
            filters={"keywords": "rust", "role_types": ["Design"], "regions": ["Tel Aviv area"]},
        )
    ]
    rig.db.jobs += [
        job_row("Acme", "other-type", "Software Engineer"),
        job_row("Acme", "abroad", "Product Designer", location="London, UK"),
        job_row("Acme", "ok", "Product Designer"),
    ]
    rig.db.text_hits = {"rust": [["Acme", "other-type"], ["Acme", "abroad"], ["Acme", "ok"]]}
    rig.go()
    text = rig.mail.sent[0]["text"]
    assert "Acme/ok\n" in text and "Acme/other-type" not in text and "Acme/abroad" not in text


def test_one_search_per_distinct_keywords_and_none_without_keywords_or_when_nobody_is_due(rig: Rig) -> None:
    rig.db.subscriptions = [
        keyword_sub(ANN, "rust"),
        keyword_sub(BOB, " RUST "),  # the same words
        keyword_sub(CAROL, ""),  # no keywords: no search
    ]
    rig.db.jobs += [job_row("Acme", "new", "Product Manager")]
    rig.go()
    assert rig.db.searched == ["rust"]
    rig.db.subscriptions = [{**keyword_sub(ANN, "rust"), "frequency": "weekly"}]
    rig.db.searched.clear()
    assert rig.go(MONDAY).due == 0
    assert rig.db.searched == []


def test_a_failed_search_is_counted_and_the_title_match_still_goes_out_without_logging_the_words(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.db.subscriptions = [keyword_sub(ANN, "secretword")]
    rig.db.jobs += [job_row("Acme", "title", "Secretword Engineer"), job_row("Acme", "text", "Software Engineer")]
    rig.db.fail = ("POST", "search_job_keys")
    summary = rig.go()
    assert (summary.failed, summary.sent, summary.jobs_sent) == (1, 1, 1)  # the title match only
    assert summary.failures == [
        "a keyword search failed: POST rpc/search_job_keys failed: HTTP 404".replace("404", "500")
    ]
    assert "secretword" not in " ".join(summary.failures) and "Acme/text" not in rig.mail.sent[0]["text"]
    rig.db.fail = None
    rig.db.text_hits = {"secretword": [["Acme", "text"]]}  # the next run (the function is there): it is still new
    rig.mail.sent.clear()
    rig.db.emailed.clear()
    assert rig.go().failed == 0
    assert "Acme/text\n" in rig.mail.sent[0]["text"]


def test_a_search_answer_that_is_not_a_list_of_keys_is_an_error(rig: Rig) -> None:
    for answer in ({"message": "x"}, [["Acme"]], [["Acme", "a", "b"]], ["Acme"], None):
        rig.db.bad_answer = ("search_job_keys", answer)
        with pytest.raises(DigestError, match="not JSON" if answer is None else "other than a list of keys"):
            rig.client().search_keys("rust")
    rig.db.bad_answer = None
    rig.db.raises = (
        "POST",
        "search_job_keys",
        requests.ConnectionError("https://abc.supabase.co?apikey=service-role-key-xyz"),
    )
    with pytest.raises(DigestError) as caught:
        rig.client().search_keys("rust")
    assert str(caught.value) == "POST rpc/search_job_keys failed: ConnectionError"


def test_search_keys_asks_with_the_query_in_the_body_never_the_url_and_returns_keys_only(rig: Rig) -> None:
    rig.db.text_hits = {"rust": [["Acme", "a"], ["Beta", "b"]]}
    assert rig.client().search_keys("rust") == {("Acme", "a"), ("Beta", "b")}
    (call,) = rig.db.calls
    assert call["url"] == f"{URL}/rest/v1/rpc/search_job_keys" and "rust" not in call["url"]
    assert call["json"] == {"p_query": "rust"} and call["params"] == {}


def test_matches_takes_the_full_text_keys_as_an_alternative_to_the_words_in_the_title() -> None:
    job = parse_job(job_row("Acme", "x", "Software Engineer"))
    filters = parse_filters({"keywords": "rust"})
    assert not matches(job, filters)
    assert matches(job, filters, frozenset({("Acme", "x")}))
    assert not matches(job, filters, frozenset({("Acme", "y")}))
    assert matches(job, parse_filters({"keywords": "engineer"}))
