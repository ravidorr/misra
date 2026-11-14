"""Normalise a job's location string to the regions of Israel (FR-3).

`place(location)` says whether the location is in Israel and, if so, which of the seven regions it names. A string can
name several places ("Israel, Yokneam; Israel, Tel Aviv"), and the job belongs to every region it names. Places we do
not group keep their own location string and a job in Israel is never dropped. Jobs outside Israel are left alone.
The places are a table (`PLACES`), so a new city is one string.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from common import ISRAEL

TEL_AVIV = "Tel Aviv area"
SHARON = "Sharon"
CENTER = "Center"
JERUSALEM = "Jerusalem"
NORTH = "Haifa and the north"
SOUTH = "South"
REMOTE = "Remote"
REGION_NAMES = (TEL_AVIV, SHARON, CENTER, JERUSALEM, NORTH, SOUTH, REMOTE)

# What a job in Israel gets when the location names the country and no city.
CITY_NOT_GIVEN = "Israel, city not given"

# Cities by region, written lowercase without apostrophes or hyphens (the text is normalised the same way).
PLACES: dict[str, tuple[str, ...]] = {
    TEL_AVIV: (
        r"tel ?aviv|\btlv\b|jaffa|\byafo\b|gush dan|ramat gan|givatayim|bnei brak|bene brak|holon|bat yam|kiryat ono",
        r"or yehuda|\bazor\b|givat shmuel|תל אביב|רמת גן|בני ברק|חולון",
    ),
    SHARON: (
        r"herzliya|raanana|hod ?hasharon|kfar saba|netanya|kfar netter|ramat hasharon|kadima|even yehuda",
        r"הרצליה|רעננה|הוד השרון|כפר סבא|נתניה",
    ),
    CENTER: (
        r"peta(?:h|ch) ?tikv|rosh ?ha ?ayin|rehovot|rishon|\blod\b|ramle|modi ?in|ness ?ziona|nes ?ziona|yavne|gedera",
        r"airport city|\byehud\b|shoham|beer yaakov|פתח תקו|ראש העין|רחובות|ראשון לציון",
    ),
    JERUSALEM: (r"jerusalem|mevaseret|beit shemesh|maale adumim|ירושלים",),
    NORTH: (
        r"haifa|yokneam|mig(?:dal)? ?ha ?emek|yagur|tirat carmel|caesarea|nazareth|tel hai|kiryat shmona|karmiel",
        r"afula|tiberias|nesher|kiryat (?:ata|bialik|motzkin|yam)|krayot|hadera|zichron|\bacre\b|akko|nahariya|safed",
        r"חיפה|יקנעם|מגדל העמק|נצרת",
    ),
    SOUTH: (
        r"beer ?sheva|beersheba|ashdod|ashkelon|kiryat gat|eilat|dimona|sderot|netivot|ofakim|yeruham|mitzpe ramon",
        r"באר שבע|אשדוד|אשקלון|אילת",
    ),
}

# A district alone ("Tel Aviv District, Israel") places a job only when no city is named.
DISTRICTS: dict[str, str] = {
    r"tel aviv district": TEL_AVIV,
    r"haifa district|northern": NORTH,
    r"jerusalem district": JERUSALEM,
    r"center district|\bcenter\b|central": CENTER,
    r"southern|south district": SOUTH,
}

# Words that say "Israel" or "somewhere" but name no city.
_NO_CITY = re.compile(
    r"israel|\bisr\b|\bil\b|\bhq\b|hybrid|remote|multiple locations|district|headquarters|\boffices?\b|[^a-z0-9א-ת]",
)


def compile_places(table: dict[str, tuple[str, ...]]) -> dict[str, re.Pattern[str]]:
    """One pattern per region, from a table shaped like `PLACES`."""
    return {name: re.compile("|".join(parts)) for name, parts in table.items()}


_CITIES = compile_places(PLACES)
_DISTRICTS = {re.compile(pattern): region for pattern, region in DISTRICTS.items()}
_DISTRICT_WORDS = re.compile(r"(?:tel aviv|haifa|jerusalem|center|central|south|southern|north|northern) district")
_REMOTE = re.compile(r"remote|עבודה מרחוק")
THREE_FIELDS = 3
# "TLV" is the airport code of Tel Aviv: Israeli, though `common.ISRAEL` does not know it. A city of the table below
# also makes a string Israeli, since `common.ISRAEL` knows only a few places (not Ashdod or Givatayim).
_TLV = re.compile(r"\bTLV\b", re.I)


@dataclass(frozen=True)
class Place:
    """Where a job is. `regions` are the regions it belongs to (in `REGION_NAMES` order). `locations` are the places
    of a job in Israel that have no region: their own strings as written, or `CITY_NOT_GIVEN`. Both are empty for a
    job outside Israel."""

    israel: bool
    regions: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()


def _in_israel(text: str) -> bool:
    return bool(ISRAEL.search(text) or _TLV.search(text) or _city_regions(_normalise(text)))


def _normalise(text: str) -> str:
    text = text.lower().replace("\u2019", "").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[-_]", " ", text))


def _city_regions(text: str) -> set[str]:
    return {region for region, pattern in _CITIES.items() if pattern.search(text)}


def _cities_in(text: str) -> set[str]:
    """Regions of the cities named in normalised text. District names do not count: "Haifa District", and the middle
    field of "Israel, Tel Aviv, Herzliya" (country, district, city), which some sources write."""
    fields = [f.strip() for f in _DISTRICT_WORDS.sub(" ", text).split(",")]
    # "Israel, X, Y" or "Y, X, Israel": X is a district when exactly one end is a city
    if (
        len(fields) == THREE_FIELDS
        and _city_regions(fields[1])
        and bool(_city_regions(fields[0])) != bool(_city_regions(fields[2]))
    ):
        del fields[1]
    return _city_regions(", ".join(fields))


def _regions_in(segment: str) -> set[str]:
    text = _normalise(segment)
    found = _cities_in(text)
    if not found:
        found = {region for pattern, region in _DISTRICTS.items() if pattern.search(text)}
    if _REMOTE.search(text) and _in_israel(segment):
        found.add(REMOTE)
    return found


def place(location: str | None) -> Place:
    """Place a location string. A remote role is Remote only when the same part of the string (parts are split on ";")
    also names Israel; "Remote, US" or a bare "Remote" is left alone, as an unknown or foreign place."""
    text = (location or "").strip()
    if not _in_israel(text):
        return Place(israel=False)
    regions: set[str] = set()
    unmapped: list[str] = []
    for segment in (s.strip() for s in text.split(";")):
        if not _in_israel(segment):
            continue
        found = _regions_in(segment)
        regions |= found
        if not found and _NO_CITY.sub("", _normalise(segment)):
            unmapped.append(segment)
    if not regions and not unmapped:
        unmapped = [CITY_NOT_GIVEN]
    return Place(israel=True, regions=tuple(r for r in REGION_NAMES if r in regions), locations=tuple(unmapped))


def report(locations: Iterable[str | None], sample: int = 20) -> str:
    """Region quality report for the daily log: the share of jobs in Israel that fall into a region, the jobs with no
    city, and the most common places left unmapped, so the table above can be improved."""
    placed = [p for p in (place(location) for location in locations) if p.israel]
    in_region = sum(1 for p in placed if p.regions)
    unmapped: Counter[str] = Counter()
    for p in placed:
        if not p.regions:
            unmapped.update(p.locations)
    share = in_region / len(placed) if placed else 1.0
    lines = [f"Regions: {share:.1%} of {len(placed)} jobs in Israel are in a region"]
    lines.extend(f"  {name}: {sum(1 for p in placed if name in p.regions)}" for name in REGION_NAMES)
    lines.append(f"  {CITY_NOT_GIVEN}: {unmapped.pop(CITY_NOT_GIVEN, 0)}")
    if unmapped:
        lines.append(f"  Unmapped places (top {min(sample, len(unmapped))} of {len(unmapped)}):")
        lines.extend(f"    {count} {name}" for name, count in unmapped.most_common(sample))
    return "\n".join(lines)
