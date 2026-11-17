"""Optional Google sign-in (PLAN.md step 4.1; PRD FR-1 AC 1, 2, 3 and 6), run in a real browser against the built page
with a fake sign-in service. AC 3 (a user sees and changes only their own rows) lives in the database: see
tests/test_migrations.py and supabase/tests/accounts_rls.sql. AC 4 and 5 are in tests/test_delete_account.py.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import asyncio
import base64
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.test_dashboard_page import (
    AXE,
    EMAIL,
    NAME,
    ORIGIN,
    PAGE_SIZE,
    PHONE,
    PHOTO,
    TAP,
    TAP_MIN,
    USER_ID,
    AuthBackend,
    account_action,
    account_action_by_keyboard,
    serve,
    site,  # noqa: F401  (a fixture)
)

AXE_RUN = "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
STORAGE_KEY = "misra-auth"
FOOTER_LINKS = 6
RETRIES = 50
HEADER_GAP = 8
POLL_SECONDS = 0.1
CANCELLED = "The sign-in was cancelled."
NOT_FINISHED = "The sign-in did not finish."
STUB = """
const quiet = {
  onAuthStateChange() {},
  initialize: async () => ({ error: null }),
  getSession: async () => ({ data: {} }),
};
const stub = (auth) => ({ createClient: () => ({ auth: { ...quiet, ...auth } }) });
Object.defineProperty(window, 'supabase', { configurable: true, get: () => stub(window.__auth ?? {}), set() {} });
"""


PROVIDER_KEYS = {"full_name", "name", "avatar_url", "picture", "identities", "identity_data"}


def all_keys(value: object) -> set[str]:
    """Every key of a JSON value, however deep."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in all_keys(item)}
    return set()


async def start_sign_in(page: Page, auth: AuthBackend) -> str:
    """Press "Sign in", confirm in the notice, and return the address the browser is sent to (the stand-in for Google's
    page)."""
    await page.click("#signIn")
    await page.click("#signInContinue")
    await page.wait_for_url("**/auth/v1/authorize**")
    return page.url


async def come_back(page: Page, query: str = "code=abc123") -> None:
    """The browser returns from Google to the page with the one-time code (or an error)."""
    await page.goto(f"{ORIGIN}/index.html?{query}")


async def sign_in(page: Page, site: Path, auth: AuthBackend | None = None) -> AuthBackend:
    """The whole sign-in: the page, the click, the redirect to Google, the way back with a code."""
    auth = auth or AuthBackend()
    problems = await serve(page, site, auth=auth)
    assert problems == []
    await start_sign_in(page, auth)
    await come_back(page)
    await page.wait_for_selector("#accountUser:not([hidden])")
    return auth


async def stored(page: Page) -> dict[str, str]:
    return await page.evaluate("Object.fromEntries(Object.entries(localStorage))")  # type: ignore[no-any-return]


# ---- AC 1: signed out, the dashboard is whole, and signing in is optional ----


async def test_fr1_ac1_a_visitor_who_is_not_signed_in_sees_the_whole_dashboard(page: Page, site: Path) -> None:
    auth = AuthBackend()
    assert await serve(page, site, auth=auth) == []
    assert await page.locator("#list > li").count() == PAGE_SIZE
    assert await page.locator("#summary").inner_text() == "Showing 12 of 12 open roles in Israel. 0 discarded."
    assert await page.locator("#hiring li button.story").count() == 1
    assert await page.locator("#signIn").is_visible()
    assert await page.locator("#accountUser").is_hidden()
    assert await page.locator("#accountEmail").inner_text() == ""
    assert await page.locator("#authMessage").inner_text() == ""
    assert auth.requests == []  # nothing is asked of the sign-in service until the visitor chooses to sign in
    assert await stored(page) == {}  # and nothing is kept


