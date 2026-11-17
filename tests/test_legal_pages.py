"""The privacy, terms and security pages of the site (docs/privacy.html, terms.html, security.html).

The Markdown files of the repository are the source of truth and the build converts them, so these tests check three
things: the converter (raw HTML escaped, links rewritten, anchors), that each real document reaches its page whole
(the sync tests, which read the page with a parser of their own and never reuse the converter's output as the
expectation), and the workflow that republishes the pages when a Markdown file changes.

Locally the browser tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import mimetypes
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest
from markdown_it import MarkdownIt
from playwright.async_api import Page, Route

import build_dashboard
from tests.conftest import add_crawls
from tests.test_build_dashboard import prepare, read_payload
from tests.test_dashboard_page import AXE, ORIGIN, PHONE, TAP_MIN, build_site, site  # noqa: F401  (site is a fixture)
from tests.test_sign_in import AXE_RUN

REPO = Path(__file__).resolve().parents[1]
BLOB = "https://github.com/ravidorr/misra/blob/main"
DOCUMENTS = [(page.source, page.output) for page in build_dashboard.LEGAL_PAGES]
BY_OUTPUT = {page.output: page for page in build_dashboard.LEGAL_PAGES}
DATE_LINES = {
    "PRIVACY.md": r"Last updated: \d{4}-\d{2}-\d{2}",
    "TERMS.md": r"Last updated: \d{4}-\d{2}-\d{2}",
    "SECURITY.md": (
        r"Written \d{4}-\d{2}-\d{2} by the maintainer, Raanan Avidor\. "
        r"Next full review due by \d{4}-\d{2}-\d{2}\."
    ),
}
SMALL_PHONE = {"width": 360, "height": 800}
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def convert(text: str) -> str:
    return build_dashboard.markdown_to_html(text)


# ---- The converter ----


def test_raw_html_in_the_markdown_is_escaped_and_never_passed_through() -> None:
    html = convert(
        '<script>alert(1)</script>\n\nA <b onclick="x()">bold</b> word and <img src=x onerror=y> here.\n\n'
        '<div style="color:red">block</div>\n\n[x](javascript:alert(1)) and <span>inline</span>\n'
    )
    assert "<script" not in html
    assert "<b " not in html
    assert "<img" not in html
    assert "<div" not in html
    assert "<span>" not in html
    assert not re.search(r"<[^>]*\sstyle=", html)  # escaped text may say style=, a tag may not
    assert 'href="javascript' not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b onclick=&quot;x()&quot;&gt;bold&lt;/b&gt;" in html


def test_a_bare_address_is_not_made_into_a_link_but_an_angle_bracket_address_is() -> None:
    html = convert("See https://example.com/page and www.example.org or write <raanan@avidor.org>.\n")
    assert "<a " in html
    assert html.count("<a ") == 1  # only the autolink in angle brackets
    assert '<a href="mailto:raanan@avidor.org">raanan@avidor.org</a>' in html


def test_every_heading_gets_the_id_github_gives_it_and_a_repeat_gets_a_number() -> None:
    html = convert(
        "# Privacy\n\n## Why we collect it, and whether you have to give it\n\n## Q&A: `code` (2)\n\n### Same\n\n"
        "### Same\n\n### Same\n\n## ???\n\n## ???\n"
    )
    assert '<h1 id="privacy">' in html
    assert '<h2 id="why-we-collect-it-and-whether-you-have-to-give-it">' in html
    assert '<h2 id="qa-code-2">' in html
    assert '<h3 id="same">' in html
    assert '<h3 id="same-1">' in html
    assert '<h3 id="same-2">' in html
    assert '<h2 id="section">' in html  # nothing left of the title
    assert '<h2 id="section-1">' in html


def test_a_table_is_rendered_and_no_style_attribute_comes_with_it() -> None:
    html = convert("| A | B |\n| --- | :-: |\n| 1 | 2 |\n")
    assert "<table>" in html
    assert "<th>A</th>" in html
    assert "<th>B</th>" in html
    assert not re.search(r"<[^>]*\sstyle=", html)  # a column's alignment would come as a style attribute


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("./PRIVACY.md", "privacy.html"),
        ("PRIVACY.md", "privacy.html"),
        ("./TERMS.md", "terms.html"),
        ("TERMS.md", "terms.html"),
        ("./SECURITY.md", "security.html"),
        ("SECURITY.md", "security.html"),
        ("./PRIVACY.md#your-rights", "privacy.html#your-rights"),
        ("PRIVACY.md#your-rights", "privacy.html#your-rights"),
        ("/TERMS.md", "terms.html"),
        ("docs/../PRIVACY.md", "privacy.html"),
        ("./CONTRIBUTING.md", f"{BLOB}/CONTRIBUTING.md"),
        ("SUPPORT.md", f"{BLOB}/SUPPORT.md"),
        ("./CHANGELOG.md#unreleased", f"{BLOB}/CHANGELOG.md#unreleased"),
        ("./supabase/tests/", "https://github.com/ravidorr/misra/tree/main/supabase/tests"),
        ("./README.md?plain=1#x", f"{BLOB}/README.md?plain=1#x"),
        ("#your-rights", "#your-rights"),
        ("mailto:raanan@avidor.org", "mailto:raanan@avidor.org"),
        ("https://example.com/PRIVACY.md", "https://example.com/PRIVACY.md"),
        ("//example.com/x", "//example.com/x"),
    ],
)
def test_relative_links_are_rewritten_and_the_others_are_left_alone(href: str, expected: str) -> None:
    assert build_dashboard.rewrite_href(href) == expected


@pytest.mark.parametrize("href", ["..", "../secret.md", "./", "."])
def test_a_link_that_leaves_the_repository_stops_the_build(href: str) -> None:
    with pytest.raises(ValueError, match="does not point to a file of the repository"):
        build_dashboard.rewrite_href(href)
    with pytest.raises(ValueError, match="does not point"):
        convert(f"[x]({href})\n")


def test_an_image_stops_the_build() -> None:
    with pytest.raises(ValueError, match="Content-Security-Policy"):
        convert("![logo](logo.png)\n")


def test_links_on_the_page_work_on_the_site_and_the_outside_ones_open_in_a_new_tab_and_say_so() -> None:
    html = convert(
        "[terms](./TERMS.md), [file](./SUPPORT.md), [mail](mailto:a@b.test), [out](https://github.com/x/y/issues) and "
        "[top](#top).\n"
    )
    assert '<a href="terms.html">terms</a>' in html  # the same site: the same tab, nothing to announce
    assert '<a href="#top">top</a>' in html
    assert '<a href="mailto:a@b.test">mail</a>' in html
    opens = ' target="_blank" rel="noopener noreferrer">'
    assert f'<a href="{BLOB}/SUPPORT.md"{opens}file<span class="sr-only"> (opens in a new tab)</span></a>' in html
    assert (
        f'<a href="https://github.com/x/y/issues"{opens}out<span class="sr-only"> (opens in a new tab)</span></a>'
        in html
    )
    assert html.count("opens in a new tab") == 2  # noqa: PLR2004


def test_the_words_of_the_markdown_are_never_read_as_a_placeholder_of_the_template(workdir: Path) -> None:
    prepare(workdir)
    (workdir / "PRIVACY.md").write_text(
        "# Privacy\n\nWords: `__TITLE__`, `__OUTPUT__`, `__SRI_THEME_JS__`.\n", encoding="utf-8"
    )
    html = build_dashboard.render_legal(build_dashboard.LEGAL_PAGES[0])
    assert "<title>Privacy notice - Misra</title>" in html
    assert "<code>__SRI_THEME_JS__</code>" in html


# ---- The build ----


def test_the_build_writes_the_pages_from_the_markdown_files(workdir: Path, db: sqlite3.Connection) -> None:
    prepare(workdir)
    (workdir / "TERMS.md").write_text("# Terms\n\nSee the [privacy notice](./PRIVACY.md).\n", encoding="utf-8")
    db.close()
    build_dashboard.main()
    assert sorted(p.name for p in (workdir / "docs").glob("*.html")) == [
        "about.html",
        "assistant.html",
        "email.html",
        "google0e1aa15dbd543e2f.html",
        "index.html",
        "privacy.html",
        "security.html",
        "terms.html",
    ]
    terms = (workdir / "docs" / "terms.html").read_text(encoding="utf-8")
    assert '<a href="privacy.html">privacy notice</a>' in terms
    assert "<title>Terms of use - Misra</title>" in terms


def test_the_pages_carry_the_integrity_hash_of_the_stylesheet_they_load(workdir: Path, db: sqlite3.Connection) -> None:
    prepare(workdir)
    (workdir / "PRIVACY.md").write_text("# Privacy\n\n`__SRI_THEME_JS__`\n", encoding="utf-8")  # a word of the Markdown
    (workdir / "templates" / "legal.html").write_text(
        '<link href="dashboard.css" integrity="__SRI_DASHBOARD_CSS__">'
        '<script src="theme.js" integrity="__SRI_THEME_JS__"></script>__BODY__',
        encoding="utf-8",
    )
    db.close()
    build_dashboard.main()
    docs = workdir / "docs"
    css = build_dashboard.integrity(docs / "dashboard.css")
    theme = build_dashboard.integrity(docs / "theme.js")
    for _source, output in DOCUMENTS:
        page = (docs / output).read_text(encoding="utf-8")
        assert f'integrity="{css}"' in page
        assert f'integrity="{theme}"' in page
        assert "__SRI_" not in page.replace("<code>__SRI_THEME_JS__</code>", "")


def test_the_build_time_shown_is_the_latest_crawl_so_a_rebuild_changes_nothing(
    workdir: Path, db: sqlite3.Connection
) -> None:
    prepare(workdir)
    add_crawls(db, "Acme", [(1, None), (1, None)])  # 2026-01-01 and 2026-01-02, 06:00
    db.close()
    build_dashboard.main()
    first = {p.name: p.read_bytes() for p in (workdir / "docs").iterdir()}
    assert read_payload(workdir)["generated"] == "2026-01-02 06:00 UTC"
    build_dashboard.main()
    assert {p.name: p.read_bytes() for p in (workdir / "docs").iterdir()} == first


# ---- The sync tests: the page says what the Markdown says ----


class Body(HTMLParser):
    """What the converted page shows: its headings, links, ids and text, from the `main` element only; the words only
    a screen reader gets are left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[tuple[int, str, str]] = []
        self.links: list[dict[str, str | None]] = []
        self.ids: list[str] = []
        self.chunks: list[str] = []
        self.in_main = False
        self.hidden = False
        self.heading: tuple[int, str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "main":
            self.in_main = True
        if not self.in_main:
            return
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag in HEADINGS:
            self.heading = (int(tag[1]), values.get("id") or "", [])
        if tag == "a":
            self.links.append(values)
        if tag == "span" and values.get("class") == "sr-only":
            self.hidden = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
        if tag == "span":
            self.hidden = False
        if tag in HEADINGS and self.heading:
            level, ident, words = self.heading
            self.headings.append((level, ident, " ".join("".join(words).split())))
            self.heading = None

    def handle_data(self, data: str) -> None:
        if self.in_main and not self.hidden:
            self.chunks.append(data)
            if self.heading:
                self.heading[2].append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self.chunks).split())


