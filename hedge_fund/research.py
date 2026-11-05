"""Read BeliefState research for a bot without running the fund engine."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlencode
from uuid import uuid4

HOST = "beliefstate.ai"
API_KEY_ENV = "BELIEFSTATE_API_KEY"


class ResearchError(Exception):
    """A research request failed or returned an unexpected response."""


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value
    ):
        raise ValueError("expected an RFC 3339 timestamp with a timezone")
    return datetime.fromisoformat(value)


def _read(path: str, params: dict[str, str], *, api_key: str = "", request_id: str = "") -> dict:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-Request-ID"] = request_id
    target = f"{path}?{urlencode(params)}" if params else path
    connection = HTTPSConnection(HOST, timeout=15)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        if response.status == 402:
            raise ResearchError("BeliefState returned HTTP 402. Check the workspace's research allowance or credit.")
        if response.status != 200:
            raise ResearchError(f"BeliefState returned HTTP {response.status} for {path}.")
        # The current brief is bounded by the server. The larger free ticker
        # directory has its own ceiling so an unexpected payload cannot exhaust memory.
        limit = 65_536 if api_key else 1_048_576
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ResearchError(f"BeliefState response exceeded the size limit for {path}.")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload
    except (OSError, HTTPException, ValueError, RecursionError):
        raise ResearchError(f"Could not read the BeliefState response for {path}.") from None
    finally:
        connection.close()


def read_research(ticker: str, *, api_key: str, as_of: str | None = None) -> dict:
    """Return one bounded, cited company brief after a free coverage check."""
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", ticker):
        raise ResearchError("Supply a single company ticker, such as NBIS.")
    api_key = api_key.strip()
    if not api_key:
        raise ResearchError(f"Set {API_KEY_ENV} to a scoped BeliefState key.")
    cutoff = None
    if as_of is not None:
        try:
            cutoff = _timestamp(as_of).astimezone(timezone.utc)
            if cutoff > datetime.now(timezone.utc):
                raise ValueError("future cutoff")
        except (ValueError, OverflowError) as exc:
            raise ResearchError("--as-of must be a past RFC 3339 timestamp, such as 2026-10-30T20:00:00Z.") from exc
        if cutoff.microsecond % 1000:
            raise ResearchError("--as-of supports millisecond precision.")
    window = {}
    if cutoff:
        window["as_of"] = cutoff.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    coverage = _read("/v1/tickers", window)
    try:
        coverage_at = _timestamp(coverage.get("as_of"))
    except ValueError as exc:
        raise ResearchError("BeliefState coverage did not include a valid cutoff.") from exc
    tickers = coverage.get("tickers")
    if coverage.get("resource") != "beliefs" or not isinstance(tickers, list) or not all(isinstance(item, str) for item in tickers):
        raise ResearchError("BeliefState did not return a valid coverage list.")
    if cutoff and coverage_at != cutoff:
        raise ResearchError("BeliefState coverage did not match the requested cutoff.")
    if ticker not in tickers:
        return {"ticker": ticker, "status": "no_released_coverage", "as_of": coverage["as_of"]}

    request_id = str(uuid4())
    try:
        brief = _read(
            "/v1/brief", {"ticker": ticker, "detail": "current", "max_results": "3", **window},
            api_key=api_key, request_id=request_id,
        )
        try:
            brief_at = _timestamp(brief.get("as_of"))
        except ValueError as exc:
            raise ResearchError("BeliefState brief did not include a valid cutoff.") from exc
        scope = brief.get("scope")
        beliefs = brief.get("beliefs")
        brief_coverage = brief.get("coverage")
        if (
            brief.get("schema_version") != "1"
            or brief.get("resource") != "brief"
            or brief.get("view") != "current"
            or not isinstance(scope, dict)
            or scope.get("ticker") != ticker
            or not isinstance(beliefs, list)
            or len(beliefs) > 3
            or any(
                not isinstance(case, dict)
                or not isinstance(case.get("thesis"), str)
                or not case["thesis"].strip()
                or not isinstance(case.get("evidence"), list)
                or not any(
                    isinstance(source, dict)
                    and isinstance(source.get("source_url"), str)
                    and source["source_url"].startswith(("https://", "http://"))
                    for source in case["evidence"]
                )
                for case in beliefs
            )
            or not isinstance(brief_coverage, dict)
            or brief_coverage.get("completeness") not in ("complete_within_scope", "bounded", "no_fit")
            or not isinstance(brief.get("warnings"), list)
            or not isinstance(brief.get("quality_gaps"), list)
        ):
            raise ResearchError("BeliefState did not return a current brief for the requested ticker.")
        if cutoff and brief_at != cutoff:
            raise ResearchError("BeliefState brief did not match the requested cutoff.")
    except ResearchError as exc:
        raise ResearchError(f"{exc} Request ID {request_id}. Check account usage before sending a new request.") from exc

    status = "available" if beliefs and brief_coverage["completeness"] != "no_fit" else "no_matching_beliefs"
    return {"ticker": ticker, "status": status, "request_id": request_id, "brief": brief}
