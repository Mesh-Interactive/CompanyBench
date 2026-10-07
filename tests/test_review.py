import asyncio
import json

import pytest
from test_evidence import packet

from companybench.config import Settings
from companybench.evidence import bound_packet
from companybench.models import (
    CompanyCandidate,
    Cost,
    EvidenceSource,
    SearchRequest,
    SearchResult,
    Usage,
)
from companybench.review import export_review, import_review, load_identity_overrides
from companybench.runner import manifest_for, research_run
from companybench.storage import RunStore


def frozen_store(tmp_path):
    store = RunStore(tmp_path / "run")
    p = packet()
    p.sources = [
        EvidenceSource(
            id="s1", url="https://example.com", text="Example is headquartered in the UK."
        )
    ]
    manifest = manifest_for(
        [p.query],
        Settings(providers=["review-fixture"], judges=["human"], target_count=1),
        reference_time=p.reference_time,
    )
    store.write("manifest.json", manifest)
    task_id = manifest["tasks"][0]["task_id"]
    request = SearchRequest(
        query=p.query, target_count=1, reference_time=p.reference_time, task_id=task_id
    )
    result = SearchResult(
        provider="review-fixture",
        candidates=[CompanyCandidate(position=1, name=p.company.name, domain=p.company.domain)],
        cost=Cost(public_usd=0),
    )
    store.save_task(
        task_id,
        status="searched",
        request=request.model_dump(mode="json"),
        result=result.model_dump(mode="json"),
    )

    class FixtureResearcher:
        async def research(self, incoming, leads, context):
            captured = incoming.model_copy(update={"sources": p.sources}, deep=True)
            return bound_packet(captured), Usage(), Cost(public_usd=0)

    # Exercise the real freeze path so review checks use current manifest, cohort,
    # identity, source-capture revision and search-result hashes without bypasses.
    version = asyncio.run(research_run(store, researcher=FixtureResearcher()))
    return store, version


def reviewed_file(store, tmp_path):
    path = tmp_path / "review.jsonl"
    export_review(store, path)
    row = json.loads(path.read_text())
    row["reviewed"] = True
    row["identity"] = {
        "verdict": "met",
        "reason": "Official source identifies the company",
        "evidence_ids": ["s1"],
    }
    row["conditions"][0].update(
        verdict="met", reason="Official headquarters statement", evidence_ids=["s1"]
    )
    path.write_text(json.dumps(row) + "\n")
    return path, row


def test_review_export_is_blind_and_human_import_recomputes_verdict(tmp_path):
    store, version = frozen_store(tmp_path)
    path, row = reviewed_file(store, tmp_path)
    assert "provider" not in row
    assert "reference" not in row["evidence_context"]
    result = import_review(store, path, reviewer="auditor-one")
    assert result["judge"] == "human-auditor-one"
    index = store.read(f"judgments/{version}/human-auditor-one/current.json")
    judgments = store.read(index["path"])
    assert judgments[0]["verdict"] == "valid"
    assert judgments[0]["packet_hash"] == row["packet_hash"]
    assert judgments[0]["cost"]["public_usd"] == 0


@pytest.mark.parametrize("change", ["hash", "criterion", "evidence", "overall", "frozen"])
def test_review_rejects_stale_tampered_or_inconsistent_labels(tmp_path, change):
    store, version = frozen_store(tmp_path)
    path, row = reviewed_file(store, tmp_path)
    if change == "hash":
        row["packet_hash"] = "wrong"
    if change == "criterion":
        row["conditions"][0]["criterion_id"] = "different"
    if change == "evidence":
        row["conditions"][0]["evidence_ids"] = ["invented"]
    if change == "overall":
        row["verdict"] = "invalid"
    if change == "frozen":
        values = store.read(f"evidence/{version}/packets.json")
        values[0]["sources"][0]["text"] = "Changed after freeze"
        store.write(f"evidence/{version}/packets.json", values)
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError):
        import_review(store, path)
    assert not store.artifact(f"judgments/{version}").exists()


def test_review_paths_and_unreviewed_template_are_not_accepted(tmp_path):
    store, _ = frozen_store(tmp_path)
    path = tmp_path / "review.jsonl"
    export_review(store, path)
    with pytest.raises(ValueError, match="reviewed"):
        import_review(store, path)
    with pytest.raises(ValueError, match="reviewer"):
        import_review(store, path, reviewer="../../other")


def test_identity_override_requires_explicit_review_and_explanation(tmp_path):
    path = tmp_path / "identities.json"
    data = {
        "overrides": [
            {
                "original_id": "old",
                "reviewed": True,
                "reason": "Official site identifies the operating entity",
                "source_urls": ["https://example.com/about"],
                "company": {
                    "id": "new",
                    "name": "Example",
                    "domain": "example.com",
                    "resolved": True,
                },
            }
        ]
    }
    path.write_text(json.dumps(data))
    result = load_identity_overrides(path)
    assert result["old"].id == "new"
    assert "Official site" in result["old"].notes
    data["overrides"][0]["reviewed"] = False
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="reviewed"):
        load_identity_overrides(path)


def test_reviewed_override_uses_exported_short_identity_id():
    from companybench.evidence import normalize_identity
    from companybench.models import CompanyCandidate, CompanyIdentity

    original = CompanyCandidate(position=1, name="Example")
    unresolved = normalize_identity(original, "q", unique_key="task:1")
    reviewed = CompanyIdentity(
        id="reviewed-company", name="Example Ltd", domain="example.com", resolved=True
    )
    actual = normalize_identity(
        original, "q", unique_key="task:1", aliases={unresolved.id: reviewed}
    )
    assert actual == reviewed