def plain(inline: object) -> str:
    """The words of an inline Markdown token."""
    words = []
    for child in getattr(inline, "children", None) or []:
        if child.type in {"text", "code_inline"}:
            words.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            words.append(" ")
    return " ".join("".join(words).split())


def site_href(href: str) -> str:
    """Where a link of a Markdown file must lead on the site (written out here, apart from the build's own code)."""
    if re.match(r"^(https?:|mailto:|#)", href):
        return href
    name, _, fragment = href.removeprefix("./").partition("#")
    local = {"PRIVACY.md": "privacy.html", "TERMS.md": "terms.html", "SECURITY.md": "security.html"}
    target = local.get(name, f"{BLOB}/{name}")
    return f"{target}#{fragment}" if fragment else target


def read_page(output: str) -> Body:
    parsed = Body()
    parsed.feed(build_dashboard.render_legal(BY_OUTPUT[output], REPO))
    return parsed


@pytest.mark.parametrize(("source", "output"), DOCUMENTS)
def test_every_heading_of_the_markdown_is_on_the_page_in_order_with_an_anchor(source: str, output: str) -> None:
    tokens = MarkdownIt("commonmark").enable("table").parse((REPO / source).read_text(encoding="utf-8"))
    expected = [(int(t.tag[1]), plain(tokens[i + 1])) for i, t in enumerate(tokens) if t.type == "heading_open"]
    assert expected
    page = read_page(output)
    assert [(level, words) for level, _id, words in page.headings] == expected
    assert all(ident for _level, ident, _words in page.headings)
    assert len(set(page.ids)) == len(page.ids)  # an anchor names one place only


