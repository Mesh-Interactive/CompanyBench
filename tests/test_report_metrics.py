"""Check accounting and publication behavior without relying on an LLM's answer."""

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from companybench.config import Settings
from companybench.metrics import aggregate, calculate_metrics, paired_interval
from companybench.models import (
    CompanyCandidate,
    CompanyIdentity,
    CompanyJudgment,
    Cost,
    Criterion,
    Query,
    ReferenceCompany,
    ReferenceSet,
    SearchRequest,
    SearchResult,
)
from companybench.report import build_report, html_report, publish_bundle
from companybench.runner import manifest_for
from companybench.storage import RunStore


def query(identifier="TEST-1", *, reference=None, **facets):
    return Query(
        id=identifier,
        index=int(identifier.rsplit("-", 1)[-1]),
        query="Synthetic qualifying companies",
        family="firmographic",
        complexity="L1",
        industry="Manufacturing",
        conditions=[Criterion(id="qualifies", description="Qualifies in this fixture")],
        reference=reference,
        **facets,
    )


def score(
    q=None,
    *,
    names=("Alpha",),
    verdicts=None,
    cost=1.0,
    status="completed",
    target=5,
    latency=10.0,
    censored=False,
    provider="fixture",
    trial=1,
):
    q = q or query()
    identities = {}
    judgments = {}
    candidates = []
    for position, name in enumerate(names, 1):
        candidates.append(CompanyCandidate(position=position, name=name, malformed=name is None))
        if name is None:
            continue
        identity = CompanyIdentity(
            id=name, name=name, domain="shared.example", resolved=name != "Unresolved"
        )
        identities[position] = identity
        verdict = (verdicts or {}).get(name, "valid")
        judgments[identity.id] = CompanyJudgment(
            query_id=q.id,
            entity_id=identity.id,
            judge="fixture",
            verdict=verdict,
        )
    request = SearchRequest(
        query=q, target_count=target, reference_time=datetime.now(UTC), trial=trial
    )
    result = SearchResult(
        provider=provider,
        candidates=candidates,
        status=status,
        cost=Cost(public_usd=cost),
        latency_seconds=latency,
        timing_censored=censored,
    )
    row = calculate_metrics(request, result, identities, judgments)
    row["judge"] = "fixture"
    return row


def test_precision_counts_bad_positions_and_unknown_is_not_a_grading_error():
    row = score(
        names=("Alpha", "Alpha", "Unknown", "Invalid", None),
        verdicts={"Unknown": "unknown", "Invalid": "invalid"},
    )
    assert row["returned_companies"] == 5
    assert row["unique_companies"] == 3
    assert row["valid_companies"] == row["duplicates"] == row["malformed"] == 1
    assert row["precision"] == row["requested_count_fraction"] == 0.2
    assert row["quality_score"] == 20
    assert row["valid_companies_upper_bound"] == 2
    assert row["quality_upper_bound"] == 40
    assert row["grading_complete"]


def test_failed_empty_search_has_zero_quality_but_undefined_precision():
    row = score(names=(), status="failed", cost=None)
    assert row["quality_score"] == row["requested_count_fraction"] == 0
    assert row["precision"] is None
    assert row["cost_per_valid_company"] is None
    assert row["latency_per_valid_company"] is None
    assert aggregate([row])["search_errors"] == 1


def test_error_judgment_is_not_converted_to_factual_unknown():
    row = score(verdicts={"Alpha": "error"})
    assert row["grading_errors"] == 1
    assert row["unknown_companies"] == 0
    assert row["quality_score"] is None
    assert not aggregate([row])["comparison_complete"]


def reference(*names):
    return ReferenceSet(
        companies=[
            ReferenceCompany(name=name, domain="shared.example", aliases=[f"{name} former name"])
            for name in names
        ],
        exhaustive=True,
        source_urls=["https://official.example/cohort"],
        as_of="2024-01-01",
    )


def test_reference_smaller_than_requested_count_can_achieve_full_quality():
    row = score(query(reference=reference("Alpha", "Beta")), names=("Alpha", "Beta"))
    assert row["quality_score"] == 100
    assert row["quality_upper_bound"] == 100
    assert row["recall"] == row["recall_ceiling"] == 1
    assert row["requested_count_fraction"] == 0.4


def test_reference_names_and_reviewed_aliases_match_but_shared_parent_domain_does_not():
    row = score(
        query(reference=reference("Alpha", "Beta")),
        names=("ALPHA, Inc.", "Alpha former name", "Unlisted subsidiary", "Beta"),
    )
    assert row["valid_companies"] == 2
    assert row["duplicates"] == 1
    assert row["invalid_companies"] == row["reference_conflicts"] == 1
    assert row["precision"] == 0.5
    assert row["recall"] == 1
    assert row["quality_score"] == pytest.approx(200 * 2 / (4 + 2))


