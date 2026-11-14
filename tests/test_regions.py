"""Regions of Israel (FR-3), checked on location strings taken from the open jobs in data/jobs.db."""

import sqlite3
from pathlib import Path

import pytest

import regions
from regions import CENTER, CITY_NOT_GIVEN, JERUSALEM, NORTH, REMOTE, SHARON, SOUTH, TEL_AVIV

# (location, regions): real strings from data/jobs.db, spellings as written.
REAL_LOCATIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Tel Aviv-Yafo, IL", (TEL_AVIV,)),
    ("Tel-Aviv, IL", (TEL_AVIV,)),
    ("Tel Aviv", (TEL_AVIV,)),
    ("TLV", (TEL_AVIV,)),
    ("TEL AVIV, ISRAEL", (TEL_AVIV,)),
    ("Tel Aviv-Yafo, Tel Aviv District, Israel", (TEL_AVIV,)),
    ("Tel Aviv District, Israel", (TEL_AVIV,)),
    ("Tel Aviv-Yafo, Gush Dan, Israel", (TEL_AVIV,)),
    ("Tel Aviv- Toha", (TEL_AVIV,)),
    ("Ramat Gan, IL", (TEL_AVIV,)),
    ("Holon, IL", (TEL_AVIV,)),
    ("Israel \u2013 Bnei-Brak", (TEL_AVIV,)),
    ("Kiryat Ono, IL", (TEL_AVIV,)),
    ("Herzliya", (SHARON,)),
    ("Herzliya, Tel Aviv District, Israel", (SHARON,)),
    ("Hod Hasharon, Haifa District, Israel", (SHARON,)),
    ("Raanana, Israel", (SHARON,)),
    ("Kfar Saba, Israel", (SHARON,)),
    ("Kfar-Netter, Israel", (SHARON,)),
    ("Netanya, Israel", (SHARON,)),
    ("Petah-Tikva, IL", (CENTER,)),
    ("Petah Tikva, Central District, Israel", (CENTER,)),
    ("Rosh Haayin, IL", (CENTER,)),
    ("Rosh Ha'ayin, IL", (CENTER,)),
    ("Rehovot,ISR", (CENTER,)),
    ("Ness Ziona, IL", (CENTER,)),
    ("Modi'in, IL", (CENTER,)),
    ("Jerusalem, Israel", (JERUSALEM,)),
    ("Haifa, Haifa District, Israel", (NORTH,)),
    ("Israel, Yokneam", (NORTH,)),
    ("Migdal Haemek, ISR", (NORTH,)),
    ("Mig'ha'emek,ISR", (NORTH,)),
    ("Yagur, IL", (NORTH,)),
    ("Caesarea, IL", (NORTH,)),
    ("Tirat Carmel, IL", (NORTH,)),
    ("Israel, Beer Sheva", (SOUTH,)),
    ("Be\u2019er Sheva, Israel", (SOUTH,)),
    ("Kiryat Gat,ISR", (SOUTH,)),
    ("Ashdod, Israel", (SOUTH,)),
    ("IL-Israel-Remote", (REMOTE,)),
    ("Israel, Remote", (REMOTE,)),
    # several places: the job belongs to every region it names
    ("Israel, Yokneam; Israel, Tel Aviv", (TEL_AVIV, NORTH)),
    ("Israel, Tel Aviv; Israel, Raanana; Israel, Beer Sheva; Israel, Yokneam", (TEL_AVIV, SHARON, NORTH, SOUTH)),
    ("Tel Aviv/ Netanya, Israel", (TEL_AVIV, SHARON)),
    ("Israel, Tel Aviv, Herzliya", (TEL_AVIV, SHARON)),
    ("Haifa, Haifa District, Israel; Hod Hasharon, Haifa District, Israel", (SHARON, NORTH)),
    ("Israel, Multiple Locations, Multiple Locations; Israel, Tel Aviv, Herzliya", (TEL_AVIV, SHARON)),
    (
        "Israel, Multiple Locations, Multiple Locations; Israel, Central, Jerusalem; Israel, Southern, Beer-Sheva",
        (JERUSALEM, SOUTH),
    ),
    # a list mixing countries: only the Israeli place counts
    ("Dresden,DEU; Dublin,IRL; Milan,ITA; Bernin,FRA; Kiryat Gat,ISR", (SOUTH,)),
    ("Singapore,SGP; Boise,ID; Austin,TX; Rehovot,ISR; Heimstetten,DEU", (CENTER,)),
    # hybrid counts as the region of its office
    ("Israel: Tel Aviv/ Hybrid (Israel)", (TEL_AVIV,)),
    ("Israel | Hybrid", ()),
]


@pytest.mark.parametrize(("location", "expected"), REAL_LOCATIONS)
def test_regions_of_real_locations(location: str, expected: tuple[str, ...]) -> None:
    placed = regions.place(location)
    assert placed.regions == expected
    if expected:
        assert placed.locations == ()


