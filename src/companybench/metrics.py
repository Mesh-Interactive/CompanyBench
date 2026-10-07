"""Pure metric calculations; no provider calls or evidence retrieval."""

from __future__ import annotations

import random
import re
import statistics
from collections import defaultdict
from typing import Any

from companybench.models import CompanyIdentity, CompanyJudgment, SearchRequest, SearchResult

METRIC_LABELS = {
    "returned_companies": "Companies returned",
    "unique_companies": "Unique companies returned",
    "valid_companies": "Valid companies found",
    "precision": "Precision",
    "requested_count_fraction": "Valid companies found (% of requested)",
    "cost_per_valid_company": "Cost per valid company",
    "valid_companies_per_dollar": "Valid companies per dollar",
    "latency_seconds": "Search time (seconds)",
    "latency_per_valid_company": "Search time per valid company (seconds)",
    "quality_score": "Quality score",
    "quality_per_dollar": "Quality score per dollar",
    "recall": "Recall",
    "unknown_companies": "Not enough evidence",
    "unresolved_identities": "Unresolved company identities",
    "grading_errors": "Incomplete company judgments",
    "duplicates": "Duplicate results",
    "malformed": "Malformed results",
    "invalid_companies": "Invalid companies",
}


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return (
        numerator / denominator
        if numerator is not None and denominator is not None and denominator > 0
        else None
    )


def _company_name(value: str) -> str:
    name = re.sub(r"[.,]", "", value.casefold()).strip()
    return re.sub(r"\s+(inc|incorporated|llc|ltd|limited|corp|corporation|gmbh)$", "", name)


def query_facets(query: Any) -> dict[str, Any]:
    excluded = {
        "id",
        "index",
        "query",
        "conditions",
        "rule",
        "acceptance",
        "reference",
        "evidence",
        "pitfalls",
        "gtm_use_case",
        "source_urls",
        "source_ids",
        "fixture",
        "logic",
        "verification_limitations",
    }
    values = query.model_dump(mode="json") if hasattr(query, "model_dump") else query
    return {
        key: value
        for key, value in values.items()
        if key not in excluded
        and (
            isinstance(value, (str, int, float, bool))
            or (
                isinstance(value, list)
                and all(isinstance(item, (str, int, float, bool)) for item in value)
            )
        )
    }


