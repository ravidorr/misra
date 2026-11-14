"""Level and role type, checked on titles taken from the open jobs in data/jobs.db."""

import re

import pytest

import classify
from classify import NOT_SPECIFIED, OTHER

# (title, level, role types): real titles of open jobs.
REAL_TITLES: list[tuple[str, str, list[str]]] = [
    ("Product Manager Intern", "Intern", ["Product"]),
    ("Senior Product Manager - Mobile App", NOT_SPECIFIED, ["Product", "R&D"]),
    ("Product Manager - Core Application", NOT_SPECIFIED, ["Product"]),
    ("Software Engineer Intern - Two Dots", "Intern", ["R&D"]),
    ("Junior Software Engineer", "Junior", ["R&D"]),
    ("Top Graduate Software Engineer - V-Force - New York", "Junior", ["R&D"]),
    ("Pre-Silicon Verification Engineer - Hod Hasharon", NOT_SPECIFIED, ["R&D"]),
    ("DevOps Engineer - US East Coast/ Central", NOT_SPECIFIED, ["R&D"]),
    ("Algorithm Developer for Objects Modeling", NOT_SPECIFIED, ["R&D"]),
    ("Junior Data Scientist", "Junior", ["Data & AI"]),
    ("Data Science INTERN", "Intern", ["Data & AI"]),
    ("Senior Data Engineer", NOT_SPECIFIED, ["R&D", "Data & AI"]),
    ("Senior Product Designer", NOT_SPECIFIED, ["Product", "Design"]),
    ("Marketing Design & Animation Intern", "Intern", ["Design"]),
    ("IT Junior", "Junior", ["IT"]),
    ("IT Support Specialist", NOT_SPECIFIED, ["IT", "Customer success & support"]),
    ("Corporate FP&A Manager", NOT_SPECIFIED, ["Finance"]),
    ("Bookkeeper - 2942", NOT_SPECIFIED, ["Finance"]),
    ("Credit Manager Intern", "Intern", [OTHER]),
    ("Sales Intern - Business Development Team", "Intern", ["Sales & BD"]),
    ("Junior Sales Manager", "Junior", ["Sales & BD"]),
    ("Business Development Representative", NOT_SPECIFIED, ["Sales & BD"]),
    ("Sales Engineer, Brisbane", NOT_SPECIFIED, ["Sales & BD"]),
    ("Salesforce Engineer", NOT_SPECIFIED, ["R&D"]),
    ("Regional Customer Success Manager, Enterprise (Chinese Speaking)", NOT_SPECIFIED, ["Customer success & support"]),
    ("Technical Support Engineer- Student", "Intern", ["Customer success & support"]),
    ("Senior Customer Success Engineer - West (California)", NOT_SPECIFIED, ["Customer success & support"]),
    ("Working Student - Sales DACH", "Intern", ["Sales & BD"]),
    ("Legal Counsel", NOT_SPECIFIED, [OTHER]),
    ("Digital Marketing Intern", "Intern", [OTHER]),
    ("מהנדס/ת ייצור אלקטרוניקה", NOT_SPECIFIED, ["R&D"]),
    ("קניין רכש", NOT_SPECIFIED, [OTHER]),
    ("Junior Intern", "Intern", [OTHER]),
    ("International Accountant", NOT_SPECIFIED, ["Finance"]),
    ("Internal Tools Engineer", NOT_SPECIFIED, ["R&D"]),
]


@pytest.mark.parametrize(("title", "expected_level", "_types"), REAL_TITLES)
def test_level_on_real_titles(title: str, expected_level: str, _types: list[str]) -> None:
    assert classify.level(title) == expected_level


@pytest.mark.parametrize(("title", "_level", "expected_types"), REAL_TITLES)
def test_role_types_on_real_titles(title: str, _level: str, expected_types: list[str]) -> None:
    assert classify.role_types(title) == expected_types


def test_every_title_has_one_level_and_a_role_type() -> None:
    """FR-9 AC 1 data side: nothing is left without a level or a type, and a job is never dropped."""
    for title, _, _ in REAL_TITLES:
        assert classify.level(title) in classify.LEVELS
        assert set(classify.role_types(title)) <= set(classify.ROLE_TYPE_NAMES)


