"""Standalone report controls, lazy inspection, and compact inert data."""

import json
from copy import deepcopy

from bs4 import BeautifulSoup

from companybench.report import html_report


def report_fixture():
    query = {
        "id": "Q1",
        "query": "Find AI manufacturers",
        "complexity": "L2",
        "industry": "Manufacturing",
        "signal_tags": ["hiring"],
        "conditions": [{"id": "fit", "description": "Manufactures AI-enabled equipment"}],
        "rule": {"op": "condition", "condition_id": "fit"},
        "acceptance": "Makes equipment, not a reseller",
        "company_unit": "Operating company",
    }
    judgment = {
        "verdict": "unknown",
        "conditions": [],
        "raw": {
            "request_body": "Large original API payload",
            "identity_judgment": {"verdict": "met"},
        },
    }
    row = {
        "query_id": "Q1",
        "provider": "example",
        "judge": "llm",
        "trial": 1,
        "facets": {"complexity": "L2", "industry": "Manufacturing", "signal_tags": ["hiring"]},
        "requested_count": 50,
        "returned_companies": 1,
        "unique_companies": 1,
        "valid_companies": 0,
        "undefined_reasons": {"cost": "Price or usage is unknown"},
        "slots": [
            {
                "position": 1,
                "name": "Company",
                "entity_id": "entity",
                "category": "unknown",
                "judgment": judgment,
            }
        ],
    }
    return {
        "synthetic": True,
        "comparison_complete": True,
        "reference_time": "2026-10-06T00:00:00Z",
        "cost_view": "public",
        "evidence_version": "revision",
        "selected_queries": [query],
        "rows": [row],
        "summaries": [],
        "limitations": [],
        "paired_comparisons": [],
        "judge_agreement": [],
        "evaluation_costs": [],
        "evidence": [
            {
                "query": query,
                "company": {"id": "entity", "notes": "Identity is unresolved"},
                "sources": [],
            }
        ],
    }


def test_report_exposes_accessible_pagination_and_intersecting_facet_controls():
    soup = BeautifulSoup(html_report(report_fixture()), "html.parser")
    assert soup.select_one('nav[aria-label="Task result pages"]')
    assert soup.select_one("#previous-page").get_text() == "Previous page"
    assert soup.select_one("#next-page").get_text() == "Next page"
    assert soup.select_one("#page-size option[selected]").get_text() == "20"
    assert soup.select_one("#result-count")["aria-live"] == "polite"
    assert soup.select_one("#facet-filters")
    assert soup.select_one("#configurations")
    assert "Configurations and calculation settings" in soup.get_text()
    assert "all selections intersect (AND)" in soup.get_text()
    assert (
        "Summary statistics and plots above describe the complete saved report" in soup.get_text()
    )
    assert not soup.select("script[src],link[rel=stylesheet]")


def test_inspection_is_paginated_and_company_evidence_is_lazy():
    soup = BeautifulSoup(html_report(report_fixture()), "html.parser")
    script = soup.find_all("script")[-1].string
    assert "rows.slice(start,start+size)" in script
    assert "visibleRows.map" in script
    assert "selected.every" in script  # Each selected facet must match the same query.
    assert "target.open" in script and "target.dataset.loaded" in script
    assert "taskContent(row)" in script and "companyContent(row,slot)" in script
    assert (
        "catalogue queries match" in script
        and "search tasks" in script
        and "task judgments" in script
    )
    assert "Exact query conditions and acceptance logic" in script
    assert "Result denominators and undefined metrics" in script
    assert "undefined_reasons:r.undefined_reasons" in script
    assert "returned_model:r.model,usage:r.usage,cost_details:r.cost_details" in script
    assert "calculation_settings:d.calculation_settings" in script
    assert "['http:','https:'].includes(u.protocol)" in script


def test_html_compaction_keeps_full_report_unchanged_and_preserves_typed_identity():
    report = report_fixture()
    original = deepcopy(report)
    soup = BeautifulSoup(html_report(report), "html.parser")
    display = json.loads(soup.select_one("#data").string)
    assert report == original
    assert display["selected_queries"] == report["selected_queries"]
    assert display["evidence"][0]["query"] == {"id": "Q1"}
    judgment = display["rows"][0]["slots"][0]["judgment"]
    assert "raw" not in judgment
    assert judgment["identity_judgment"] == {"verdict": "met"}
    assert display["rows"][0]["slots"][0]["position"] == 1
    assert "Normalized HTML inspection data" in soup.get_text()


def test_pagination_keeps_disclosure_and_untrusted_values_in_inert_json():
    report = report_fixture()
    payload = '</script><img src=x onerror="alert(1)">'
    report["selected_queries"][0]["query"] = payload
    html = html_report(report)
    assert payload not in html
    soup = BeautifulSoup(html, "html.parser")
    assert json.loads(soup.select_one("#data").string)["selected_queries"][0]["query"] == payload
    assert "Automated preliminary results" in soup.get_text()
    assert "Authorship and commercial interest are disclosed" in soup.get_text()
    assert "Synthetic demonstration: no provider was called" in html
