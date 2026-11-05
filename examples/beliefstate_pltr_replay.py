"""Show the PLTR review with a saved research fixture and the real client."""

import sys
from unittest.mock import patch

from hedge_fund import research

CUTOFF = "2026-11-03T21:59:36.678Z"
ISSUER = "https://www.sec.gov/Archives/edgar/data/1321655/000132165526000045/a2026q3ex991pressrelease.htm"
SOURCE = "https://nikhs.substack.com/p/palantir-3q26-the-contract-is-not"
BRIEF = {
    "schema_version": "1",
    "resource": "brief",
    "view": "current",
    "scope": {"ticker": "PLTR"},
    "as_of": CUTOFF,
    "coverage": {"completeness": "bounded"},
    "warnings": [],
    "quality_gaps": [],
    "beliefs": [{
        "person": {"name": "Nikhs"},
        "thesis": "How reliably will shorter U.S. commercial contracts expand into durable spending?",
        "catalysts": [
            {"statement": "At the February report, look for U.S. commercial remaining deal value above $7B."},
            {"statement": "Check whether bookings exceed 1.6× quarterly U.S. commercial revenue."},
            {"statement": "Look for the return of duration-adjusted bookings disclosure."},
        ],
        "evidence": [{
            "source_url": SOURCE,
            "published_at": "2026-11-03T08:42:48.000Z",
            "first_observed_at": "2026-11-03T18:19:01.042Z",
        }],
    }],
}


def fixture_read(path, params, *, api_key="", request_id=""):
    if path == "/v1/tickers" and params == {"as_of": CUTOFF} and not api_key:
        return {"resource": "beliefs", "as_of": CUTOFF, "tickers": ["PLTR"]}
    brief_query = {"ticker": "PLTR", "detail": "current", "max_results": "3", "as_of": CUTOFF}
    if path == "/v1/brief" and params == brief_query and api_key == "fixture" and request_id:
        return BRIEF
    raise RuntimeError("Unexpected request in the offline replay")


def main():
    if sys.version_info < (3, 11):
        raise SystemExit("The PLTR replay requires Python 3.11 or later.")

    with patch.object(research, "_read", side_effect=fixture_read):
        result = research.read_research("PLTR", api_key="fixture", as_of=CUTOFF)
    case = result["brief"]["beliefs"][0]

    print("PLTR illustrative draft review · offline fixture")
    print(f"Saved research cutoff: {result['brief']['as_of']}")
    print("\nBefore · issuer disclosures")
    print("U.S. commercial revenue grew 29% quarter over quarter. Remaining deal value grew 4%.")
    print("Will shorter initial deals expand? Look for comparable duration-adjusted bookings.")
    print(ISSUER)
    print("\nAfter · issuer disclosures plus cited investor research")
    print(f"{case['person']['name']}: {case['thesis']}")
    for item in case["catalysts"]:
        print(f"- {item['statement']}")
    print(case["evidence"][0]["source_url"])


if __name__ == "__main__":
    main()