def test_first_role_types_cover_the_agreed_nine() -> None:
    assert classify.ROLE_TYPE_NAMES == (
        "Product",
        "R&D",
        "Data & AI",
        "Design",
        "IT",
        "Finance",
        "Sales & BD",
        "Customer success & support",
        "Other",
    )


def test_a_job_can_belong_to_several_role_types_and_other_only_alone() -> None:
    assert classify.role_types("Senior Product Designer") == ["Product", "Design"]
    for _, _, types in REAL_TITLES:
        assert types == [OTHER] or OTHER not in types


def test_a_new_role_type_is_one_table_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    assert classify.role_types("Legal Counsel") == [OTHER]
    monkeypatch.setitem(classify.ROLE_TYPES, "Legal", (r"\blegal\b|counsel",))
    compiled = {name: re.compile("|".join(parts), re.I) for name, parts in classify.ROLE_TYPES.items()}
    monkeypatch.setattr(classify, "_COMPILED", compiled)
    assert classify.role_types("Legal Counsel") == ["Legal"]
    assert classify.role_types("Senior Product Counsel") == ["Product", "Legal"]


def test_existing_patterns_keep_their_behaviour() -> None:
    assert classify.PM_CORE.search("APM, Growth")
    assert classify.PM_CORE.search("מנהל/ת מוצר")
    assert classify.INTERN.search("סטודנט/ית") and not classify.INTERN.search("International")
    assert classify.JUNIOR.search("Jr Analyst") and not classify.JUNIOR.search("Senior Analyst")


PRODUCT_INTERN = "Product Manager Intern"
PRODUCT_JUNIOR = "Junior Product Manager"
PRODUCT_SENIOR = "Senior Product Manager - Mobile App"
SOFTWARE_INTERN = "Software Engineer Intern - Two Dots"


def test_product_with_every_level_gives_product_at_any_seniority() -> None:
    every_level = list(classify.LEVELS)
    for levels in ((), every_level):
        assert classify.matches(PRODUCT_INTERN, levels, ["Product"])
        assert classify.matches(PRODUCT_JUNIOR, levels, ["Product"])
        assert classify.matches(PRODUCT_SENIOR, levels, ["Product"])
        assert not classify.matches(SOFTWARE_INTERN, levels, ["Product"])


def test_product_with_intern_and_junior_gives_only_entry_level_ones() -> None:
    entry = ["Intern", "Junior"]
    assert classify.matches(PRODUCT_INTERN, entry, ["Product"])
    assert classify.matches(PRODUCT_JUNIOR, entry, ["Product"])
    assert not classify.matches(PRODUCT_SENIOR, entry, ["Product"])
    # the old digest bug: an entry-level job of another field is not Product
    assert not classify.matches(SOFTWARE_INTERN, entry, ["Product"])


def test_no_role_type_chosen_means_every_type_and_not_specified_is_a_level() -> None:
    assert classify.matches(SOFTWARE_INTERN, ["Intern"], [])
    assert classify.matches("Legal Counsel", [NOT_SPECIFIED], [])
    assert classify.matches("Legal Counsel", [], [OTHER])
    assert not classify.matches("Legal Counsel", ["Intern"], [])
    assert classify.matches("Senior Product Designer", [], ["Design", "Finance"])


def test_share_not_other() -> None:
    half = 0.5
    assert classify.share_not_other(["Product Manager", "Legal Counsel", "Software Engineer", "Recruiter"]) == half
    assert classify.share_not_other([]) == 1.0


def test_report_lists_share_counts_and_unplaced_titles() -> None:
    text = classify.report(["Product Manager", "Legal Counsel", "Legal Counsel", "Software Engineer", "Recruiter"])
    lines = text.splitlines()
    assert lines[0] == "Role types: 40.0% of 5 open jobs are not in Other"
    assert "  Product: 1" in lines
    assert "  R&D: 1" in lines
    assert "  Other: 3" in lines
    assert "  Unplaced titles (first 2):" in lines
    assert lines.count("    Legal Counsel") == 1


def test_report_without_unplaced_titles_has_no_list() -> None:
    assert "Unplaced" not in classify.report(["Product Manager"])


def test_report_caps_the_unplaced_sample() -> None:
    sample = 3
    text = classify.report([f"Recruiter {n}" for n in range(30)], sample=sample)
    assert f"Unplaced titles (first {sample}):" in text
    assert text.count("    Recruiter") == sample
