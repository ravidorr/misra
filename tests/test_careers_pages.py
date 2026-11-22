"""The readers of two small careers pages that list their roles as plain page text (`adapters.nucleai`, Nucleai, and
`adapters.zigit`, ZIGIT). The pages are cut down from the live ones (read 2026-11-22). No network."""

from typing import Any

import pytest

import adapters
import regions
from tests.fakes import FakeHttpResponse

NUCLEAI_URL = "https://nucleai.ai/careers/"
ZIGIT_URL = "https://www.zigit.co.il/%d7%a7%d7%a8%d7%99%d7%99%d7%a8%d7%94/"
ZIGIT_QA = "https://www.zigit.co.il/job-management/qa/"
ZIGIT_FULL = "https://www.zigit.co.il/job-management/full-stack/"


def fake_page(monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None) -> list[str]:
    """Every request gets the answer; the addresses asked are kept."""
    calls: list[str] = []

    def _get(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append(url)
        return answer

    monkeypatch.setattr(adapters, "get", _get)
    return calls


def nucleai_role(
    title: str = "Senior Software Engineer",
    place: str = "Tel Aviv-Yafo",
    text: str = "This is a full-time hybrid role for a {t} located in {p}, with some work from home acceptable.",
) -> str:
    body = text.format(t=title, p=place)
    return (
        f'<div class="elementor-widget-container"><h4 class="elementor-heading-title">{title}</h4></div>'
        f'<div class="elementor-widget-container"><p><strong>Responsibilities:</strong><br>{body}</p>'
        f"<ul><li>React &amp; WebSocket APIs</li></ul></div>"
        f'<a href="https://www.linkedin.com/jobs/view/4291700752/" target="_blank"><span>Apply Now</span></a>'
    )


def nucleai_page(*roles: str, heading: str = "Open Position(s)") -> FakeHttpResponse:
    return FakeHttpResponse(
        text=(
            "<html><head><style>.hide{display:none;}</style><script>var h2 = '<h2>x</h2>';</script></head><body>"
            '<h4>Not a role: before the section</h4><h2 class="elementor-heading-title">About us</h2>'
            f'<h2 class="elementor-heading-title">{heading}</h2>'
            "<p>Text before the first role.</p>"
            f"{''.join(roles)}"
            '<h2 class="elementor-heading-title">Open Positions</h2><div id="commet-data">Array</div>'
            "<h4>Not a role: after the section</h4><p>Footer</p></body></html>"
        )
    )


def test_nucleai_reads_the_roles_under_the_open_positions_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_page(
        monkeypatch,
        nucleai_page(nucleai_role(), nucleai_role("  QA\n  Engineer ", "Herzliya")),
    )
    assert adapters.nucleai("nucleai.ai") == [
        {
            "job_id": "senior-software-engineer",
            "title": "Senior Software Engineer",
            "location": "Tel Aviv-Yafo, Israel",
            "url": "https://nucleai.ai/careers/#senior-software-engineer",
            "team": "",
            "posted": "",
        },
        {
            "job_id": "qa-engineer",
            "title": "QA Engineer",
            "location": "Herzliya, Israel",
            "url": "https://nucleai.ai/careers/#qa-engineer",
            "team": "",
            "posted": "",
        },
    ]
    assert calls == [NUCLEAI_URL]  # one request


def test_nucleai_the_places_it_gives_are_placed_in_israel_and_in_a_region(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page(monkeypatch, nucleai_page(nucleai_role(place="Tel Aviv-Yafo"), nucleai_role("B", "Haifa")))
    jobs = adapters.nucleai("nucleai.ai")
    assert jobs
    places = [regions.place(j["location"]) for j in jobs]
    assert all(p.israel for p in places)
    assert [p.regions for p in places] == [("Tel Aviv area",), ("Haifa and the north",)]


def test_nucleai_is_for_its_host_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_page(monkeypatch, nucleai_page(nucleai_role()))
    assert adapters.nucleai("example.com") is None
    assert calls == []


@pytest.mark.parametrize("answer", [None, FakeHttpResponse(status_code=503, text="x")])
def test_nucleai_a_request_that_fails_is_a_failure(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    fake_page(monkeypatch, answer)
    assert adapters.nucleai("nucleai.ai") is None


def test_nucleai_a_changed_shape_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page(monkeypatch, nucleai_page(nucleai_role(), heading="Join us"))  # the heading is gone
    assert adapters.nucleai("nucleai.ai") is None
    fake_page(monkeypatch, FakeHttpResponse(text="<html><body><h4>Senior Software Engineer</h4></body></html>"))
    assert adapters.nucleai("nucleai.ai") is None


def test_nucleai_a_section_with_no_role_is_a_failure_that_keeps_the_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page(monkeypatch, nucleai_page())
    assert adapters.nucleai("nucleai.ai") is None


@pytest.mark.parametrize(
    "roles",
    [
        [nucleai_role(), nucleai_role(place="Tel Aviv-Yafo")],  # a title twice
        [nucleai_role(), nucleai_role("Other", text="A role with no place given.")],  # no `located in`
        [nucleai_role(), nucleai_role("   ")],  # no title
        [nucleai_role(), nucleai_role("!!!")],  # a title with no letter or digit: no id
    ],
)
def test_nucleai_a_role_that_does_not_read_or_is_twice_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, roles: list[str]
) -> None:
    fake_page(monkeypatch, nucleai_page(*roles))
    assert adapters.nucleai("nucleai.ai") is None


def test_nucleai_the_list_at_the_cap_is_read_and_one_above_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page(monkeypatch, nucleai_page(*(nucleai_role(f"Role {i}") for i in range(adapters.CAREERS_PAGE_MAX))))
    jobs = adapters.nucleai("nucleai.ai")
    assert jobs and len(jobs) == adapters.CAREERS_PAGE_MAX
    fake_page(monkeypatch, nucleai_page(*(nucleai_role(f"Role {i}") for i in range(adapters.CAREERS_PAGE_MAX + 1))))
    assert adapters.nucleai("nucleai.ai") is None


def zigit_card(
    title: str = "בודק/ת תוכנה", href: str | None = ZIGIT_QA, cls: str = "card_box_main_wrp", extra: str = ""
) -> str:
    link = f' href="{href}"' if href is not None else ""
    return (
        f'<a class="{cls}"{link}><div class="card_box" style="border: 2px solid #c4a7cd;">'
        f'<div class="card_box_details"><p>{title}</p>{extra}'
        '<span class="dashicons dashicons-arrow-left-alt2"></span>'
        '<div class="right_image"><img src="https://www.zigit.co.il/wp-content/uploads/a.png" /></div>'
        "</div></div></a>"
    )


def zigit_page(*cards: str, heading: str = " משרות חדשות ") -> FakeHttpResponse:
    return FakeHttpResponse(
        text=(
            '<html><head><script>var a = "<a class=\\"card_box_main_wrp\\">";</script></head><body>'
            f'<a class="card_box_main_wrp" href="{ZIGIT_QA}"><p>Not a role: before the section</p></a>'
            f'<div class="job__container"><h2 class="new__job__title">{heading}</h2><div class="job__list__box">'
            f'<a href="https://www.zigit.co.il/about/"><p>Not a card</p></a><p>Not in a card</p>'
            f"{''.join(cards)}</div></div>"
            '<div class="main__grid"><h2 class="grid__title">zigitwibes</h2>'
            f'<a class="card_box_main_wrp" href="{ZIGIT_QA}"><p>Not a role: after the section</p></a></div>'
            "</body></html>"
        )
    )


def test_zigit_reads_the_cards_under_the_new_jobs_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_page(
        monkeypatch,
        zigit_page(
            zigit_card(),
            zigit_card("מפתח/ת Full Stack בכיר/ה", ZIGIT_FULL, cls="x card_box_main_wrp"),
            zigit_card("  מעצב/ת ומאפיין/ת\n UX/UI ", ZIGIT_FULL + "ux/"),
        ),
    )
    assert adapters.zigit("www.zigit.co.il") == [
        {
            "job_id": "בודק-ת-תוכנה",
            "title": "בודק/ת תוכנה",
            "location": "Israel",
            "url": ZIGIT_QA,
            "team": "",
            "posted": "",
        },
        {
            "job_id": "מפתח-ת-full-stack-בכיר-ה",
            "title": "מפתח/ת Full Stack בכיר/ה",
            "location": "Israel",
            "url": ZIGIT_FULL,
            "team": "",
            "posted": "",
        },
        {
            "job_id": "מעצב-ת-ומאפיין-ת-ux-ui",
            "title": "מעצב/ת ומאפיין/ת UX/UI",
            "location": "Israel",
            "url": ZIGIT_FULL + "ux/",
            "team": "",
            "posted": "",
        },
    ]
    assert calls == [ZIGIT_URL]  # one request


def test_zigit_the_place_is_israel_with_no_region(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page(monkeypatch, zigit_page(zigit_card()))
    jobs = adapters.zigit("www.zigit.co.il")
    assert jobs
    place = regions.place(jobs[0]["location"])
    assert place.israel and place.regions == () and place.locations == (regions.CITY_NOT_GIVEN,)


def test_zigit_is_for_its_host_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_page(monkeypatch, zigit_page(zigit_card()))
    assert adapters.zigit("zigit.co.il") is None
    assert calls == []


@pytest.mark.parametrize("answer", [None, FakeHttpResponse(status_code=404, text="x")])
def test_zigit_a_request_that_fails_is_a_failure(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    fake_page(monkeypatch, answer)
    assert adapters.zigit("www.zigit.co.il") is None


def test_zigit_a_changed_shape_or_no_card_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page(monkeypatch, zigit_page(zigit_card(), heading="גיוס"))  # the heading is gone
    assert adapters.zigit("www.zigit.co.il") is None
    fake_page(monkeypatch, zigit_page())  # the section is empty
    assert adapters.zigit("www.zigit.co.il") is None


@pytest.mark.parametrize(
    "cards",
    [
        [zigit_card(), zigit_card(href=ZIGIT_FULL)],  # a title twice
        [zigit_card(), zigit_card("x", None)],  # no address
        [zigit_card(), zigit_card("x", "https://elsewhere.test/job-management/x/")],  # not a page of the site
        [zigit_card(), zigit_card("x", "https://www.zigit.co.il/about/")],  # not a job page
        [zigit_card(), zigit_card("  ", ZIGIT_FULL)],  # no title
        [zigit_card(), zigit_card("//", ZIGIT_FULL)],  # a title with no letter or digit: no id
    ],
)
def test_zigit_a_card_that_does_not_read_or_is_twice_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, cards: list[str]
) -> None:
    fake_page(monkeypatch, zigit_page(*cards))
    assert adapters.zigit("www.zigit.co.il") is None


def test_zigit_the_list_at_the_cap_is_read_and_one_above_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    def many(n: int) -> FakeHttpResponse:
        return zigit_page(*(zigit_card(f"Role {i}", f"{ZIGIT_FULL}{i}/") for i in range(n)))

    fake_page(monkeypatch, many(adapters.CAREERS_PAGE_MAX))
    jobs = adapters.zigit("www.zigit.co.il")
    assert jobs and len(jobs) == adapters.CAREERS_PAGE_MAX
    fake_page(monkeypatch, many(adapters.CAREERS_PAGE_MAX + 1))
    assert adapters.zigit("www.zigit.co.il") is None


def test_the_readers_are_registered() -> None:
    assert adapters.ADAPTERS["nucleai"] is adapters.nucleai
    assert adapters.ADAPTERS["zigit"] is adapters.zigit