def calculate_metrics(
    request: SearchRequest,
    result: SearchResult,
    identities: dict[int, CompanyIdentity],
    judgments: dict[str, CompanyJudgment],
    *,
    cost_view: str = "public",
    prefix: int | None = None,
) -> dict[str, Any]:
    """Score original positions without dropping bad slots or backfilling beyond K."""
    if cost_view not in {"public", "account", "confirmed"}:
        raise ValueError("Cost view must be public, account, or confirmed")
    if prefix is not None and prefix <= 0:
        raise ValueError("Prefix must be positive")
    k = min(request.target_count, prefix) if prefix is not None else request.target_count
    candidates = result.candidates[:k]
    counts = dict.fromkeys(
        [
            "valid_companies",
            "invalid_companies",
            "unknown_companies",
            "unresolved_identities",
            "grading_errors",
            "duplicates",
            "malformed",
        ],
        0,
    )
    reference = request.query.reference
    reference_names = (
        [
            {_company_name(company.name), *(_company_name(alias) for alias in company.aliases)}
            for company in reference.companies
        ]
        if reference and reference.exhaustive
        else []
    )
    matched_reference: set[int] = set()
    reference_conflicts = 0
    seen: set[str] = set()
    slots: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates, 1):
        identity = identities.get(ordinal)
        slot: dict[str, Any] = {
            "position": ordinal,
            "native_position": candidate.position,
            "name": candidate.name,
            "domain": candidate.domain,
            "entity_id": identity.id if identity else None,
        }
        if (
            candidate.position != ordinal
            or candidate.malformed
            or (not candidate.name and not candidate.domain and not candidate.website)
        ):
            category = "malformed"
            if candidate.position != ordinal:
                slot["contract_error"] = "Candidate position does not match its original list order"
        elif not identity or not identity.resolved:
            category = "unresolved_identities"
        elif identity.id in seen:
            category = "duplicates"
        else:
            seen.add(identity.id)
            judgment = judgments.get(identity.id)
            slot["judgment"] = judgment.model_dump(mode="json") if judgment else None
            category = {
                "valid": "valid_companies",
                "invalid": "invalid_companies",
                "unknown": "unknown_companies",
            }.get(judgment.verdict if judgment else "error", "grading_errors")
            if category == "valid_companies" and reference and reference.exhaustive:
                names = {
                    _company_name(identity.name),
                    *(_company_name(alias) for alias in identity.aliases),
                }
                membership = next(
                    (
                        i
                        for i, ref_names in enumerate(reference_names)
                        if names.intersection(ref_names)
                    ),
                    None,
                )
                if membership is None:
                    category = "invalid_companies"
                    reference_conflicts += 1
                    slot["reference_check"] = (
                        "Automated valid judgment conflicts with the exhaustive reference; no exact name or reviewed alias matches"
                    )
                elif membership in matched_reference:
                    category = "duplicates"
                    slot["reference_check"] = (
                        "Another returned company already matches this reference member"
                    )
                else:
                    matched_reference.add(membership)
        counts[category] += 1
        slot["category"] = category
        slots.append(slot)
    r, v = len(candidates), counts["valid_companies"]
    attainable = min(k, len(reference.companies)) if reference and reference.exhaustive else k
    complete = counts["grading_errors"] == 0
    empty_reference = bool(reference and reference.exhaustive and not reference.companies)
    quality = (
        200 * v / (r + attainable)
        if complete and r + attainable > 0 and not empty_reference
        else None
    )
    if result.status in {"failed", "refused"}:
        quality = 0.0
    cost = getattr(result.cost, f"{cost_view}_usd")
    latency = result.latency_seconds if not result.timing_censored else None
    recall = None
    if reference and reference.exhaustive:
        recall = ratio(len(matched_reference), len(reference.companies)) if complete else None
    unknown_upper = min(
        attainable, v + counts["unknown_companies"] + counts["unresolved_identities"]
    )
    facets = query_facets(request.query)
    return {
        "query_id": request.query.id,
        "query_index": request.query.index,
        "provider": result.provider,
        "model": result.model,
        "trial": request.trial,
        "requested_count": k,
        "attainable_count": attainable,
        "returned_companies": r,
        "unique_companies": len(seen),
        **counts,
        "precision": ratio(v, r) if complete else None,
        "requested_count_fraction": v / k if complete else None,
        "valid_fraction_among_resolved": ratio(v, len(seen)) if complete else None,
        "precision_among_decided": ratio(v, v + counts["invalid_companies"]) if complete else None,
        "unknown_fraction": ratio(counts["unknown_companies"] + counts["unresolved_identities"], r),
        "cost_usd": cost,
        "cost_details": result.cost.model_dump(mode="json"),
        "usage": result.usage.model_dump(mode="json"),
        "cost_view": cost_view,
        "cost_per_valid_company": ratio(cost, v) if complete else None,
        "valid_companies_per_dollar": ratio(v, cost) if complete else None,
        "latency_seconds": latency,
        "observed_elapsed_seconds": result.latency_seconds,
        "latency_per_valid_company": ratio(latency, v) if complete else None,
        "quality_score": quality,
        "quality_per_dollar": ratio(quality, cost),
        "recall": recall,
        "reference_count": len(reference.companies) if reference and reference.exhaustive else None,
        "reference_conflicts": reference_conflicts,
        "recall_ceiling": ratio(min(k, len(reference.companies)), len(reference.companies))
        if reference and reference.exhaustive
        else None,
        "correct_empty_result": r == 0 and result.status == "completed"
        if empty_reference and complete
        else None,
        "valid_companies_upper_bound": unknown_upper if complete else None,
        "quality_upper_bound": 200 * unknown_upper / (r + attainable)
        if complete and r + attainable > 0 and not empty_reference
        else None,
        "grading_complete": complete,
        "search_status": result.status,
        "search_error": result.error,
        "timing_censored": result.timing_censored,
        "slots": slots,
        "facets": facets,
        "undefined_reasons": {
            "precision": "No returned companies"
            if not r
            else "Incomplete grading"
            if not complete
            else None,
            "cost": "Price or usage is unknown" if cost is None else None,
            "cost_per_valid_company": "No valid companies" if not v else None,
            "latency": "Completion time is uncertain after interruption"
            if result.timing_censored
            else None,
            "recall": "No exhaustive nonempty reference list" if recall is None else None,
            "quality_score": "Known empty reference list; use Correct empty results"
            if empty_reference
            else "Incomplete grading"
            if not complete
            else None,
            "valid_companies_per_dollar": "Search cost is unknown"
            if cost is None
            else "Search cost is zero"
            if not cost
            else "Incomplete grading"
            if not complete
            else None,
            "quality_per_dollar": "Quality score is undefined"
            if quality is None
            else "Search cost is unknown"
            if cost is None
            else "Search cost is zero"
            if not cost
            else None,
        },
    }


