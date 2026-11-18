"""A fake of the two tables the Companies area of the admin page reads and writes (`companies` and `jobs`), at the
network layer, for the browser tests. It answers as PostgREST does for the calls supabase-js makes, and it refuses a
write the way the database's checks of supabase/migrations/20261130100000_companies.sql would, so a page that sent a
record the database would refuse fails here. It holds invented companies only (tests/fixtures/admin_companies.json)."""

import asyncio
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Route

import adapters
import build_dashboard
from tests.test_dashboard_page import CORS

REST_CORS = {**CORS, "access-control-allow-methods": "GET, POST, PATCH, DELETE, HEAD, OPTIONS"}
SECRET = "SECRET-ROW-VALUE"  # what a real error's `details` quotes: the page must never show it
EXCLUDED_MAX = 200
NAME_MIN = 2
HTTP_OK, HTTP_UNAUTHORIZED = 200, 401


def violated(record: dict[str, Any], others: list[dict[str, Any]], excluded_count: int) -> tuple[int, str, str] | None:  # noqa: PLR0911, PLR0912
    """(HTTP status, SQLSTATE, constraint) of the first rule the record breaks, as the migration states them."""
    name = record["name"]
    if re.search(r"^\s|\s$", name) or len(name) < NAME_MIN:
        return 400, "23514", "companies_name_min_length"
    excluded, gap, links = record.get("excluded"), record.get("gap"), record.get("links")
    if excluded is None and not (record.get("domain") or "").strip():
        return 400, "23514", "companies_domain_required"
    if int(bool(record["crawl"])) + (gap is not None) + (excluded is not None) != 1:
        return 400, "23514", "companies_exactly_one_status"
    if gap is not None and (set(gap) - {"reason", "note"} or gap.get("reason") not in build_dashboard.REASONS):
        return 400, "23514", "companies_gap_reason"
    if record.get("ats") not in (None, "", *adapters.ADAPTERS, "browser"):
        return 400, "23514", "companies_ats_known"
    careers = record.get("careers_url")
    if careers and not careers.startswith("https://"):
        return 400, "23514", "companies_careers_https"
    if links is not None and any(not v.startswith("https://") for v in links.values()):
        return 400, "23514", "companies_links_https"
    if excluded is not None:
        if set(excluded) != {"reason", "date_added"}:
            return 400, "23514", "companies_excluded_keys"
        if not str(excluded["reason"]).strip():
            return 400, "23514", "companies_excluded_reason"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(excluded["date_added"])):
            return 400, "23514", "companies_excluded_date"
        blanks = (
            "domain",
            "segment",
            "careers_url",
            "ats",
            "slug",
            "source",
            "status",
            "israel_only",
            "verify",
            "links",
        )
        if record["crawl"] or gap is not None or any(record.get(k) is not None for k in blanks):
            return 400, "23514", "companies_excluded_shape"
        if excluded_count >= EXCLUDED_MAX:
            return 400, "23514", "companies_excluded_limit"
    if any(o["name"].lower() == name.lower() for o in others):
        return 409, "23505", "companies_name_unique"
    domain = (record.get("domain") or "").lower()
    if domain and any((o.get("domain") or "").lower() == domain for o in others):
        return 409, "23505", "companies_domain_unique"
    return None


def refusal(status: int, code: str, constraint: str) -> tuple[int, str]:
    verb = "violates check constraint" if code == "23514" else "violates unique constraint"
    message = (
        f'duplicate key value {verb} "{constraint}"'
        if code == "23505"
        else f'new row for relation "companies" {verb} "{constraint}"'
    )
    return status, json.dumps({"code": code, "message": message, "details": f"Failing row contains ({SECRET})."})


def refused(rule: tuple[int, str, str]) -> tuple[int, Any]:
    status, text = refusal(*rule)
    return status, json.loads(text)


