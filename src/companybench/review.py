"""Provider-blind review files and explicit, auditable identity overrides.

These helpers never call paid APIs. Import validates the entire file before writing any
judgments, so a bad row cannot publish a partly imported review as a complete one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from companybench.evidence import normalize_domain, packet_context, packet_hash
from companybench.judges import RUBRIC_HASH, _complete
from companybench.models import CompanyIdentity, Cost, CriterionJudgment, Usage
from companybench.storage import RunStore, canonical_json, fingerprint, now_iso


def export_review(store: RunStore, path: str | Path) -> dict[str, Any]:
    from companybench.runner import frozen_evidence

    version, packets = frozen_evidence(store)
    rows = []
    for packet in sorted(packets, key=lambda p: (p.query.id, p.company.id)):
        rows.append(
            {
                "query_id": packet.query.id,
                "entity_id": packet.company.id,
                "packet_hash": packet_hash(packet),
                "evidence_version": version,
                "evidence_context": packet_context(packet),
                "reviewed": False,
                "identity": {"verdict": "unknown", "reason": "", "evidence_ids": []},
                "conditions": [
                    {"criterion_id": c.id, "verdict": "unknown", "reason": "", "evidence_ids": []}
                    for c in packet.query.conditions
                ],
            }
        )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
    return {"path": str(destination), "rows": len(rows), "evidence_version": version}


def import_review(store: RunStore, path: str | Path, reviewer: str = "human") -> dict[str, Any]:
    from companybench.runner import frozen_evidence

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", reviewer):
        raise ValueError("reviewer must contain lowercase alphanumeric words separated by hyphens")
    version, packets = frozen_evidence(store)
    by_key = {(p.query.id, p.company.id): p for p in packets}
    name = "human" if reviewer == "human" else "human-" + reviewer
    judgments = []
    seen = set()
    records = []
    for line_number, text in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip():
            continue
        row = json.loads(text)
        if not isinstance(row, dict):
            raise ValueError(f"Review row {line_number} must be an object")
        if row.get("reviewed") is not True:
            continue
        key = (row.get("query_id"), row.get("entity_id"))
        if key not in by_key or key in seen:
            raise ValueError(f"Review row {line_number} has an unknown or duplicate query/company")
        packet = by_key[key]
        if row.get("evidence_version") != version or row.get("packet_hash") != packet_hash(packet):
            raise ValueError(f"Review row {line_number} targets stale or changed evidence")
        if "evidence_context" in row and row["evidence_context"] != packet_context(packet):
            raise ValueError(f"Review row {line_number} altered its frozen evidence context")
        identity = CriterionJudgment(criterion_id="identity", **row["identity"])
        conditions = [CriterionJudgment.model_validate(item) for item in row["conditions"]]
        for condition in [identity, *conditions]:
            if condition.verdict != "unknown" and (
                not condition.evidence_ids or not condition.reason.strip()
            ):
                raise ValueError("Human met/failed labels require a reason and evidence IDs")
        judgment = _complete(
            packet,
            name,
            "human-review",
            identity,
            conditions,
            Usage(),
            Cost(public_usd=0, account_usd=0, basis="No API calls; human labor is not priced"),
            {"reviewer": reviewer, "reviewed": True},
        )
        if "verdict" in row and row["verdict"] != judgment.verdict:
            raise ValueError("Overall verdict conflicts with evidence/condition logic")
        judgments.append(judgment.model_dump(mode="json"))
        records.append(
            {
                "query_id": key[0],
                "entity_id": key[1],
                "packet_hash": row["packet_hash"],
                "identity": identity.model_dump(),
                "conditions": [c.model_dump() for c in conditions],
            }
        )
        seen.add(key)
    if not judgments:
        raise ValueError("No explicitly reviewed rows were provided")
    content_hash = fingerprint(
        {
            "reviewer": reviewer,
            "version": version,
            "rubric": RUBRIC_HASH,
            "records": sorted(records, key=lambda r: (r["query_id"], r["entity_id"])),
        }
    )
    relative = f"judgments/{version}/{name}/{content_hash}.json"
    # Retain prior imports by their content hash; only the current pointer changes.
    store.write(relative, judgments)
    store.write(
        f"judgments/{version}/{name}/current.json",
        {
            "path": relative,
            "config_hash": content_hash,
            "options": {"reviewer": reviewer, "imported_rows": len(judgments)},
            "graded_at": now_iso(),
            "model": "human-review",
            "human_review": True,
        },
    )
    store.event(
        "human_review_imported",
        reviewer=reviewer,
        judge=name,
        evidence_version=version,
        rows=len(judgments),
        review_hash=content_hash,
    )
    return {
        "judge": name,
        "rows": len(judgments),
        "path": relative,
        "evidence_version": version,
        "review_hash": content_hash,
    }


def load_identity_overrides(path: str | Path) -> dict[str, CompanyIdentity]:
    """Load explicit review records keyed by pre-normalization identity ID.

    Format: {"overrides": [{"original_id": "...", "reviewed": true, "reason": "...",
    "source_urls": ["https://..."], "company": {CompanyIdentity fields}}]}.
    Empty/missing files are not silently accepted: callers decide whether overrides exist.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("overrides"), list):
        raise ValueError("Identity map requires an overrides list")
    output: dict[str, CompanyIdentity] = {}
    canonical: dict[str, tuple[str, str | None]] = {}
    for row in data["overrides"]:
        if not isinstance(row, dict) or row.get("reviewed") is not True:
            raise ValueError("Every identity override must be explicitly reviewed")
        original_id = row.get("original_id")
        reason = row.get("reason")
        sources = row.get("source_urls")
        if not isinstance(original_id, str) or not original_id or original_id in output:
            raise ValueError("Identity overrides need unique, nonempty original IDs")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(sources, list)
            or not sources
        ):
            raise ValueError("Identity overrides need a review reason and supporting source URLs")
        if any(
            not isinstance(url, str)
            or urlsplit(url).scheme not in {"https", "http"}
            or not urlsplit(url).hostname
            for url in sources
        ):
            raise ValueError("Identity override sources must be HTTP(S) URLs")
        company = CompanyIdentity.model_validate(row["company"])
        if (
            not company.id
            or not company.name.strip()
            or not company.resolved
            or not normalize_domain(company.domain)
        ):
            raise ValueError(
                "Reviewed identity must have a nonempty ID, name and valid domain and be resolved"
            )
        company.domain = normalize_domain(company.domain)
        pair = (company.name.casefold(), company.domain)
        if company.id in canonical and canonical[company.id] != pair:
            raise ValueError("The same resolved ID cannot describe conflicting company identities")
        canonical[company.id] = pair
        company.notes = "; ".join(
            filter(
                None,
                [
                    company.notes,
                    "Reviewed identity override: " + reason.strip(),
                    "Sources: " + ", ".join(sources),
                ],
            )
        )
        output[original_id] = company
    return output