async def test_fr1_ac1_the_page_works_when_the_sign_in_script_does_not_load(page: Page, site: Path) -> None:
    """A blocked or corrupted script leaves no sign-in control, and the roles load as they always did."""
    (site / "supabase.js").unlink()
    await serve(page, site)
    assert await page.locator("#list > li").count() == PAGE_SIZE
    assert await page.locator("#account").is_hidden()
    assert await page.locator("#authMessage").inner_text() == ""


async def test_fr1_ac1_a_changed_sign_in_script_is_refused_by_the_browser(page: Page, site: Path) -> None:
    """The integrity hash in the page does not match a tampered file, so it never runs."""
    script = site / "supabase.js"
    script.write_text(script.read_text(encoding="utf-8") + "\nwindow.tampered = true;", encoding="utf-8")
    await serve(page, site)
    assert await page.evaluate("window.tampered === undefined && typeof window.supabase === 'undefined'")
    assert await page.locator("#account").is_hidden()
    assert await page.locator("#list > li").count() == PAGE_SIZE


class ScriptTags(HTMLParser):
    """The attributes of every script tag of a page."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append(dict(attrs))


async def test_the_page_loads_the_sign_in_scripts_from_its_own_origin_with_integrity_hashes(site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    parser = ScriptTags()
    parser.feed(html)
    by_src = {tag["src"]: tag for tag in parser.scripts if tag.get("src")}
    for name in ("supabase.js", "auth.js"):
        digest = "sha384-" + base64.b64encode(hashlib.sha384((site / name).read_bytes()).digest()).decode()
        assert by_src[name]["integrity"] == digest, name
    assert "default-src 'none'; script-src 'self'; style-src 'self'" in html  # no CDN, no inline script or style
    assert "unsafe-" not in html
    # every script is a file of the page's own origin or a JSON data block: nothing inline runs
    assert all(
        tag.get("src") or tag.get("type") in {"application/json", "application/ld+json"} for tag in parser.scripts
    )


# ---- AC 2: sign in with Google and sign out; there is no password anywhere ----


async def test_fr1_ac2_the_sign_in_button_sends_the_browser_to_google_through_supabase(page: Page, site: Path) -> None:
    auth = AuthBackend()
    await serve(page, site, auth=auth)
    assert await page.locator("#signIn").get_attribute("aria-label") is None
    assert await page.get_by_role("button", name="Sign in with Google").is_visible()
    url = urlparse(await start_sign_in(page, auth))
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}" == build_dashboard.SUPABASE_URL
    assert url.path == "/auth/v1/authorize"
    assert query["provider"] == ["google"]
    assert query["redirect_to"] == [f"{ORIGIN}/index.html"]  # back to this page, without a query or a hash
    assert query["code_challenge_method"] == ["s256"]  # the one-time code flow: no token travels in the address
    assert query["code_challenge"]


async def test_fr1_ac2_the_way_back_from_google_signs_the_user_in_and_shows_the_email(page: Page, site: Path) -> None:
    auth = await sign_in(page, site)
    assert await page.locator("#accountEmail").text_content() == EMAIL
    assert await page.locator("#signIn").is_hidden()
    await page.click("#accountMenuButton")
    assert await page.get_by_role("button", name="Sign out").is_visible()
    exchange = next(r for r in auth.requests if r.url.split("?")[0].endswith("/token"))
    assert "grant_type=pkce" in exchange.url
    body = json.loads(exchange.post_data or "{}")
    assert body["auth_code"] == "abc123"
    assert body["code_verifier"]  # proof that the same browser that asked for the code is the one that uses it
    assert "code=" not in page.url  # the one-time code does not stay in the address bar
    assert await page.locator("#list > li").count() == PAGE_SIZE  # the dashboard is the same, signed in


async def test_fr1_ac2_a_signed_in_user_stays_signed_in_after_a_reload(page: Page, site: Path) -> None:
    auth = await sign_in(page, site)
    requests = len(auth.requests)
    await page.reload()
    await page.wait_for_selector("#accountUser:not([hidden])")
    assert await page.locator("#accountEmail").text_content() == EMAIL
    assert len(auth.requests) == requests  # the saved session is enough: no new request


async def test_fr1_ac2_a_user_can_sign_out(page: Page, site: Path) -> None:
    auth = await sign_in(page, site)
    await account_action(page, "#signOut")
    await page.wait_for_selector("#accountUser", state="hidden")
    assert await page.locator("#signIn").is_visible()
    assert await page.locator("#accountEmail").inner_text() == ""
    logout = auth.requests[-1]
    assert logout.method == "POST"
    assert logout.url.split("?")[0].endswith("/auth/v1/logout")
    assert logout.headers["authorization"].startswith("Bearer ey")  # the user's own token, so the service knows whose
    assert STORAGE_KEY not in await stored(page)
    await page.reload()
    await page.wait_for_selector("#signIn")
    assert await page.locator("#accountUser").is_hidden()


async def test_fr1_ac2_there_is_no_password_anywhere(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("input[type=password], [autocomplete*=password], [name*=pass i]").count() == 0
    await sign_in(page, site)
    assert await page.locator("input[type=password], [autocomplete*=password], [name*=pass i]").count() == 0
    assert await page.locator("form:not(#reportForm)").count() == 0  # no sign-in form; the only form reports a site
    text = (site / "auth.js").read_text(encoding="utf-8")
    for call in ("signInWithPassword", "signUp", "resetPasswordForEmail", "updateUser", "signInWithOtp"):
        assert call not in text


# ---- AC 6: the email is the only personal detail, and the provider's name and photo are discarded ----


async def test_fr1_ac6_the_name_and_photo_from_google_are_neither_shown_nor_stored(page: Page, site: Path) -> None:
    await sign_in(page, site)
    shown = await page.evaluate("document.body.innerText + document.body.innerHTML")
    assert EMAIL in shown
    assert NAME not in shown
    assert PHOTO not in shown
    assert await page.locator("img[src*=photos]").count() == 0
    keys = await stored(page)
    assert all(key.startswith(STORAGE_KEY) for key in keys)  # the session and the library's one-time code verifier
    session = json.loads(keys[STORAGE_KEY])
    assert session["user"]["email"] == EMAIL
    assert session["user"]["id"] == USER_ID
    assert session["user"]["user_metadata"] == {}
    assert "identities" not in session["user"]
    assert not PROVIDER_KEYS & all_keys(session)  # no key left over, however deep
    payload = session["access_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["user_metadata"] == {}  # what the project's access token hook leaves in the token
    assert not PROVIDER_KEYS & all_keys(claims)
    for value in (*keys.values(), await page.evaluate("JSON.stringify(Object.entries(sessionStorage))")):
        assert NAME not in value
        assert "photos.test" not in value
    assert "provider_token" not in keys[STORAGE_KEY]  # Google's own tokens are dropped
    assert "provider_refresh_token" not in keys[STORAGE_KEY]


async def test_fr1_ac6_a_renewed_session_is_stored_without_the_provider_details_too(page: Page, site: Path) -> None:
    """supabase-js saves the whole session again when it renews the token, and the service sends the details again."""
    auth = await sign_in(page, site)
    session = json.loads((await stored(page))[STORAGE_KEY])
    session["expires_at"] = 1  # long expired: the library asks for a new one on the next load
    await page.evaluate("([key, value]) => localStorage.setItem(key, value)", [STORAGE_KEY, json.dumps(session)])
    await page.reload()
    await page.wait_for_selector("#accountUser:not([hidden])")
    for _ in range(RETRIES):  # the renewal is asynchronous: wait for the new expiry to be saved
        if json.loads((await stored(page))[STORAGE_KEY])["expires_at"] > 1:
            break
        await asyncio.sleep(POLL_SECONDS)
    assert any("grant_type=refresh_token" in r.url for r in auth.requests)
    assert NAME not in (await stored(page))[STORAGE_KEY]
    assert not PROVIDER_KEYS & all_keys(json.loads((await stored(page))[STORAGE_KEY]))


# ---- Failures: a clear message, and the dashboard keeps working ----


async def test_a_cancelled_sign_in_says_so_and_cleans_the_address(page: Page, site: Path) -> None:
    await serve(page, site)
    await come_back(page, "error=access_denied&error_code=x&error_description=The+user+denied+access")
    await page.wait_for_selector("#list article")
    message = page.locator("#authMessage")
    assert await message.get_attribute("role") == "alert"
    assert await message.inner_text() == (f"{CANCELLED} You can try again, or keep using the roles without an account.")
    assert page.url == f"{ORIGIN}/index.html"
    assert await page.locator("#signIn").is_visible()


async def test_any_other_error_from_the_way_back_says_the_sign_in_did_not_finish(page: Page, site: Path) -> None:
    await serve(page, site)
    await come_back(page, "q=keep&error=server_error&error_description=%3Cb%3Eevil%3C%2Fb%3E#error=x")
    await page.wait_for_selector("#list article")
    text = await page.locator("#authMessage").inner_text()
    assert text.startswith(NOT_FINISHED)
    assert "evil" not in text  # the address bar's words are never shown
    assert page.url == f"{ORIGIN}/index.html?q=keep"  # the visitor's other query words stay


async def test_a_code_the_service_refuses_says_the_sign_in_did_not_finish(page: Page, site: Path) -> None:
    auth = AuthBackend("bad_code")
    problems = await serve(page, site, auth=auth)
    await start_sign_in(page, auth)
    await come_back(page)
    await page.wait_for_selector("#list article")
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith(NOT_FINISHED)
    assert await page.locator("#signIn").is_visible()
    assert await page.locator("#accountUser").is_hidden()
    assert problems  # the failure is also logged for the maintainer
    assert STORAGE_KEY not in await stored(page)


async def test_a_code_with_no_matching_sign_in_start_says_the_sign_in_did_not_finish(page: Page, site: Path) -> None:
    """A copied or stale link: this browser never asked for the code, so there is nothing to exchange."""
    auth = AuthBackend()
    await serve(page, site, auth=auth)
    await come_back(page)
    await page.wait_for_selector("#list article")
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith(NOT_FINISHED)
    assert "code=" not in page.url
    assert auth.requests == []


@pytest.mark.parametrize(
    ("stub", "message"),
    [
        ("signInWithOAuth: async () => ({ error: new Error('no') })", "Could not start the Google sign-in."),
        ("signInWithOAuth: async () => { throw new Error('offline'); }", "Could not start the Google sign-in."),
    ],
)
async def test_a_sign_in_that_cannot_start_says_so(page: Page, site: Path, stub: str, message: str) -> None:
    await page.add_init_script(STUB + f"window.__auth = {{ {stub} }};")
    problems = await serve(page, site)
    await page.click("#signIn")
    await page.click("#signInContinue")
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith(message)
    assert len(problems) == 1  # logged once


async def test_a_sign_out_the_service_cannot_finish_still_signs_out_here_and_says_so(page: Page, site: Path) -> None:
    auth = AuthBackend("logout_fails")
    await sign_in(page, site, auth)
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    await account_action(page, "#signOut")
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith("You are signed out here")
    assert await page.locator("#signIn").is_visible()  # the saved session is gone from this browser
    assert STORAGE_KEY not in await stored(page)
    assert problems


async def test_a_sign_out_error_from_the_library_is_reported_and_the_saved_session_is_cleared(
    page: Page, site: Path
) -> None:
    """The library gives up before clearing when an expired token cannot be renewed: the page clears it itself."""
    await page.add_init_script(STUB + "window.__auth = { signOut: async () => ({ error: new Error('x') }) };")
    await page.add_init_script("localStorage.setItem('misra-auth', '{\"stale\": true}');")
    await serve(page, site)
    await page.evaluate("document.getElementById('signOut').click()")
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith("You are signed out here")
    assert STORAGE_KEY not in await stored(page)


async def test_a_failed_start_up_says_the_sign_in_did_not_finish(page: Page, site: Path) -> None:
    await page.add_init_script(STUB + "window.__auth = { initialize: async () => ({ error: new Error('x') }) };")
    await serve(page, site)
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith(NOT_FINISHED)
    assert await page.locator("#list > li").count() == PAGE_SIZE


async def test_blocked_browser_storage_does_not_break_the_page_or_the_sign_in_button(page: Page, site: Path) -> None:
    await page.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
    )
    problems = await serve(page, site)
    assert await page.locator("#signIn").is_visible()
    await start_sign_in(page, AuthBackend())
    assert page.url.startswith(f"{build_dashboard.SUPABASE_URL}/auth/v1/authorize")
    assert problems == []


# ---- FR-1 AC 7: the notice shown when "Sign in" is pressed (PLAN.md step 6.6) ----

NOTICE_FIRST = 'Sign in to sync your stars, "Not for me" choices, and saved filters across devices.'
NOTICE_SECOND = (
    "Signing in is optional. We store only your Google email address and job preferences. They are kept in our "
    "database in the EU (Supabase), with a daily backup copy at GitHub in the United States. We never store your CV "
    "or a password, and we set no tracking cookies. "
    # accuracy fix: Supabase's own sign-in record also holds the Google name and photo, as PRIVACY.md says
    "(Supabase's own sign-in record also holds your Google name and photo until you delete your account.) "
    "By signing in, you agree to the Terms and privacy notice."
)
DIALOG = "#signInDialog"


async def test_fr1_ac7_the_header_has_no_notice_only_the_buttons(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("#signInNote").count() == 0
    assert await page.locator(DIALOG).is_hidden()
    assert await page.locator("main > .sign-in-note").count() == 0
    top = page.locator("header.top")
    assert await top.get_by_role("button").count() == len(("Sign in", "Theme"))
    assert await page.locator("#signIn").get_attribute("aria-haspopup") == "dialog"


async def test_fr1_ac7_pressing_sign_in_shows_the_notice_and_does_not_leave_the_page(page: Page, site: Path) -> None:
    auth = AuthBackend()
    await serve(page, site, auth=auth)
    requests = len(auth.requests)
    await page.click("#signIn")
    dialog = page.locator(DIALOG)
    await expect(dialog).to_be_visible()
    assert await dialog.evaluate("e => e.matches(':modal')")
    assert await dialog.get_attribute("aria-labelledby") == "signInTitle"
    assert await dialog.get_attribute("aria-describedby") == "signInText"
    assert await page.evaluate("document.activeElement.id") == "signInTitle"
    assert await page.locator("#signInCancel").get_attribute("aria-label") == "Close"  # the corner (x) has a name
    paragraphs = [" ".join(t.split()) for t in await page.locator("#signInText p").all_text_contents()]
    # the approved text, word for word, and nothing else, except the words that say a link opens in a new tab
    assert [p.replace(" (opens in a new tab)", "") for p in paragraphs] == [NOTICE_FIRST, NOTICE_SECOND]
    text = page.locator("#signInText")
    assert await text.get_by_role("link", name="privacy notice").get_attribute("href") == "privacy.html"
    assert await text.get_by_role("link", name="Terms").get_attribute("href") == "terms.html"
    for link in await text.locator(
        "a"
    ).all():  # a new tab, so the dialog and the page stay: the sign and the words say so
        assert await link.get_attribute("target") == "_blank"
        assert await link.get_attribute("rel") == "noopener"
        assert await link.locator("svg.icon-ext[aria-hidden=true]").count() == 1
        assert await link.locator(".sr-only").inner_text() == "(opens in a new tab)"
    await page.wait_for_timeout(300)
    assert page.url == f"{ORIGIN}/index.html"  # nothing was started by the button
    assert len(auth.requests) == requests
    assert await page.evaluate("Object.keys(localStorage).filter(k => k.includes('code-verifier')).length") == 0


@pytest.mark.parametrize("closer", ["button", "escape"])
async def test_fr1_ac7_cancel_and_escape_leave_the_page_signed_out_and_give_the_focus_back(
    page: Page, site: Path, closer: str
) -> None:
    auth = AuthBackend()
    await serve(page, site, auth=auth)
    requests = len(auth.requests)
    await page.click("#signIn")
    if closer == "button":
        await page.click("#signInCancel")
    else:
        await page.keyboard.press("Escape")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "signIn"
    await page.wait_for_timeout(300)
    assert page.url == f"{ORIGIN}/index.html"
    assert len(auth.requests) == requests
    assert await page.locator("#signIn").is_visible()
    assert await page.locator("#accountUser").is_hidden()
    assert STORAGE_KEY not in await stored(page)


async def test_fr1_ac7_continue_with_google_is_what_starts_the_sign_in(page: Page, site: Path) -> None:
    auth = AuthBackend()
    await serve(page, site, auth=auth)
    await page.click("#signIn")
    assert await page.get_by_role("button", name="Continue with Google").is_visible()
    await page.click("#signInContinue")
    await page.wait_for_url("**/auth/v1/authorize**")
    assert parse_qs(urlparse(page.url).query)["provider"] == ["google"]


async def test_fr1_ac7_the_notice_works_by_keyboard_and_traps_the_focus(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.focus("#signIn")
    await page.keyboard.press("Enter")
    await expect(page.locator(DIALOG)).to_be_visible()
    for _ in range(10):
        await page.keyboard.press("Tab")
        assert await page.evaluate(
            "document.activeElement === document.body || document.activeElement.closest('#signInDialog') !== null"
        )


async def test_fr1_ac7_the_signed_in_header_is_one_profile_button_and_a_menu(page: Page, site: Path) -> None:
    await sign_in(page, site)
    await expect(page.locator("#signIn")).to_be_hidden()
    assert await page.locator("#signInDialog").is_hidden()
    button = page.locator("#accountMenuButton")
    await expect(button).to_be_visible()
    assert await button.get_attribute("aria-label") == "Account"
    assert await button.get_attribute("aria-expanded") == "false"
    assert await button.get_attribute("aria-haspopup") is None  # a disclosure, not an ARIA menu
    assert await button.inner_text() == ""  # an icon, no words
    assert await page.evaluate("document.querySelector('#accountMenuButton svg').getAttribute('aria-hidden')") == "true"
    assert await page.locator("header img").count() == 0  # never a photo
    for control in ("#accountEmail", "#signOut", "#deleteAccount"):
        await expect(page.locator(control)).to_be_hidden()  # in the closed menu, not in the header
    await button.click()
    assert await button.get_attribute("aria-expanded") == "true"
    assert await page.locator("#accountEmail").text_content() == EMAIL
    assert await page.locator("#accountMenu .account-item").all_inner_texts() == ["Sign out", "Delete my account"]
    await page.click("#signOut")
    await expect(page.locator("#signIn")).to_be_visible()
    assert await page.locator("#signInDialog").is_hidden()
    assert await page.evaluate("document.activeElement.id") == "signIn"  # the focus does not fall to the page


async def test_the_menu_closes_on_escape_an_outside_click_and_a_choice(page: Page, site: Path) -> None:
    await sign_in(page, site)
    button = page.locator("#accountMenuButton")
    menu = page.locator("#accountMenu")
    await button.click()
    await expect(menu).to_be_visible()
    await button.click()  # the button toggles
    await expect(menu).to_be_hidden()
    await button.click()
    await page.keyboard.press("Escape")
    await expect(menu).to_be_hidden()
    assert await button.get_attribute("aria-expanded") == "false"
    assert await page.evaluate("document.activeElement.id") == "accountMenuButton"
    await button.click()
    await page.click("#heroTop")  # a click elsewhere
    await expect(menu).to_be_hidden()
    assert await button.get_attribute("aria-expanded") == "false"
    await button.click()
    await page.focus("#theme")  # the Tab key moving on
    await expect(menu).to_be_hidden()
    assert await button.get_attribute("aria-expanded") == "false"
    await button.click()
    await page.focus("#signOut")
    await page.keyboard.press("Escape")  # Escape from an item
    await expect(menu).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "accountMenuButton"
    await button.click()
    await page.click("#deleteAccount")  # a choice closes it and the focus goes to the button
    await expect(menu).to_be_hidden()
    await expect(page.locator("#deleteDialog")).to_be_visible()
    await page.keyboard.press("Escape")
    assert await page.evaluate("document.activeElement.id") == "accountMenuButton"


async def test_a_token_refresh_does_not_close_the_open_menu(page: Page, site: Path) -> None:
    await sign_in(page, site)
    await page.click("#accountMenuButton")
    await page.focus("#signOut")
    await page.evaluate("globalThis.misraAccount.client.auth.refreshSession()")  # an auth event with the same session
    await expect(page.locator("#accountMenu")).to_be_visible()


async def test_sign_out_works_from_the_keyboard_and_the_focus_lands_on_sign_in(page: Page, site: Path) -> None:
    await sign_in(page, site)
    await account_action_by_keyboard(page, "#signOut")
    await expect(page.locator("#signIn")).to_be_visible()
    assert await page.evaluate("document.activeElement.id") == "signIn"


async def test_the_menu_opens_from_the_keyboard_and_tab_walks_through_it(page: Page, site: Path) -> None:
    await sign_in(page, site)
    await page.focus("#accountMenuButton")
    await page.keyboard.press("Enter")
    await expect(page.locator("#accountMenu")).to_be_visible()
    assert await page.evaluate("document.activeElement.id") == "accountMenuButton"  # the focus stays on the button
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "signOut"
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "deleteAccount"
    await page.keyboard.press("Tab")  # on to the theme button: the menu closes
    await expect(page.locator("#accountMenu")).to_be_hidden()
    await page.focus("#accountMenuButton")
    await page.keyboard.press("Space")
    await expect(page.locator("#accountMenu")).to_be_visible()


async def test_fr1_ac7_without_the_sign_in_script_there_is_no_button(page: Page, site: Path) -> None:
    (site / "supabase.js").unlink()
    await serve(page, site)
    await expect(page.locator("#signIn")).to_be_hidden()


async def test_fr1_ac7_the_notice_reads_well_on_a_phone_in_both_themes(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    for scheme in ("light", "dark"):
        await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
        await serve(page, site)
        await page.click("#signIn")
        assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        box = await page.locator(DIALOG).bounding_box()
        assert box is not None
        assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]
        for selector in ("#signInText a", "#signInCancel", "#signInContinue"):  # the consent links are 44px targets too
            for target in await page.locator(selector).all():
                size = await target.bounding_box()
                assert size is not None
                assert size["height"] >= TAP_MIN
                assert size["width"] >= TAP_MIN
        await page.evaluate(AXE.read_text(encoding="utf-8"))
        assert await page.evaluate("axe.run().then(r => r.violations.map(v => v.id))") == []


# ---- The footer: links to the privacy notice, the terms and the contact ----


async def test_the_footer_links_to_the_privacy_notice_the_terms_and_the_contact(page: Page, site: Path) -> None:
    await serve(page, site)
    nav = page.get_by_role("navigation", name="Site pages")
    links = nav.locator("a")
    assert await links.count() == FOOTER_LINKS
    privacy = nav.get_by_role("link", name="Privacy")
    assert await privacy.get_attribute("href") == "privacy.html"
    assert await nav.get_by_role("link", name="Terms").get_attribute("href") == "terms.html"
    assert await nav.get_by_role("link", name="Connect your assistant").get_attribute("href") == "assistant.html"
    assert await nav.get_by_role("link", name="Contact").get_attribute("href") == "mailto:raanan@avidor.org"
    assert await nav.get_by_role("link", name="About").get_attribute("href") == "about.html"
    assert await nav.get_by_role("link", name="Security").get_attribute("href") == "security.html"
    for link in await links.all():
        box = await link.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert box["width"] >= TAP_MIN
    assert "Career pages last read" in await page.locator("footer.foot").inner_text()  # the date line is still there


async def test_the_footer_links_read_well_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    await serve(page, site)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for link in await page.locator("footer.foot a").all():
        box = await link.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]


# ---- The header: small, phone width, light and dark, accessible ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("signed_in", [False, True])
async def test_the_header_has_no_axe_violations(page: Page, site: Path, scheme: str, signed_in: bool) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    if signed_in:
        await sign_in(page, site)
    else:
        await serve(page, site)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []


@pytest.mark.parametrize("signed_in", [False, True])
async def test_the_header_reads_well_on_a_phone(page: Page, site: Path, signed_in: bool) -> None:
    await page.set_viewport_size(PHONE)
    if signed_in:
        await sign_in(page, site)
        await page.evaluate(
            "document.getElementById('accountEmail').textContent = 'a.very.long.address.indeed@subdomain.example.org'"
        )
        await page.click("#accountMenuButton")
    else:
        await serve(page, site)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("header .btn").and_(page.locator(":visible")).all():
        box = await target.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert box["width"] >= TAP_MIN
        assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]
    if signed_in:
        email = await page.locator("#accountEmail").bounding_box()
        menu = await page.locator("#accountMenu").bounding_box()
        assert email is not None
        assert menu is not None
        assert 0 <= menu["x"] <= menu["x"] + menu["width"] <= PHONE["width"]  # inside the screen
        assert (
            menu["x"] <= email["x"] and email["x"] + email["width"] <= menu["x"] + menu["width"]
        )  # and the email in it
        assert await page.locator("#accountEmail").evaluate(
            "e => e.scrollWidth <= e.clientWidth"
        )  # wrapped, never cut: the whole address is readable


@pytest.mark.parametrize("width", [320, 393])
async def test_the_open_menu_does_not_overflow_the_screen(page: Page, site: Path, width: int) -> None:
    await page.set_viewport_size({"width": width, "height": 800})
    await sign_in(page, site)
    await page.evaluate("document.getElementById('accountEmail').textContent = 'a'.repeat(60) + '@example.org'")
    await page.click("#accountMenuButton")
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    menu = await page.locator("#accountMenu").bounding_box()
    profile = await page.locator("#accountMenuButton").bounding_box()
    theme = await page.locator("#theme").bounding_box()
    assert menu is not None
    assert profile is not None
    assert theme is not None
    assert 0 <= menu["x"] <= menu["x"] + menu["width"] <= width
    assert profile["width"] == theme["width"] == TAP
    assert profile["height"] == theme["height"] == TAP
    assert theme["x"] - (profile["x"] + profile["width"]) >= HEADER_GAP  # a gap between the two header buttons
    for item in await page.locator("#accountMenu .account-item").all():
        box = await item.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_open_menu_has_no_axe_violations(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await sign_in(page, site)
    await page.click("#accountMenuButton")
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []


async def test_a_session_that_cannot_be_written_is_read_back_from_memory_not_from_the_stale_copy(
    page: Page, site: Path
) -> None:
    """When the storage quota is full the refreshed session lives in memory, and that copy wins over the old one."""
    await sign_in(page, site)
    session = json.loads((await stored(page))[STORAGE_KEY])
    session["expires_at"] = 1  # expired: the next load renews it
    await page.evaluate("([key, value]) => localStorage.setItem(key, value)", [STORAGE_KEY, json.dumps(session)])
    await page.add_init_script(
        "const set = Storage.prototype.setItem;"
        "Storage.prototype.setItem = function (key, value) {"
        " if (key === 'misra-auth') throw new DOMException('full', 'QuotaExceededError');"
        " return set.call(this, key, value); };"
    )
    await page.reload()
    await page.wait_for_selector("#accountUser:not([hidden])")
    assert await page.locator("#accountEmail").text_content() == EMAIL
    assert json.loads((await stored(page))[STORAGE_KEY])["expires_at"] == 1  # the write failed: the old copy remains
    await account_action(page, "#signOut")  # and the session in memory is the one that signs out and clears both
    await page.wait_for_selector("#signIn")
    assert STORAGE_KEY not in await stored(page)