class FakeTables:
    def __init__(self, companies: list[dict[str, Any]], jobs: list[tuple[str, str]]) -> None:
        self.companies = [dict(row) for row in companies]
        self.jobs = list(jobs)  # (company, status)
        self.next_id = max((int(row["id"]) for row in self.companies), default=0) + 1
        self.writes: list[tuple[str, dict[str, Any] | None, dict[str, list[str]]]] = []  # method, body, query
        self.reads: list[str] = []  # "companies", "jobs-count", "jobs-rows"
        self.fail: tuple[int, str] | None = None  # the next write answers with this
        self.jobs_status = 200
        self.list_status = 200  # the answer to a read of `companies`
        self.write_gate: asyncio.Event | None = None
        self.jobs_gate: asyncio.Event | None = None
        self.clock = 0

    async def handle(self, route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/jobs"):
            await self.jobs_answer(route, request.method, query)
            return
        if request.method == "GET":
            self.reads.append("companies")
            if self.list_status != HTTP_OK:
                text = '{"message":"JWT expired"}' if self.list_status == HTTP_UNAUTHORIZED else "unavailable"
                await route.fulfill(
                    status=self.list_status, headers=REST_CORS, body=text, content_type="application/json"
                )
                return
            rows = sorted(self.companies, key=lambda r: r["name"])
            await route.fulfill(headers=REST_CORS, body=json.dumps(rows), content_type="application/json")
            return
        body = json.loads(request.post_data) if request.post_data else None
        self.writes.append((request.method, body, query))
        if self.write_gate is not None:
            await self.write_gate.wait()
        if self.fail:
            status, text = self.fail
            self.fail = None
            await route.fulfill(status=status, headers=REST_CORS, body=text, content_type="application/json")
            return
        status, rows = self.apply(request.method, body, query)
        await route.fulfill(status=status, headers=REST_CORS, body=json.dumps(rows), content_type="application/json")

    def stamp(self) -> str:
        self.clock += 1
        return f"2026-11-18T09:{self.clock:02d}:00+00:00"

    def matching(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        wanted = {key: value[0].removeprefix("eq.") for key, value in query.items() if value[0].startswith("eq.")}
        return [row for row in self.companies if all(str(row.get(key)) == value for key, value in wanted.items())]

    def apply(self, method: str, body: dict[str, Any] | None, query: dict[str, list[str]]) -> tuple[int, Any]:
        if method == "DELETE":
            rows = self.matching(query)
            self.companies = [row for row in self.companies if row not in rows]
            return 200, [{"id": row["id"]} for row in rows]
        assert body is not None
        if method == "POST":
            record = dict.fromkeys((*FIELDS,)) | body
            rule = violated(record, self.companies, self.excluded_count())
            if rule:
                return refused(rule)
            record |= {"id": self.next_id, "updated_at": self.stamp()}
            self.next_id += 1
            self.companies.append(record)
            return 201, [record]
        rows = self.matching(query)  # PATCH
        if not rows:
            return 200, []
        row = rows[0]
        merged = row | body
        others = [r for r in self.companies if r is not row]
        rule = violated(merged, others, self.excluded_count(others))
        if rule:
            return refused(rule)
        row.update(body, updated_at=self.stamp())
        return 200, [row]

    def excluded_count(self, rows: list[dict[str, Any]] | None = None) -> int:
        return sum(1 for r in (self.companies if rows is None else rows) if r.get("excluded") is not None)

    async def jobs_answer(self, route: Route, method: str, query: dict[str, list[str]]) -> None:
        if self.jobs_gate is not None:
            await self.jobs_gate.wait()
        if self.jobs_status != HTTP_OK:
            await route.fulfill(status=self.jobs_status, headers=REST_CORS, body="unavailable")
            return
        if method == "HEAD":
            self.reads.append("jobs-count")
            company = query["company"][0].removeprefix("eq.")
            only = query["status"][0].removeprefix("eq.") if "status" in query else None
            n = sum(1 for c, s in self.jobs if c == company and only in (None, s))
            await route.fulfill(headers={**REST_CORS, "content-range": f"*/{n}"}, body="")
            return
        self.reads.append("jobs-rows")
        listed = (
            query["company"][0].removeprefix("in.(").removesuffix(")")
        )  # a name with `,()` is quoted, as PostgREST asks
        names = {quoted or plain for quoted, plain in re.findall(r'"((?:[^"\\]|\\.)*)"|([^,]+)', listed)}
        rows = [{"company": c} for c, _s in self.jobs if c in names]
        await route.fulfill(headers=REST_CORS, body=json.dumps(rows), content_type="application/json")


FIELDS = (
    "name", "domain", "segment", "crawl", "careers_url", "ats", "slug", "source", "status", "israel_only", "verify",
    "links", "gap", "excluded",
)  # fmt: skip
