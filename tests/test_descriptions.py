import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

import descriptions
from descriptions import (
    HASH_KEY,
    MAX_CHARS,
    TRUNCATED,
    attach,
    cap,
    clean_details,
    fingerprint,
    html_to_text,
    plain_text,
    read_work_file,
    sections,
    share_report,
    work_path,
    write_work_file,
)

REPO = Path(__file__).resolve().parent.parent
SHA256_HEX = 64


def test_html_becomes_text_with_the_paragraph_and_list_breaks_kept() -> None:
    raw = "<h2>About</h2><p>We build <b>things</b>.</p><ul><li>Python</li><li>SQL</li></ul>Apply&nbsp;now &amp; join"
    assert html_to_text(raw) == "About\n\nWe build things.\n\n- Python\n- SQL\nApply now & join"


def test_a_script_a_style_and_an_embedded_frame_vanish_with_their_content() -> None:
    raw = (
        "<p>Hello</p><script>alert(1)</script><style>p{color:red}</style><iframe src='x'>bad</iframe>"
        "<svg><text>t</text></svg>world"
    )
    text = html_to_text(raw)
    assert text == "Hello\nworld"
    assert "alert" not in text
    assert "color" not in text


def test_an_unclosed_script_drops_the_rest_and_a_stray_end_tag_changes_nothing() -> None:
    assert html_to_text("a</script>b<script>never closed") == "ab"


def test_escaped_html_is_unescaped_once_before_it_is_read() -> None:
    assert html_to_text("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;&lt;script&gt;x()&lt;/script&gt;") == "Hi & bye"


def test_a_script_escaped_twice_is_taken_out_but_escaped_prose_stays() -> None:
    assert html_to_text("<p>&lt;b&gt;bold&lt;/b&gt; and &lt;script&gt;x()&lt;/script&gt;</p>") == "<b>bold</b> and"
    assert html_to_text("<p>&lt;SCRIPT&gt;never closed") == ""
    assert html_to_text("&lt;p&gt;Use &amp;lt;vector&amp;gt; in C++&lt;/p&gt;") == "Use <vector> in C++"


def test_a_void_embed_does_not_swallow_the_rest_of_the_posting() -> None:
    assert html_to_text("<p>Intro</p><embed src=x><p>Requirements</p>") == "Intro\n\nRequirements"


def test_a_less_than_sign_in_prose_stays() -> None:
    assert html_to_text("<p>3 < 5 and 7 > 2</p>") == "3 < 5 and 7 > 2"


def test_control_characters_never_survive_and_blank_lines_are_collapsed() -> None:
    assert html_to_text("<p>a\x00b\x07c</p>\n\n\n<p>\ud800d</p><br><br><br>e") == "a b c\n\nd\n\ne"
    assert "\x00" not in plain_text("x\x00y")


@pytest.mark.parametrize("value", [None, 5, ["<p>x</p>"], {"a": "b"}, b"<p>x</p>"])
def test_what_is_not_text_is_no_description(value: Any) -> None:
    assert html_to_text(value) == ""
    assert plain_text(value) == ""


def test_a_parser_that_fails_gives_no_description_and_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Any, data: str) -> None:
        raise AssertionError(data)

    monkeypatch.setattr(descriptions._Text, "feed", boom)
    assert html_to_text("<p>x</p>") == ""


def test_sections_skip_the_empty_parts_and_head_the_rest() -> None:
    assert sections(("Description", "Do things"), ("Requirements", ""), ("", "Bye")) == "Description\nDo things\n\nBye"
    assert sections(("Only a heading", "")) == ""


def test_the_cap_cuts_at_a_space_near_the_limit_and_says_so() -> None:
    text = "word " * 10
    assert cap(text, 1000) == (text, False)
    cut, was_cut = cap(text, 12)
    assert (cut, was_cut) == ("word word", True)  # not "word word w"
    assert cap("x" * 50, 12) == ("x" * 12, True)  # no space near: cut where it is
    assert cap("a\nb" * 10, 4)[1] is True
    far = "a b" + "x" * 500
    assert cap(far, 400) == (far[:400], True)  # the only space is far back: the cut stays at the cap