@pytest.mark.parametrize(("source", "output"), DOCUMENTS)
def test_every_link_of_the_markdown_is_on_the_page_pointing_where_it_must(source: str, output: str) -> None:
    tokens = MarkdownIt("commonmark").enable("table").parse((REPO / source).read_text(encoding="utf-8"))
    hrefs = [
        str(child.attrGet("href"))
        for token in tokens
        if token.type == "inline"
        for child in token.children or []
        if child.type == "link_open"
    ]
    assert hrefs
    page = read_page(output)
    assert [link["href"] for link in page.links] == [site_href(href) for href in hrefs]
    for link in page.links:
        href = link["href"] or ""
        assert not href.endswith(".md")
        assert "/blob/main/" not in href or link.get("target") == "_blank"
        if href.startswith("http"):
            assert link["rel"] == "noopener noreferrer"
        if href.startswith("#"):
            assert href[1:] in page.ids  # an anchor link of the document leads somewhere
        if "#" in href and not href.startswith(("#", "http")):
            assert href.split("#")[1] in read_page(href.split("#")[0]).ids  # and so does one to another page


@pytest.mark.parametrize(("source", "output"), DOCUMENTS)
def test_every_paragraph_list_item_and_table_cell_of_the_markdown_is_on_the_page_in_order(
    source: str, output: str
) -> None:
    tokens = MarkdownIt("commonmark").enable("table").parse((REPO / source).read_text(encoding="utf-8"))
    texts = [plain(t) for t in tokens if t.type == "inline"]
    assert len(texts) > 5  # noqa: PLR2004
    text = read_page(output).text
    start = 0
    for words in texts:
        found = text.find(words, start)
        assert found >= 0, f"{source}: {words!r} is missing from {output} (or out of order)"
        start = found + len(words)