def test_reference_upper_bound_cannot_exceed_finite_universe():
    row = score(
        query(reference=reference("Alpha", "Beta")),
        names=("Alpha", "Unknown one", "Unknown two", "Unresolved"),
        verdicts={"Unknown one": "unknown", "Unknown two": "unknown"},
    )
    assert row["valid_companies_upper_bound"] == 2
    assert row["quality_upper_bound"] <= 100
    assert row["unresolved_identities"] == 1


def test_correct_empty_requires_a_successful_search_not_a_failed_request():
    q = query(reference=reference())
    successful = score(q, names=())
    failed = score(q, names=(), status="failed")
    assert successful["correct_empty_result"] is True
    assert failed["correct_empty_result"] is not True
    assert successful["quality_score"] is None
    assert successful["recall"] is None


def test_unknown_cost_and_censored_time_stay_nullable():
    row = score(cost=None, latency=12.5, censored=True)
    assert (
        row["cost_usd"]
        is row["cost_per_valid_company"]
        is row["valid_companies_per_dollar"]
        is None
    )
    assert row["quality_per_dollar"] is None
    assert row["latency_seconds"] is row["latency_per_valid_company"] is None
    assert row["observed_elapsed_seconds"] == 12.5
    summary = aggregate([row, score(query("TEST-2"), cost=2)])
    assert summary["cost_usd"] is None
    assert summary["known_cost_usd"] == 2
    assert summary["price_coverage"] == 0.5
    assert summary["latency_per_valid_company"] is None


def test_aggregate_quality_weights_queries_and_cost_efficiency_uses_totals():
    a = score(names=("Alpha", "Beta", "Gamma", "Delta", "Epsilon"), cost=2)
    b = score(query("TEST-2"), names=(), cost=1)
    summary = aggregate([a, b])
    assert summary["quality_score"] == 50
    assert summary["cost_per_valid_company"] == 3 / 5
    assert summary["valid_companies_per_dollar"] == 5 / 3
    assert summary["quality_per_dollar"] == 100 / 3
    repeated = aggregate([a, {**a, "trial": 2}, b])
    assert repeated["quality_score"] == 50  # Extra trials do not reweight this query.
    duplicated = aggregate([a, {**a, "trial": 2}, b, {**b, "trial": 2}])
    assert duplicated["quality_per_dollar"] == summary["quality_per_dollar"]
    assert aggregate([a], expected_tasks=2)["quality_score"] is None


def test_paired_comparisons_average_trials_before_bootstrapping_queries():
    left = [score(query(f"TEST-{i}"), names=("Alpha", "Beta")) for i in range(1, 6)]
    right = [score(query(f"TEST-{i}")) for i in range(1, 6)]
    result = paired_interval(left + [{**left[0], "trial": 2}], right)
    assert result["queries"] == 5
    assert result["interval_95"][0] == pytest.approx(result["difference"])
    assert result["interval_95"][1] == pytest.approx(result["difference"])
    assert paired_interval(left[:4], right[:4])["interval_95"] is None


def report_store(tmp_path, queries, providers=("fixture",)):
    store = RunStore(tmp_path)
    manifest = manifest_for(queries, Settings(providers=list(providers), judges=["fixture"]))
    store.write("manifest.json", manifest)
    store.write("evidence/current.json", {"version": "fixture-version"})
    store.write("evidence/fixture-version/packets.json", [])
    return store


def test_reports_include_all_supported_facets_and_keep_segment_coverage(tmp_path, monkeypatch):
    first = query(
        filter_tags=["technographic"],
        signal_tags=["hiring"],
        region_custom="LATAM",
        primary_category="signals",
    )
    second = query("TEST-2", filter_tags=["technographic"], region_custom="LATAM")
    store = report_store(tmp_path, [first, second])
    monkeypatch.setattr("companybench.report.metric_rows", lambda *args, **kwargs: [score(first)])
    report = build_report(store)
    pairs = {(segment["facet"], segment["value"]) for segment in report["segments"]}
    assert ("region_custom", "LATAM") in pairs
    assert ("signal_tags", "hiring") in pairs
    assert ("primary_category", "signals") in pairs
    assert not report["comparison_complete"]
    segment = next(
        row
        for row in report["segments"]
        if row["facet"] == "filter_tags" and row["value"] == "technographic"
    )
    assert segment["expected_tasks"] == 2
    assert not segment["comparison_complete"]


def test_missing_whole_provider_cannot_produce_complete_comparison(tmp_path, monkeypatch):
    q = query()
    store = report_store(tmp_path, [q], providers=("fixture", "missing"))
    monkeypatch.setattr("companybench.report.metric_rows", lambda *args, **kwargs: [score(q)])
    assert not build_report(store)["comparison_complete"]


