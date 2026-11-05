import json
import sys
import traceback
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from hedge_fund import research, run

CUTOFF = "2025-10-30T20:00:00.000Z"
COVERAGE = {"resource": "beliefs", "as_of": CUTOFF, "tickers": ["NBIS"]}
BRIEF = {
    "schema_version": "1",
    "resource": "brief",
    "view": "current",
    "scope": {"ticker": "NBIS"},
    "as_of": CUTOFF,
    "generated_at": "2025-11-01T11:00:00Z",
    "beliefs": [{
        "belief_id": "00000000-0000-4000-8000-000000000001",
        "person": {"name": "Example Investor"},
        "thesis": "Illustrative expansion thesis.",
        "evidence": [{
            "source_url": "https://example.com/synthetic-case",
            "source_title": "Synthetic source",
            "published_at": "2025-10-29T12:00:00Z",
            "first_observed_at": "2025-10-30T13:00:00Z",
        }],
        "risks": [{"statement": "Illustrative execution risk", "status": "unknown"}],
        "catalysts": [{"statement": "Illustrative release", "status": "pending"}],
        "unknowns": [{"field": "confidence", "reason": "Source did not state it."}],
    }],
    "coverage": {"completeness": "bounded", "omitted_belief_count": 1},
    "warnings": ["Scope is limited."],
    "quality_gaps": [],
    "next_cursor": "synthetic-cursor",
}


def stub_http(monkeypatch, *responses):
    connections = []
    for status, payload in responses:
        connection = Mock()
        connection.getresponse.return_value.status = status
        connection.getresponse.return_value.read.return_value = json.dumps(payload).encode()
        connections.append(connection)
    factory = Mock(side_effect=connections)
    monkeypatch.setattr(research, "HTTPSConnection", factory)
    return factory, connections


def test_cli_returns_cited_context_and_does_not_start_a_fund(monkeypatch, capsys):
    factory, (free, paid) = stub_http(monkeypatch, (200, COVERAGE), (200, BRIEF))
    monkeypatch.setattr(sys, "argv", ["aihf", "research", "nbis", "--as-of", "2025-10-30T16:00:00-04:00"])
    monkeypatch.setenv(research.API_KEY_ENV, "fixture-key")
    monkeypatch.setattr(run, "apply_credentials", lambda: None)
    for name in ("ensure_mandates_dir", "Fund", "FDClient", "deploy", "tick", "backtest_fund"):
        monkeypatch.setattr(run, name, Mock(side_effect=AssertionError(f"unexpected {name}")))
    run.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "available"
    assert result["brief"] == BRIEF
    assert UUID(result["request_id"]).version == 4
    assert factory.call_count == 2
    assert all(call.args == ("beliefstate.ai",) for call in factory.call_args_list)
    for connection, path, expected in (
        (free, "/v1/tickers", {"as_of": [CUTOFF]}),
        (paid, "/v1/brief", {"ticker": ["NBIS"], "detail": ["current"], "max_results": ["3"], "as_of": [CUTOFF]}),
    ):
        connection.request.assert_called_once()
        method, url = connection.request.call_args.args
        assert method == "GET"
        assert urlsplit(url).path == path
        assert parse_qs(urlsplit(url).query) == expected
        connection.close.assert_called_once()
    assert free.request.call_args.kwargs["headers"] == {"Accept": "application/json"}
    assert paid.request.call_args.kwargs["headers"]["Authorization"] == "Bearer fixture-key"
    assert paid.request.call_args.kwargs["headers"]["X-Request-ID"] == result["request_id"]
    assert "fixture-key" not in json.dumps(result)


def test_uncovered_ticker_uses_only_free_coverage(monkeypatch):
    factory, _ = stub_http(monkeypatch, (200, COVERAGE))
    assert research.read_research("ZZZZ", api_key="fixture-key") == {
        "ticker": "ZZZZ", "status": "no_released_coverage", "as_of": CUTOFF,
    }
    factory.assert_called_once()


def test_no_fit_retains_service_warnings(monkeypatch):
    no_fit = {**BRIEF, "beliefs": [], "coverage": {"completeness": "no_fit"}}
    stub_http(monkeypatch, (200, COVERAGE), (200, no_fit))
    result = research.read_research("NBIS", api_key="fixture-key")
    assert result["status"] == "no_matching_beliefs"
    assert result["brief"] == no_fit


@pytest.mark.parametrize("status", [302, 402, 503])
def test_metered_errors_keep_request_id_and_never_retry(monkeypatch, status):
    factory, (_, paid) = stub_http(monkeypatch, (200, COVERAGE), (status, {"secret": "fixture-key"}))
    with pytest.raises(research.ResearchError, match=f"HTTP {status}") as failure:
        research.read_research("NBIS", api_key="fixture-key")
    assert paid.request.call_args.kwargs["headers"]["X-Request-ID"] in str(failure.value)
    assert "fixture-key" not in str(failure.value)
    assert factory.call_count == 2
    paid.request.assert_called_once()


@pytest.mark.parametrize("brief", [
    {**BRIEF, "scope": {"ticker": "OTHER"}},
    {**BRIEF, "as_of": "2025-10-31T20:00:00.000Z"},
    {**BRIEF, "beliefs": [{"thesis": "Uncited", "evidence": [{}]}]},
])
def test_rejects_mismatched_or_uncited_briefs(monkeypatch, brief):
    factory, _ = stub_http(monkeypatch, (200, COVERAGE), (200, brief))
    with pytest.raises(research.ResearchError, match="Request ID"):
        research.read_research("NBIS", api_key="fixture-key", as_of=CUTOFF)
    assert factory.call_count == 2


def test_invalid_cutoff_or_key_never_sends_a_request(monkeypatch):
    factory, _ = stub_http(monkeypatch)
    for cutoff in ("2025-10-30", "2025-10-30T20:00:00+00:60", "0001-01-01T00:00:00+00:01"):
        with pytest.raises(research.ResearchError, match="RFC 3339"):
            research.read_research("NBIS", api_key="fixture-key", as_of=cutoff)
    with pytest.raises(research.ResearchError, match="BELIEFSTATE_API_KEY"):
        research.read_research("NBIS", api_key="")
    factory.assert_not_called()


def test_sub_millisecond_cutoff_is_rejected_before_request(monkeypatch):
    factory, _ = stub_http(monkeypatch)
    with pytest.raises(research.ResearchError, match="millisecond precision"):
        research.read_research("NBIS", api_key="fixture-key", as_of="2025-10-30T20:00:00.123456Z")
    factory.assert_not_called()


def test_transport_traceback_does_not_expose_a_rejected_key(monkeypatch):
    factory, (_, paid) = stub_http(monkeypatch, (200, COVERAGE), (200, BRIEF))
    secret = "fixture-key"
    paid.request.side_effect = ValueError(f"Invalid header value containing {secret}")
    with pytest.raises(research.ResearchError, match="Request ID") as failure:
        research.read_research("NBIS", api_key=secret)
    assert secret not in "".join(traceback.format_exception(failure.value))
    assert factory.call_count == 2
    paid.request.assert_called_once()