def export_identity_proposals(store: RunStore, path: str | Path) -> dict[str, Any]:
    """Export unresolved occurrences and same-domain collisions; never infer replacements."""
    from companybench.evidence import normalize_identity
    from companybench.models import SearchRequest, SearchResult

    manifest = store.read("manifest.json")
    identities: dict[str, CompanyIdentity] = {}
    queries_by_id: dict[str, str] = {}
    domains: dict[str, set[str]] = {}
    for task in manifest["tasks"]:
        state = store.task(task["task_id"])
        if not state.get("result"):
            continue
        request = SearchRequest.model_validate(state["request"])
        result = SearchResult.model_validate(state["result"])
        for candidate in result.candidates[: request.target_count]:
            company = normalize_identity(
                candidate, request.query.id, unique_key=f"{task['task_id']}:{candidate.position}"
            )
            identities[company.id] = company
            queries_by_id[company.id] = request.query.id
            if company.domain:
                # Collisions matter within one query, not merely across different queries.
                domains.setdefault(request.query.id + ":" + company.domain, set()).add(company.id)
    collision_ids = (
        set().union(*(ids for ids in domains.values() if len(ids) > 1)) if domains else set()
    )
    rows: list[dict[str, Any]] = [
        {
            "original_id": company.id,
            "query_id": queries_by_id[company.id],
            "reviewed": False,
            "reason": "",
            "source_urls": [],
            "company": company.model_dump(),
            "issue": "unresolved" if not company.resolved else "shared_hostname",
        }
        for company in identities.values()
        if not company.resolved or company.id in collision_ids
    ]
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(canonical_json({"overrides": rows}) + "\n", encoding="utf-8")
    return {"path": str(destination), "rows": len(rows)}
