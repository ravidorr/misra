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
    USER_ID,
    AuthBackend,
    serve,
    site,  # noqa: F401  (a fixture)
)

AXE_RUN = "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
STORAGE_KEY = "misra-auth"
RETRIES = 50
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
    """Click "Sign in" and return the address the browser is sent to (the stand-in for Google's page)."""
    await page.click("#signIn")
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
    assert await page.locator("#accountEmail").inner_text() == EMAIL
    assert await page.locator("#signIn").is_hidden()
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
    assert await page.locator("#accountEmail").inner_text() == EMAIL
    assert len(auth.requests) == requests  # the saved session is enough: no new request


async def test_fr1_ac2_a_user_can_sign_out(page: Page, site: Path) -> None:
    auth = await sign_in(page, site)
    await page.click("#signOut")
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
    assert await page.locator("form").count() == 0
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
    await expect(page.locator("#authMessage")).not_to_have_text("")
    assert (await page.locator("#authMessage").inner_text()).startswith(message)
    assert len(problems) == 1  # logged once


async def test_a_sign_out_the_service_cannot_finish_still_signs_out_here_and_says_so(page: Page, site: Path) -> None:
    auth = AuthBackend("logout_fails")
    await sign_in(page, site, auth)
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    await page.click("#signOut")
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
    else:
        await serve(page, site)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("header .btn").and_(page.locator(":visible")).all():
        box = await target.bounding_box()
        assert box is not None
        assert box["height"] >= TAP
        assert box["width"] >= TAP
        assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]
    if signed_in:
        email = await page.locator("#accountEmail").bounding_box()
        assert email is not None
        assert email["x"] >= 0
        assert email["x"] + email["width"] <= PHONE["width"]
        assert await page.locator("#accountEmail").evaluate(
            "e => e.scrollWidth > e.clientWidth"
        )  # cut, with an ellipsis


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
    assert await page.locator("#accountEmail").inner_text() == EMAIL
    assert json.loads((await stored(page))[STORAGE_KEY])["expires_at"] == 1  # the write failed: the old copy remains
    await page.click("#signOut")  # and the session in memory is the one that signs out and clears both
    await page.wait_for_selector("#signIn")
    assert STORAGE_KEY not in await stored(page)
