"""The "Connect your assistant" page (PLAN.md step 8.2, FR-6 AC 5 to 7): docs/assistant.html, from ASSISTANT.md in the
template of the privacy page, and the link to it in the dashboard footer. Both are behind `ASSISTANT_PAGE_ENABLED`,
which is True since the lawyer approved the assistant endpoint (PLAN.md step 8.1)."""

import re
import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Page

import build_dashboard
from tests.test_dashboard_page import AXE, PHONE, TAP_MIN, build_site
from tests.test_legal_pages import SMALL_PHONE, open_legal
from tests.test_sign_in import AXE_RUN

REPO = Path(__file__).resolve().parents[1]
ADDRESS = f"{build_dashboard.SUPABASE_URL}/functions/v1/mcp"
ADDRESS_MENTIONS = 2  # the one to copy, and the developer command


def test_the_page_is_on_in_the_repository_and_llms_txt_agrees() -> None:
    """Turning the page on is one decision with the endpoint's secret: the constant and the line of llms.txt that names
    the page change together, so the file never points at a page that is not published (or hides one that is)."""
    llms = (REPO / "llms.txt").read_text(encoding="utf-8")
    named = "https://ravidorr.github.io/misra/assistant.html" in llms
    assert named is build_dashboard.ASSISTANT_PAGE_ENABLED
    assert build_dashboard.ASSISTANT_PAGE_ENABLED is True  # on since the lawyer approved it (AGENTS.md)


def test_the_markdown_says_where_to_connect_and_makes_no_promise_the_endpoint_does_not_keep() -> None:
    text = (REPO / "ASSISTANT.md").read_text(encoding="utf-8")
    assert text.count(ADDRESS) == ADDRESS_MENTIONS
    assert re.search(rf"^{re.escape(ADDRESS)}$", text, re.MULTILINE)
    assert text.startswith("# Connect your assistant\n")
    assert "](PRIVACY.md)" in text
    assert "](TERMS.md)" in text
    assert "<script" not in text
    allowed = r"dncodqeqpoqwcxdctzco\.supabase\.co/functions/v1/mcp|chatgpt\.com/plugins|ravidorr\.github\.io/misra/"
    assert not re.search(rf"https?://(?!{allowed})", text)


def test_the_markdown_carries_the_lawyers_block_of_2026_11_17_word_for_word_and_the_limits() -> None:
    text = (REPO / "ASSISTANT.md").read_text(encoding="utf-8")
    heading = "### What your assistant can see\u2014and the rules for using it\n"
    assert heading in text
    for sentence in (
        "Your assistant can search open listings and read the full description of one job at a time (up to 20,000 "
        "characters, for open roles or roles closed less than 30 days). It cannot see your Misra account, email "
        'address, stars, "Not for me" choices, or saved filters.',
        "Connecting Misra requires no sign-in and sends nothing personal to us\u2014your resume and prompts stay "
        "inside your own AI assistant. To enforce daily limits (100 searches and 15 full-job reads per day), we "
        "store only a daily, non-reversible hash of your network address, which is deleted during the next daily "
        "cleanup.",
        'All job postings and descriptions belong to the respective employers and are provided "as is". You may '
        "use this connection only for your own personal job search\u2014bulk copying, republishing, or training "
        "models on the postings is not allowed.",
        "Misra is a free, unofficial project and is not affiliated with any employer. Your AI assistant\u2014not "
        "Misra\u2014is responsible for anything it writes (such as cover letters or summaries). Always verify "
        "details on the employer's own career page before applying.",
        "By connecting your assistant, you agree to the [Terms](https://ravidorr.github.io/misra/terms.html) and "
        "[Privacy Notice](https://ravidorr.github.io/misra/privacy.html).",
    ):
        assert sentence in text, sentence
    assert "(only a daily, non-reversible hash is used to count rate limits; see the privacy notice)" in text
    assert text.count("\n## What your assistant can see\n") == 1  # the bullets stand alone, no paragraph repeats them
    assert "Postings belong to the employers" not in text
    assert "It resets the next day (UTC)." in text
    assert text.index("](TERMS.md)") < text.index("## Claude")  # the links sit next to the address