def test_regions_come_in_the_agreed_order_and_are_seven() -> None:
    assert regions.REGION_NAMES == (
        "Tel Aviv area",
        "Sharon",
        "Center",
        "Jerusalem",
        "Haifa and the north",
        "South",
        "Remote",
    )


@pytest.mark.parametrize("location", ["Israel", "IL", "IL, Israel", "Israel, Israel", "HQ Israel", "Israel | Hybrid"])
def test_israel_alone_is_city_not_given(location: str) -> None:
    placed = regions.place(location)
    assert placed == regions.Place(israel=True, regions=(), locations=(CITY_NOT_GIVEN,))


def test_multiple_locations_with_no_city_is_city_not_given() -> None:
    assert regions.place("Israel, Multiple Locations, Multiple Locations").locations == (CITY_NOT_GIVEN,)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Foo Town, IL", ("Foo Town, IL",)),
        ("Azrieli Building G, Nowhere, Israel", ("Azrieli Building G, Nowhere, Israel",)),
        ("Israel, Foo; Israel, Bar", ("Israel, Foo", "Israel, Bar")),
    ],
)
def test_unmatched_israeli_places_keep_their_own_string(location: str, expected: tuple[str, ...]) -> None:
    placed = regions.place(location)
    assert placed.israel
    assert placed.regions == ()
    assert placed.locations == expected


def test_an_unmatched_place_is_kept_next_to_a_matched_one() -> None:
    placed = regions.place("Israel, Tel Aviv; Israel, Foo")
    assert placed.regions == (TEL_AVIV,)
    assert placed.locations == ("Israel, Foo",)


@pytest.mark.parametrize("location", ["Remote, US", "Remote", "Remote - EMEA", "US-IL-Remote", "Chicago, IL"])
def test_remote_counts_only_when_open_to_israel(location: str) -> None:
    placed = regions.place(location)
    assert placed == regions.Place(israel=False)
    assert REMOTE not in placed.regions


@pytest.mark.parametrize("location", ["London, UK", "New York, NY", "Berlin, Germany", "", None])
def test_jobs_outside_israel_are_untouched(location: str | None) -> None:
    assert regions.place(location) == regions.Place(israel=False)


def test_no_job_in_israel_is_dropped() -> None:
    """Every Israeli location gets a region or a location of its own."""
    for location, _ in REAL_LOCATIONS:
        placed = regions.place(location)
        if placed.israel:
            assert placed.regions or placed.locations


def test_a_new_place_is_one_table_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    assert regions.place("Zikim, Israel").locations == ("Zikim, Israel",)
    monkeypatch.setitem(regions.PLACES, regions.SOUTH, (*regions.PLACES[regions.SOUTH], "zikim"))
    monkeypatch.setattr(regions, "_CITIES", regions.compile_places(regions.PLACES))
    assert regions.place("Zikim, Israel").regions == (SOUTH,)


def test_report_lists_share_counts_and_unmapped_places() -> None:
    text = regions.report(
        ["Tel Aviv, IL", "Herzliya", "Israel", "Foo Town, IL", "Foo Town, IL", "London, UK", None, "Israel, Remote"]
    )
    lines = text.splitlines()
    assert lines[0] == "Regions: 50.0% of 6 jobs in Israel are in a region"
    assert "  Tel Aviv area: 1" in lines
    assert "  Sharon: 1" in lines
    assert "  Remote: 1" in lines
    assert f"  {CITY_NOT_GIVEN}: 1" in lines
    assert "  Unmapped places (top 1 of 1):" in lines
    assert "    2 Foo Town, IL" in lines


def test_report_without_unmapped_places_or_israeli_jobs() -> None:
    assert "Unmapped" not in regions.report(["Tel Aviv, IL"])
    empty = regions.report(["London, UK"])
    assert empty.splitlines()[0] == "Regions: 100.0% of 0 jobs in Israel are in a region"


def test_report_caps_the_unmapped_sample() -> None:
    sample = 2
    text = regions.report([f"Town {n}, IL" for n in range(5)], sample=sample)
    assert f"Unmapped places (top {sample} of 5):" in text


def test_real_data_puts_at_least_95_percent_of_israeli_jobs_in_a_region() -> None:
    """FR-3 AC 11 on the open jobs of data/jobs.db (read-only)."""
    db = Path(__file__).resolve().parent.parent / "data" / "jobs.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        locations = [row[0] for row in c.execute("SELECT location FROM jobs WHERE status='open'")]
    placed = [p for p in (regions.place(location) for location in locations) if p.israel]
    assert placed
    share = sum(1 for p in placed if p.regions) / len(placed)
    assert share >= 0.95  # noqa: PLR2004