@pytest.mark.parametrize(("source", "output"), DOCUMENTS)
def test_the_page_carries_the_date_line_of_the_document_word_for_word(source: str, output: str) -> None:
    markdown = " ".join((REPO / source).read_text(encoding="utf-8").split())
    found = re.search(DATE_LINES[source], markdown)
    assert found, f"{source} has lost its date line"
    assert found[0] in read_page(output).text


@pytest.mark.parametrize(("source", "output"), DOCUMENTS)
def test_the_page_is_framed_by_the_template(source: str, output: str) -> None:
    page = BY_OUTPUT[output]
    html = build_dashboard.render_legal(page, REPO)
    assert f"<title>{page.title}</title>" in html
    assert f'<meta name="description" content="{page.description}">' in html
    assert f'<link rel="canonical" href="https://ravidorr.github.io/misra/{output}">' in html
    assert '<a class="top-home" href="./">' in html  # the logo and "misra" are the way back
    assert "Open Misra dashboard" not in html
    assert "noindex" not in html  # these pages are meant to be found
    assert not re.search(r"__(?!SRI_)[A-Z_]+__", html)  # every placeholder but the integrity hashes is filled in


@pytest.mark.parametrize(("source", "output"), DOCUMENTS)
def test_the_page_has_no_built_from_line_and_no_link_to_its_source_or_history(source: str, output: str) -> None:
    html = build_dashboard.render_legal(BY_OUTPUT[output], REPO)
    footer = html.split('<footer class="foot">')[1]
    assert "built from" not in footer.lower()
    assert "legal-source" not in html
    assert "github.com" not in footer  # the footer leads to the other pages of the site only
    assert f"{BLOB}/{source}" not in html  # no link to the page's own Markdown file
    assert f"github.com/ravidorr/misra/commits/main/{source}" not in html  # nor to its history
    for target in ("about.html", "privacy.html", "terms.html", "security.html"):  # the footer navigation is still there
        assert f'<a href="{target}"' in html
    assert 'aria-label="Site pages"' in html


def test_the_template_has_no_words_of_its_own_to_drift_from_the_markdown() -> None:
    template = (REPO / "templates" / "legal.html").read_text(encoding="utf-8")
    main = template.split('<main class="legal card" id="main">')[1].split("</main>")[0]
    assert main.strip() == "__BODY__"  # the whole of the main region is the converted Markdown
    assert not re.search(r"<h[1-6]", template)
    assert "templates/legal.html" not in build_dashboard.STATIC_FILES  # never copied as it is


