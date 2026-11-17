"""The research item in TODO.md quotes counts about the researched companies of data/companies.json (the records with
a `gap`). The coverage gap card is built from them, so these tests fail when the quoted counts and the file disagree,
and the card and TODO.md cannot drift."""

import json
import re
from collections import Counter
from pathlib import Path

from build_dashboard import read_unresolved

REPO = Path(__file__).resolve().parents[1]
TODO = " ".join((REPO / "TODO.md").read_text(encoding="utf-8").split())  # one line: wrapping is not a change
SEGMENTS = ("multinational", "deeptech", "cyber_infra", "biz_software")


def rows() -> list[dict[str, str]]:
    """The researched companies as `read_unresolved` returns them: name, segment, reason, note, careers_url."""
    return read_unresolved(json.loads((REPO / "data" / "companies.json").read_text(encoding="utf-8")))


def quoted(pattern: str, text: str = TODO) -> int:
    """The number a sentence of TODO.md quotes. A sentence that was reworded fails here instead of being skipped."""
    match = re.search(pattern, text)
    assert match is not None, f"TODO.md no longer matches {pattern!r}; update this test with the new wording"
    return int(match[1])


def test_the_total_is_the_number_of_rows() -> None:
    assert quoted(r"holds (\d+) researched companies") == len(rows())


def test_no_company_is_listed_twice() -> None:
    names = [r["name"] for r in rows()]
    assert len(set(names)) == len(names)


def test_the_segment_counts_are_the_rows_of_each_segment() -> None:
    sentence = re.search(r"holds \d+ researched companies.*?\)", TODO)
    assert sentence is not None
    by_segment = Counter(r["segment"] for r in rows())
    assert set(by_segment) == set(SEGMENTS)
    for segment in SEGMENTS:
        assert quoted(rf"(\d+) {segment}\b", sentence[0]) == by_segment[segment], segment


def test_the_no_careers_url_count_is_the_companies_without_a_careers_page() -> None:
    names = {r["name"] for r in rows() if r["reason"] == "no_careers_url"}
    assert names == {r["name"] for r in rows() if not r["careers_url"]}
    assert quoted(r"\*\*No careers URL \((\d+)\)\.\*\*") == len(names)


def test_the_count_with_a_careers_url_is_the_rest() -> None:
    with_url = len(rows()) - sum(r["reason"] == "no_careers_url" for r in rows())
    assert quoted(r"else its \"\((\d+) today\)\"") == with_url
    assert quoted(r"the\s+\d+\s+rows\s+that\s+had\s+a\s+careers\s+URL\s+that\s+day\s+\((\d+)\s+today\)") == with_url