def test_missing_declared_judge_cannot_produce_complete_comparison(tmp_path, monkeypatch):
    q = query()
    store = report_store(tmp_path, [q])
    manifest = store.read("manifest.json")
    manifest["declared_judges"] = ["fixture", "missing-judge"]
    store.write("manifest.json", manifest)
    monkeypatch.setattr("companybench.report.metric_rows", lambda *args, **kwargs: [score(q)])
    report = build_report(store)
    assert not report["comparison_complete"]
    absent = next(summary for summary in report["summaries"] if summary["judge"] == "missing-judge")
    assert absent["completed_tasks"] == 0
    assert absent["expected_tasks"] == 1
    assert absent["quality_score"] is None


def test_report_supports_numeric_and_mixed_custom_facets(tmp_path, monkeypatch):
    queries = [query(tier_custom=1), query("TEST-2", tier_custom="enterprise")]
    store = report_store(tmp_path, queries)
    monkeypatch.setattr(
        "companybench.report.metric_rows", lambda *args, **kwargs: [score(q) for q in queries]
    )
    report = build_report(store)
    assert report["comparison_complete"]
    segments = [segment for segment in report["segments"] if segment["facet"] == "tier_custom"]
    assert {segment["value"] for segment in segments} == {1, "enterprise"}
    assert all(
        segment["expected_tasks"] == 1 and segment["comparison_complete"] for segment in segments
    )


def test_report_html_keeps_untrusted_text_inside_inert_json(tmp_path, monkeypatch):
    q = query()
    store = report_store(tmp_path, [q])
    monkeypatch.setattr("companybench.report.metric_rows", lambda *args, **kwargs: [score(q)])
    report = build_report(store)
    payload = '</script><img src=x onerror="alert(1)">'
    report["selected_queries"][0]["query"] = payload
    report["rows"][0]["slots"][0]["name"] = payload
    text = html_report(report)
    assert payload not in text
    assert "\\u003c/script\\u003e" in text
    inert_data = text.split('<script type="application/json" id="data">', 1)[1].split(
        "</script>", 1
    )[0]
    assert json.loads(inert_data)["selected_queries"][0]["query"] == payload


def test_publication_omits_restricted_text_raw_payloads_and_secrets(tmp_path, monkeypatch):
    q = query()
    store = report_store(tmp_path / "run", [q])
    manifest = store.read("manifest.json")
    manifest["settings"]["provider_options"] = {
        "fixture": {"api_key": "SECRET", "workspace_id": "PRIVATE"}
    }
    store.write("manifest.json", manifest)
    task = manifest["tasks"][0]
    store.save_task(
        task["task_id"],
        result={
            "raw": {"private": "NATIVE"},
            "candidates": [{"name": "Alpha", "raw": {"private": "NATIVE"}}],
        },
    )
    monkeypatch.setattr("companybench.report.metric_rows", lambda *args, **kwargs: [score(q)])
    report = build_report(store)
    report["evidence"] = [
        {
            "query": q.model_dump(mode="json"),
            "company": {"id": "Alpha"},
            "sources": [
                {
                    "id": "s1",
                    "url": "https://example.org",
                    "text": "Restricted article",
                    "redistributable": False,
                }
            ],
        }
    ]
    target = tmp_path / "publication"
    publish_bundle(store, report, target)
    public_text = (target / "report.json").read_text()
    source = json.loads(public_text)["evidence"][0]["sources"][0]
    assert source["text"] == ""
    assert source["text_sha256"] == hashlib.sha256(b"Restricted article").hexdigest()
    assert "Restricted article" not in (target / "report.html").read_text()
    assert "SECRET" not in (target / "manifest.json").read_text()
    assert "PRIVATE" not in (target / "manifest.json").read_text()
    assert "NATIVE" not in (target / "normalized_results.json").read_text()
    for filename, digest in json.loads((target / "checksums.json").read_text())["files"].items():
        assert hashlib.sha256((target / filename).read_bytes()).hexdigest() == digest
    publish_bundle(store, report, target)  # Identical re-export is safe.
    changed = copy.deepcopy(report)
    changed["status"] = "A new revision"
    with pytest.raises(ValueError, match="different contents"):
        publish_bundle(store, changed, target)


@pytest.mark.parametrize(
    "filename", ["report.json", "report.html", "manifest.json", "normalized_results.json"]
)
def test_publication_reexport_rejects_tampered_files(tmp_path, monkeypatch, filename):
    q = query()
    store = report_store(tmp_path / "run", [q])
    monkeypatch.setattr("companybench.report.metric_rows", lambda *args, **kwargs: [score(q)])
    report = build_report(store)
    target = tmp_path / "publication"
    publish_bundle(store, report, target)
    (target / filename).write_text("Altered file")
    with pytest.raises(ValueError, match="changed|missing"):
        publish_bundle(store, report, target)