def test_the_page_has_the_same_security_policy_as_the_email_page_without_inline_script_or_style() -> None:
    legal = (REPO / "templates" / "legal.html").read_text(encoding="utf-8")
    email = (REPO / "templates" / "email.html").read_text(encoding="utf-8")
    pattern = r'http-equiv="Content-Security-Policy" content="([^"]+)"'
    legal_csp = re.search(pattern, legal)
    email_csp = re.search(pattern, email)
    assert legal_csp
    assert email_csp
    assert "script-src 'self'" in legal_csp[1]
    assert "style-src 'self'" in legal_csp[1]
    assert "connect-src" not in legal_csp[1]  # the page talks to nobody
    assert legal_csp[1] == email_csp[1].replace(" connect-src __SUPABASE_URL__;", "")
    assert not re.search(r"<script(?![^>]*\ssrc=)", legal)
    assert not re.search(r"<style|\sstyle=|\son[a-z]+=", legal)


def test_every_document_that_has_a_page_is_in_the_list_the_build_and_the_workflow_agree_on() -> None:
    assert [source for source, _out in DOCUMENTS] == ["PRIVACY.md", "TERMS.md", "SECURITY.md"]
    for _source, output in DOCUMENTS:
        assert f"docs/{output}" in (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    workflow = (REPO / ".github" / "workflows" / "publish-docs.yml").read_text(encoding="utf-8")
    for source, _out in DOCUMENTS:
        assert f"      - {source}\n" in workflow  # a change to the Markdown republishes the page


# ---- The workflow that publishes them between two daily runs ----


def workflow(name: str) -> str:
    return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_the_dependency_is_pinned_exactly_and_the_same_everywhere() -> None:
    pin = re.search(r'"markdown-it-py==([\d.]+)"', (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pin
    for name in ("daily.yml", "publish-docs.yml"):
        assert f"markdown-it-py=={pin[1]}" in workflow(name), name


def test_the_publish_workflow_starts_on_the_sources_of_the_pages_and_never_on_its_own_commit() -> None:
    text = workflow("publish-docs.yml")
    paths = re.search(r"push:\n    branches: \[main\]\n(?:    #.*\n)*    paths:\n((?:      - .+\n)+)", text)
    assert paths
    listed = [line.removeprefix("      - ") for line in paths[1].splitlines()]
    assert listed == [
        "PRIVACY.md",
        "TERMS.md",
        "SECURITY.md",
        "ASSISTANT.md",
        "ABOUT.md",
        "templates/**",
        "design-system/**",
        "vendor/**",
        "build_dashboard.py",
    ]
    assert not any(
        path.startswith(("docs", "data", ".github", "**")) for path in listed
    )  # its own commit is docs/ only
    assert "workflow_dispatch" in text
    assert "schedule:" not in text
    assert "pull_request" not in text


def test_the_publish_workflow_waits_for_the_daily_run_and_never_cancels_it() -> None:
    daily = re.search(r"concurrency:\n  group: (\S+)\n  cancel-in-progress: false", workflow("daily.yml"))
    publish = re.search(r"concurrency:\n  group: (\S+)\n  cancel-in-progress: false", workflow("publish-docs.yml"))
    assert daily
    assert publish
    assert publish[1] == daily[1]


def test_the_publish_workflow_pulls_and_builds_only_and_touches_only_docs() -> None:
    text = workflow("publish-docs.yml")
    runs = re.findall(r"python (\S+(?: --pull)?)", text)
    assert runs == ["sync_supabase.py --pull", "build_dashboard.py"]  # the history, then the build: no crawl, no sync
    for forbidden in ("run.py", "send_email", "playwright", "GMAIL", "RESEND", "ssh-key:"):
        assert forbidden not in text, forbidden
    # the deploy key, and the read of the history in its own step (never in the build or the push)
    assert re.findall(r"secrets\.([A-Z_]+)", text) == [
        "DEPLOY_KEY",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "DEPLOY_KEY",
    ]
    pull = next(step for step in text.split("      - ") if "sync_supabase.py --pull" in step)
    assert "DEPLOY_KEY" not in pull
    assert text.index("sync_supabase.py --pull") < text.index("build_dashboard.py")
    assert "git add --all -- docs\n" in text
    assert "git add data" not in text
    assert text.count("git push") == 1
    assert "persist-credentials: false" in text
    assert "ref: main" in text
    assert "refs/heads/main" in text  # refuses to run from another branch


def test_the_publish_workflow_writes_the_key_only_in_the_push_step_and_skips_when_nothing_changed() -> None:
    text = workflow("publish-docs.yml")
    steps = text.split("      - ")
    keyed = [step for step in steps if "scripts/deploy-key.sh" in step]
    assert len(keyed) == 1  # the key is written in the push step only, never in an early check
    assert "ssh-keygen" not in text
    build = next(step for step in steps if "build_dashboard.py" in step and "run:" in step)
    assert "DEPLOY_KEY" not in build  # the build never has the key
    push = next(step for step in steps if "git push" in step)
    assert push.index("git diff --cached --quiet") < push.index("deploy-key.sh") < push.index("git push")
    assert "exit 0" in push.split("deploy-key.sh")[0]  # nothing to commit: stop before the key is written


# ---- The pages in a browser ----


async def open_legal(page: Page, docs: Path, name: str) -> list[str]:
    problems: list[str] = []

    async def handler(route: Route) -> None:
        file = docs / (urlparse(route.request.url).path.lstrip("/") or "index.html")
        if file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or "text/plain")
        else:
            await route.fulfill(status=404)

    await page.route(f"{ORIGIN}/**", handler)
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    await page.goto(f"{ORIGIN}/{name}")
    return problems


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, {"width": 1280, "height": 900}], ids=["phone", "desktop"])
@pytest.mark.parametrize(("_source", "output"), DOCUMENTS)
async def test_the_pages_have_no_axe_violations_in_light_and_dark_on_a_phone_and_a_desktop(
    page: Page, site: Path, output: str, size: dict[str, int], scheme: str, _source: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    problems = await open_legal(page, site, output)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert problems == []  # nothing the policy blocks, no script error


@pytest.mark.parametrize(("_source", "output"), DOCUMENTS)
async def test_the_pages_never_scroll_sideways_on_a_small_phone(
    page: Page, site: Path, output: str, _source: str
) -> None:
    await page.set_viewport_size(SMALL_PHONE)  # type: ignore[arg-type]
    await open_legal(page, site, output)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


async def test_the_table_of_the_security_page_fits_a_small_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(SMALL_PHONE)  # type: ignore[arg-type]
    await open_legal(page, site, "security.html")
    box = await page.locator("main table").bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= SMALL_PHONE["width"]


@pytest.mark.parametrize(("_source", "output"), DOCUMENTS)
async def test_the_links_of_the_footer_and_the_header_are_big_enough_to_tap(
    page: Page, site: Path, output: str, _source: str
) -> None:
    await page.set_viewport_size(PHONE)  # type: ignore[arg-type]
    await open_legal(page, site, output)
    links = page.locator("header a, footer a")
    assert await links.count() >= 7  # noqa: PLR2004  (the logo, the theme button is not a link, six footer pages)
    for link in await links.all():
        box = await link.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN, await link.inner_text()
        assert box["width"] >= TAP_MIN, await link.inner_text()


async def test_a_link_to_a_section_of_another_page_opens_it_at_that_section(page: Page, site: Path) -> None:
    await open_legal(page, site, "terms.html")
    await page.get_by_role("link", name="privacy notice").first.click()
    await page.wait_for_url(f"{ORIGIN}/privacy.html")
    await page.goto(f"{ORIGIN}/privacy.html#your-rights")
    top = await page.evaluate("document.getElementById('your-rights').getBoundingClientRect().top")
    assert top < 100  # noqa: PLR2004  (scrolled to the heading)


async def test_the_logo_and_the_name_are_one_link_to_the_dashboard(page: Page, site: Path) -> None:
    await open_legal(page, site, "privacy.html")
    home = page.get_by_role("link", name="misra", exact=True)
    assert await home.count() == 1
    assert await home.get_attribute("href") == "./"
    assert await home.locator("svg.top-mark").count() == 1
    external = page.locator("footer a[target=_blank]")
    assert await external.count() == 0  # the footer only leads to the other pages of the site


# ---- The header and the footer every page shares (templates/parts/) ----

PAGES_WITH_CHROME = ["index.html", "email.html", "about.html", "assistant.html", *(out for _s, out in DOCUMENTS)]


def chrome_of(html: str) -> tuple[str, str]:
    """The header and the footer of a built page, without the marks that differ by page."""
    header = re.search(r'<header class="top">.*?</header>', html, re.DOTALL)
    footer = re.search(r'<footer class="foot">.*?</footer>', html, re.DOTALL)
    assert header
    assert footer
    marked = lambda s: s.replace(' aria-current="page"', "")  # noqa: E731
    footer_text = re.sub(r"\s*<p class=\"foot-updated\">.*?</p>", "", marked(footer[0]), flags=re.DOTALL)
    header_text = re.sub(r'\s*<div class="account".*?\n  </div>', "", marked(header[0]), flags=re.DOTALL)
    return header_text, footer_text


def test_every_page_has_the_same_header_and_footer_apart_from_the_dashboards_two_slots(
    workdir: Path, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    docs = build_site(db, workdir)
    built = {name: chrome_of((docs / name).read_text(encoding="utf-8")) for name in PAGES_WITH_CHROME}
    assert len(set(built.values())) == 1, "the pages do not share one header and one footer"
    index = (docs / "index.html").read_text(encoding="utf-8")
    assert 'id="account"' in index
    assert 'id="upd"' in index
    for name in PAGES_WITH_CHROME[1:]:
        other = (docs / name).read_text(encoding="utf-8")
        assert 'id="account"' not in other  # they load no sign-in script
        assert 'id="upd"' not in other  # and nothing that would fill the date
        assert "__" not in re.sub(r"__SRI_[A-Z_]+__", "", other).replace("__BODY__", "")


def test_the_link_of_the_page_shown_is_marked_and_no_other(
    workdir: Path, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    docs = build_site(db, workdir)
    for name in PAGES_WITH_CHROME:
        html = (docs / name).read_text(encoding="utf-8")
        marked = re.findall(r'<a [^>]*href="([^"]+)"[^>]*aria-current="page"', html)
        assert marked == (["./"] if name == "index.html" else [] if name == "email.html" else [name]), name


def test_with_the_assistant_switch_off_no_page_links_to_the_assistant_page(
    workdir: Path, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    monkeypatch.setattr(build_dashboard, "ASSISTANT_PAGE_ENABLED", False)
    docs = build_site(db, workdir)
    assert not (docs / "assistant.html").exists()
    for name in PAGES_WITH_CHROME:
        if name != "assistant.html":
            assert "assistant.html" not in (docs / name).read_text(encoding="utf-8"), name
    assert f"{BLOB}/ASSISTANT.md" in (docs / "about.html").read_text(encoding="utf-8")  # the file, not a missing page


def test_the_footer_lists_each_page_once_in_the_order_the_writer_chose() -> None:
    footer = (REPO / "templates" / "parts" / "footer.inc").read_text(encoding="utf-8")
    hrefs = re.findall(r'<a href="([^"]+)"', footer)
    assert [h.split("@")[0] for h in hrefs] == [
        "about.html",
        "assistant.html",
        "mailto:raanan",
        "privacy.html",
        "terms.html",
        "security.html",
    ]
    assert 'aria-label="Site pages"' in footer


def test_the_about_page_is_built_from_about_md_and_not_among_the_lawyers_documents() -> None:
    assert build_dashboard.ABOUT_PAGE.source == "ABOUT.md"
    assert build_dashboard.ABOUT_PAGE not in build_dashboard.LEGAL_PAGES
    html = build_dashboard.render_legal(build_dashboard.ABOUT_PAGE, REPO)
    assert "<title>About - Misra</title>" in html
    assert '<a href="privacy.html">privacy notice</a>' in html
    assert "mailto:" not in html.split("<footer")[0]  # the page itself carries no address
    assert f"{BLOB}/ABOUT.md" not in html


def test_about_md_names_no_address_and_says_nothing_the_pages_do_not_back() -> None:
    text = (REPO / "ABOUT.md").read_text(encoding="utf-8")
    assert "@" not in text
    assert "—" not in text
    assert text.startswith("# About Misra\n")
    for sentence in ("once a day", "Sundays", "no sign-in"):
        assert sentence in text
