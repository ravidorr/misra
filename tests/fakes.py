"""Hand-rolled fakes for requests responses and Playwright objects. No network, no browser."""

import asyncio
import json
from collections.abc import Callable
from typing import Any


class FakeHttpResponse:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        data: Any = None,
        json_error: bool = False,
        url: str = "https://final.test/",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self._data = data
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("bad json")
        return self._data


class FakeRequest:
    def __init__(self, resource_type: str = "xhr") -> None:
        self.resource_type = resource_type


class FakeResp:
    """Playwright response. Keywords: data, status, resource_type, content_type, text, error."""

    def __init__(self, url: str, **kw: Any) -> None:
        self.url = url
        self.status: int = kw.get("status", 200)
        self.request = FakeRequest(kw.get("resource_type", "xhr"))
        content_type = kw.get("content_type", "application/json")
        self.headers = {"content-type": content_type} if content_type is not None else {}
        self._data = kw.get("data")
        self._text: str = kw.get("text", "")
        self._error: bool = kw.get("error", False)

    async def json(self) -> Any:
        if self._error:
            raise ValueError("boom")
        return self._data

    async def text(self) -> str:
        if self._error:
            raise ValueError("boom")
        return self._text


class FakeEl:
    def __init__(
        self,
        attrs: dict[str, str] | None = None,
        disabled: bool = False,
        click_error: bool = False,
        visible: bool = True,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self.on_click = on_click
        self.attrs = attrs or {}
        self.disabled = disabled
        self.click_error = click_error
        self.visible = visible
        self.clicks = 0

    async def get_attribute(self, name: str) -> str | None:
        return self.attrs.get(name)

    async def is_disabled(self) -> bool:
        return self.disabled

    async def is_visible(self, timeout: int = 0) -> bool:
        return self.visible

    async def click(self, timeout: int = 0) -> None:
        if self.click_error:
            raise RuntimeError("click failed")
        self.clicks += 1

    async def dispatch_event(self, _name: str) -> None:
        self.clicks += 1
        if self.on_click:
            self.on_click()


class FakeLoc:
    def __init__(self, count: int = 0, first: FakeEl | None = None) -> None:
        self._count = count
        self.first = first or FakeEl()

    def locator(self, _arg: str) -> "FakeLoc":
        return self

    async def count(self) -> int:
        return self._count


class FakeRole:
    def __init__(self, first: FakeEl) -> None:
        self.first = first


class FakeMouse:
    async def wheel(self, _x: int, _y: int) -> None:
        return None


class FakeFrame:
    def __init__(self, url: str) -> None:
        self.url = url


class FakePage:
    """Scriptable Playwright page. link_fn(i) gives the i-th links evaluation, count_fn(i) the i-th a[href] count."""

    def __init__(self, **kw: Any) -> None:
        self.url = kw.get("url", "https://x.test/final")
        self.title_text: str = kw.get("title", "Careers")
        self.goto_result: Any = kw.get("goto_result")
        self.goto_error: Exception | None = kw.get("goto_error")
        self.goto_errors: list[Exception | Any] = kw.get("goto_seq", [])
        self.idle_error: bool = kw.get("idle_error", False)
        self.responses: list[Any] = kw.get("responses", [])
        self.requests: list[Any] = kw.get("requests", [])
        self.ld: list[str] = kw.get("ld", [])
        self.link_fn: Callable[[int], list[Any]] = kw.get("link_fn", lambda _i: [])
        self.count_fn: Callable[[int], int] = kw.get("count_fn", lambda _i: 5)
        self.btn: FakeEl = kw.get("btn", FakeEl(visible=False))
        self.locators: dict[str, FakeLoc] = kw.get("locators", {})
        self.frames: list[FakeFrame] = kw.get("frames", [])
        self.handlers: dict[str, list[Callable[..., Any]]] = {}
        self.mouse = FakeMouse()
        self.closed = False
        self.gotos: list[str] = []
        self._links_calls = 0
        self._count_calls = 0

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def goto(self, url: str, timeout: int = 0, wait_until: str = "") -> Any:
        self.gotos.append(url)
        if self.goto_errors:
            item = self.goto_errors.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.goto_error:
            raise self.goto_error
        for req in self.requests:
            for handler in self.handlers.get("request", []):
                handler(req)
        for resp in self.responses:
            for handler in self.handlers.get("response", []):
                handler(resp)
        return self.goto_result

    async def wait_for_load_state(self, _state: str, timeout: int = 0) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if self.idle_error:
            raise TimeoutError("idle")

    async def title(self) -> str:
        return self.title_text

    async def wait_for_timeout(self, _ms: int) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    def get_by_role(self, _role: str, name: Any = None) -> FakeRole:
        return FakeRole(self.btn)

    async def eval_on_selector_all(self, selector: str, js: str, arg: Any = None) -> Any:
        if "ld+json" in selector:
            return self.ld
        if js == "e=>e.length":
            self._count_calls += 1
            return self.count_fn(self._count_calls)
        self._links_calls += 1
        return self.link_fn(self._links_calls)

    def locator(self, key: str) -> FakeLoc:
        return self.locators.get(key, FakeLoc(0))

    async def close(self) -> None:
        self.closed = True


class FakeCtx:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def new_page(self) -> FakePage:
        return self.page


class FakeReq:
    def __init__(self, url: str) -> None:
        self.url = url


class Status:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePlaywright:
    """async_playwright() replacement: records launch kwargs; every context hands out the same page."""

    def __init__(self, calls: list[dict[str, Any]], page: FakePage | None = None) -> None:
        self.calls = calls
        self.chromium = self
        self.page = page

    async def launch(self, **kw: Any) -> "FakePlaywright":
        self.calls.append(kw)
        return self

    async def new_context(self, **_kw: Any) -> FakeCtx:
        return FakeCtx(self.page or FakePage())

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> "FakePlaywright":
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None


class FakeSupabase:
    """In-memory PostgREST `jobs` table behind the `request` method of a requests session. A POST upserts by
    (company, job_id) and merges the columns it carries, as `resolution=merge-duplicates` does; a GET with
    `status=eq.open` returns the (company, job_id) of open rows, `page` rows at most (Supabase caps a response)."""

    def __init__(self, page: int = 1000, fail_on: str | None = None, error: Exception | None = None) -> None:
        self.table: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.page = page
        self.fail_on = fail_on  # "GET" or "POST": answer that method with HTTP 500
        self.error = error  # raised by every request

    def request(self, method: str, url: str, **kw: Any) -> FakeHttpResponse:
        self.calls.append({"method": method, "url": url, **kw})
        if self.error:
            raise self.error
        if method == self.fail_on:
            return FakeHttpResponse(status_code=500, text="boom")
        if method == "POST":
            for row in kw["json"]:
                self.table.setdefault((row["company"], row["job_id"]), {}).update(row)
            return FakeHttpResponse(status_code=201)
        params = kw["params"]
        status = params["status"].removeprefix("eq.")
        keys = [key for key, row in self.table.items() if row.get("status") == status]
        if params.get("order") == "company.asc,job_id.asc":  # without an order, Postgres gives no stable pages
            keys.sort()
        else:
            keys.reverse()
        window = keys[params["offset"] : params["offset"] + min(params["limit"], self.page)]
        return FakeHttpResponse(data=[{"company": c, "job_id": j} for c, j in window])

    def snapshot(self) -> str:
        return json.dumps({"|".join(k): v for k, v in self.table.items()}, sort_keys=True)