def test_the_page_renders_in_the_template_with_the_address_in_a_code_block() -> None:
    html = build_dashboard.render_legal(build_dashboard.ASSISTANT_PAGE, REPO)
    assert "<title>Connect your assistant - Misra</title>" in html
    assert f'<pre><code class="language-text">{ADDRESS}\n</code></pre>' in html
    assert '<a href="assistant.html" aria-current="page">Connect your assistant</a>' in html  # its own footer link
    assert '<a href="privacy.html">privacy notice</a>' in html
    assert "<script" not in re.sub(
        r'<script src="theme.js"[^>]*></script>', "", html
    )  # the page has no script of its own


def test_the_real_dashboard_marks_the_link_so_the_switch_cuts_all_of_it() -> None:
    template = (REPO / "templates" / "parts" / "footer.inc").read_text(encoding="utf-8")
    assert template.count("<!--assistant-->") == template.count("<!--/assistant-->") == 1
    cut = build_dashboard.ASSISTANT_REGION.sub("", template)
    assert "assistant.html" not in cut
    removed = build_dashboard.ASSISTANT_LINE.sub("", template)
    assert len(removed.splitlines()) == len(template.splitlines()) - 1  # the whole line goes, no blank one is left
    assert "<!--assistant-->" not in removed


@pytest.fixture
def assistant_site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real page built with the assistant switch on (and the daily-email one off, as the other page tests do)."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    monkeypatch.setattr(build_dashboard, "ASSISTANT_PAGE_ENABLED", True)
    return build_site(db, workdir)


def test_with_the_switch_on_the_page_is_built_and_linked(assistant_site: Path) -> None:
    html = (assistant_site / "assistant.html").read_text(encoding="utf-8")
    assert "<h1" in html
    assert "__SRI" not in html
    assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]+"', html)
    index = (assistant_site / "index.html").read_text(encoding="utf-8")
    assert '<a href="assistant.html">Connect your assistant</a>' in index
    assert "<!--assistant-->" not in index
    assert "<!--/assistant-->" not in index


def test_with_the_switch_off_there_is_no_page_no_link_and_an_old_page_goes(
    db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    monkeypatch.setattr(build_dashboard, "ASSISTANT_PAGE_ENABLED", False)
    (workdir / "docs").mkdir(exist_ok=True)
    (workdir / "docs" / "assistant.html").write_text("a page of an earlier build", encoding="utf-8")
    docs = build_site(db, workdir)
    assert not (docs / "assistant.html").exists()
    assert "assistant" not in (docs / "index.html").read_text(encoding="utf-8").lower()
    for built in docs.iterdir():  # nothing of the real build names the page or the link, not even a CSS comment
        text = built.read_text(encoding="utf-8", errors="ignore")
        assert "assistant.html" not in text, built.name
        if built.name != "about.html":  # the About page names the assistant, and links to the file on GitHub
            assert "Connect your assistant" not in text, built.name


async def test_the_page_has_no_accessibility_problem_in_light_and_dark(page: Page, assistant_site: Path) -> None:
    for scheme in ("light", "dark"):
        await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
        problems = await open_legal(page, assistant_site, "assistant.html")
        await page.evaluate(AXE.read_text(encoding="utf-8"))
        assert await page.evaluate(AXE_RUN) == [], scheme
        assert problems == []


async def test_the_page_fits_a_small_phone_and_its_links_are_big_enough_to_tap(
    page: Page, assistant_site: Path
) -> None:
    await page.set_viewport_size(SMALL_PHONE)  # type: ignore[arg-type]
    await open_legal(page, assistant_site, "assistant.html")
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    await page.set_viewport_size(PHONE)  # type: ignore[arg-type]
    for link in await page.locator("header a, footer a").all():
        box = await link.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN, await link.inner_text()


async def test_the_dashboard_footer_leads_to_the_page(page: Page, assistant_site: Path) -> None:
    await open_legal(page, assistant_site, "index.html")
    await page.get_by_role("link", name="Connect your assistant").click()
    await page.wait_for_url("**/assistant.html")
    assert await page.get_by_role("heading", level=1).inner_text() == "Connect your assistant"