def aggregate(rows: list[dict[str, Any]], *, expected_tasks: int | None = None) -> dict[str, Any]:
    """Query means keep repeated trials from increasing one query's weight."""
    expected = expected_tasks if expected_tasks is not None else len(rows)
    complete = len(rows) == expected and all(row["grading_complete"] for row in rows)
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_query[row["query_id"]].append(row)

    def query_mean(metric: str) -> float | None:
        values = [
            statistics.mean(valid)
            for group in by_query.values()
            if (valid := [row[metric] for row in group if row[metric] is not None])
        ]
        return statistics.mean(values) if values and complete else None

    known_costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    total_cost = sum(known_costs) if len(known_costs) == len(rows) and rows else None
    valid_total = sum(row["valid_companies"] for row in rows)
    latencies = [row["latency_seconds"] for row in rows if row["latency_seconds"] is not None]
    query_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        query_groups[row["facets"]["complexity"]].append(row)
    strata_means = []
    for group in query_groups.values():
        strata_queries = {row["query_id"] for row in group}
        values = [
            statistics.mean(known)
            for query_id in strata_queries
            if (
                known := [
                    row["quality_score"]
                    for row in by_query[query_id]
                    if row["quality_score"] is not None
                ]
            )
        ]
        if values:
            strata_means.append(statistics.mean(values))
    quality_rows = [row for row in rows if row["quality_score"] is not None]
    quality_cost = (
        sum(row["cost_usd"] for row in quality_rows)
        if quality_rows and all(row["cost_usd"] is not None for row in quality_rows)
        else None
    )
    return {
        "queries": len(by_query),
        "completed_tasks": len(rows),
        "expected_tasks": expected,
        "comparison_complete": complete,
        "valid_companies": valid_total,
        "mean_valid_companies": query_mean("valid_companies"),
        "precision": query_mean("precision"),
        "quality_score": query_mean("quality_score"),
        "quality_by_equal_complexity": statistics.mean(strata_means)
        if strata_means and complete
        else None,
        "requested_count_fraction": query_mean("requested_count_fraction"),
        "cost_usd": total_cost,
        "known_cost_usd": sum(known_costs),
        "price_coverage": len(known_costs) / len(rows) if rows else 0,
        "cost_per_valid_company": ratio(total_cost, valid_total) if complete else None,
        "valid_companies_per_dollar": ratio(valid_total, total_cost) if complete else None,
        "quality_per_dollar": ratio(sum(row["quality_score"] for row in quality_rows), quality_cost)
        if complete
        else None,
        "quality_cost_usd": quality_cost,
        "quality_tasks": len(quality_rows),
        "mean_latency_seconds": statistics.mean(latencies) if latencies else None,
        "latency_per_valid_company": ratio(sum(latencies), valid_total)
        if complete and len(latencies) == len(rows)
        else None,
        "search_errors": sum(row["search_status"] in {"failed", "refused"} for row in rows),
        "partial_searches": sum(row["search_status"] in {"partial", "truncated"} for row in rows),
        "known_empty_tasks": sum(row["correct_empty_result"] is not None for row in rows),
        "correct_empty_results": sum(row["correct_empty_result"] is True for row in rows),
        "timing_coverage": len(latencies) / len(rows) if rows else 0,
        "grading_errors": sum(row["grading_errors"] for row in rows),
        "unknown_companies": sum(row["unknown_companies"] for row in rows),
        "unknown_fraction": ratio(
            sum(row["unknown_companies"] + row["unresolved_identities"] for row in rows),
            sum(row["returned_companies"] for row in rows),
        ),
        "quality_upper_bound": query_mean("quality_upper_bound"),
    }


def paired_interval(
    left: list[dict[str, Any]], right: list[dict[str, Any]], *, seed: int = 42, samples: int = 1000
) -> dict[str, Any]:
    """Bootstrap paired query differences after averaging trials within each query."""

    def means(rows: list[dict[str, Any]]) -> dict[str, float]:
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row["quality_score"] is not None and row["grading_complete"]:
                groups[row["query_id"]].append(row["quality_score"])
        return {key: statistics.mean(values) for key, values in groups.items()}

    a, b = means(left), means(right)
    ids = sorted(a.keys() & b.keys())
    diffs = [a[key] - b[key] for key in ids]
    if len(diffs) < 5:
        return {
            "queries": len(diffs),
            "difference": statistics.mean(diffs) if diffs else None,
            "interval_95": None,
            "reason": "At least five paired queries are required",
        }
    rng = random.Random(seed)
    draws = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(samples))
    return {
        "queries": len(diffs),
        "difference": statistics.mean(diffs),
        "interval_95": [draws[int(samples * 0.025)], draws[min(samples - 1, int(samples * 0.975))]],
        "samples": samples,
        "seed": seed,
        "interpretation": "Conditional on these authored queries and automated judgments; excludes annotation error and future API changes",
    }