def test_details_keep_short_text_flags_and_short_lists_only() -> None:
    kept = clean_details(
        {
            "a": " Full-time ",
            "b": True,
            "c": ["Sales", "", "Ops\x00", 7, {"x": 1}],
            "d": "",
            "e": [],
            "f": {"nested": "no"},
            "g": None,
            "h": 5,
            "i": "x" * 500,
            "j": [f"v{i}" for i in range(50)],
        }
    )
    assert kept["a"] == "Full-time"
    assert kept["b"] is True
    assert kept["c"] == ["Sales", "Ops"]
    assert len(kept["i"]) == descriptions.MAX_DETAIL_CHARS
    assert len(kept["j"]) == descriptions.MAX_DETAIL_ITEMS
    assert set(kept) == {"a", "b", "c", "i", "j"}


def test_attach_adds_only_what_there_is_and_records_a_cut() -> None:
    bare = {"job_id": "1"}
    assert attach(dict(bare), "", {}) == bare  # a job without anything stays as it was
    assert attach(dict(bare), "", {"x": ""}) == bare
    with_text = attach(dict(bare), "Text", {"type": "Full-time"})
    assert with_text == {"job_id": "1", "description": "Text", "details": {"type": "Full-time"}}
    long = attach(dict(bare), "w " * MAX_CHARS, {})
    assert len(long["description"]) <= MAX_CHARS
    assert long["details"] == {TRUNCATED: True}


def test_the_fingerprint_follows_the_text_and_details_and_ignores_its_own_key() -> None:
    base = fingerprint("text", {"a": "1"})
    assert fingerprint("text", {"a": "1", HASH_KEY: "old"}) == base
    assert fingerprint("text!", {"a": "1"}) != base
    assert fingerprint("text", {"a": "2"}) != base
    assert len(base) == SHA256_HEX


def test_the_work_file_is_outside_the_checkout_and_in_the_runner_temp_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    assert work_path() == tmp_path / descriptions.WORK_FILE
    for unset in (None, ""):
        if unset is None:
            monkeypatch.delenv("RUNNER_TEMP")
        else:
            monkeypatch.setenv("RUNNER_TEMP", unset)
        assert work_path() == Path(tempfile.gettempdir()) / descriptions.WORK_FILE  # never the checkout
        assert work_path().is_absolute()
        assert not work_path().is_relative_to(REPO)


def test_the_work_file_round_trips_and_is_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "work.jsonl"
    rows = [
        {"company": "Acme", "job_id": "1", "description": "Hebrew שלום", "details": {"a": "b"}},
        {"company": "Acme", "job_id": "2", "description": "Two", "details": {}},
    ]
    assert write_work_file(rows, path) == len(rows)
    assert not path.with_name("work.jsonl.part").exists()
    assert read_work_file(path) == {("Acme", "1"): ("Hebrew שלום", {"a": "b"}), ("Acme", "2"): ("Two", {})}
    assert write_work_file([], path) == 0  # an empty crawl replaces the old file, it does not leave it
    assert read_work_file(path) == {}


def test_a_missing_or_unreadable_work_file_is_no_text(tmp_path: Path) -> None:
    assert read_work_file(tmp_path / "none.jsonl") == {}
    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(b"\xff\xfe\x00")
    assert read_work_file(broken) == {}
    assert read_work_file() == {}  # the default place, empty in a test


def test_lines_that_are_not_what_the_crawl_writes_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "work.jsonl"
    good = {"company": "A", "job_id": "1", "description": "ok", "details": {}}
    lines = [
        "not json",
        "[1]",
        json.dumps({"company": "A"}),
        json.dumps({**good, "description": ""}),
        json.dumps({**good, "job_id": 5}),
        json.dumps({**good, "details": "text"}),
        json.dumps({**good, "details": None}),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    assert read_work_file(path) == {("A", "1"): ("ok", {})}


def test_the_share_report_has_counts_and_percentages_only() -> None:
    jobs = [
        ("greenhouse", [{"description": "aaa"}, {"description": "bb", "details": {TRUNCATED: True}}, {}]),
        ("browser", [{}, {}]),
        ("greenhouse", [{"description": "c"}]),
        ("lever", []),
    ]
    assert share_report(jobs) == (
        "descriptions: greenhouse 3/4 (75%); browser 0/2 (0%); all 3/6 (50%); "
        "cut at 20000 characters 1; 6 characters kept"
    )
    assert share_report([]) == "descriptions: ; all 0/0 (0%); cut at 20000 characters 0; 0 characters kept"
