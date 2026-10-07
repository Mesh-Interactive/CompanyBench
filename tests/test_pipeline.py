"""Pipeline tests use synthetic fixtures and prohibit HTTP or socket connections."""

import json
import socket

import httpx
import pytest

from companybench.demo import DemoJudge, DemoResearcher, run_demo
from companybench.judges import LLMJudge
from companybench.models import CompanyJudgment
from companybench.report import build_report, publish_bundle
from companybench.runner import grade_run, judge_directory, research_run
from companybench.storage import RunStore


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("The offline demonstration attempted a network call")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail)
    monkeypatch.setattr(httpx.Client, "request", fail)


@pytest.mark.asyncio
async def test_complete_offline_pipeline_and_publication(tmp_path):
    output = await run_demo(tmp_path / "demo")
    report = json.loads((output / "report.json").read_text())
    assert report["synthetic"]
    assert report["comparison_complete"]
    assert len(report["selected_queries"]) == 3
    assert len(report["rows"]) == 6
    clean = next(row for row in report["rows"] if row["provider"] == "demo-clean")
    noisy = next(row for row in report["rows"] if row["provider"] == "demo-noisy")
    assert clean["valid_companies"] == 3
    assert clean["quality_score"] == 75
    assert noisy["precision"] == 0.2
    assert (
        noisy["duplicates"]
        == noisy["malformed"]
        == noisy["unknown_companies"]
        == noisy["invalid_companies"]
        == 1
    )
    assert "Synthetic offline demonstration" in (output / "report.md").read_text()
    assert (output / "publication" / "checksums.json").exists()
    public = json.loads((output / "publication" / "report.json").read_text())
    assert public["synthetic"]
    assert all(
        source["redistributable"] for packet in public["evidence"] for source in packet["sources"]
    )


@pytest.mark.asyncio
async def test_completed_search_and_grade_resume_without_resubmitting(tmp_path):
    destination = tmp_path / "demo"
    await run_demo(destination)
    store = RunStore(destination)
    initial_ledger = store.read("evaluation_costs.json")
    await run_demo(destination)
    assert store.read("evaluation_costs.json") == initial_ledger
    for task in store.read("manifest.json")["tasks"]:
        checkpoints = store.task(task["task_id"])["checkpoints"]
        assert checkpoints["demo_submissions"] == 1
        if task["provider"] == "demo-noisy":
            assert checkpoints["demo_polls"] == 2


class ErrorJudge(DemoJudge):
    model = "synthetic-error-fixture-v1"

    async def grade(self, packet, context):
        if packet.company.name.endswith("Alpha"):
            return CompanyJudgment(
                query_id=packet.query.id,
                entity_id=packet.company.id,
                judge=self.name,
                verdict="error",
                error="Synthetic judge failure",
                model=self.model,
            )
        return await super().grade(packet, context)


@pytest.mark.asyncio
async def test_grading_error_blocks_complete_report_and_publication(tmp_path):
    await run_demo(tmp_path / "demo")
    store = RunStore(tmp_path / "demo")
    await grade_run(store, judge_instances={"demo": ErrorJudge()})
    report = build_report(store)
    assert not report["comparison_complete"]
    assert all(row["quality_score"] is None for row in report["rows"])
    assert all(row["grading_errors"] == 1 for row in report["rows"])
    with pytest.raises(ValueError, match="complete"):
        publish_bundle(store, report, tmp_path / "should-not-publish")


@pytest.mark.asyncio
async def test_demo_refuses_to_overwrite_real_run(tmp_path):
    store = RunStore(tmp_path / "real")
    store.write("manifest.json", {"synthetic": False})
    with pytest.raises(ValueError, match="different run"):
        await run_demo(store.path)
    assert store.read("manifest.json") == {"synthetic": False}


class ContractJudge(LLMJudge):
    """Mock only the remote response; retain the real packet checks and parser."""

    name = "fixture-llm"
    default_model = "synthetic-contract-model"

    async def request(self, packet, context):
        verdict = (
            json.loads(packet.sources[0].text)["fixture_verdict"] if packet.sources else "unknown"
        )
        evidence_ids = [source.id for source in packet.sources]
        payload = {
            "identity": {
                "verdict": "met" if packet.company.resolved else "unknown",
                "reason": "Synthetic fixture",
                "evidence_ids": evidence_ids,
            },
            "conditions": [
                {
                    "criterion_id": condition.id,
                    "verdict": verdict,
                    "reason": "Synthetic fixture",
                    "evidence_ids": evidence_ids,
                }
                for condition in packet.query.conditions
            ],
        }
        return {
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "output": [{"content": [{"type": "output_text", "text": json.dumps(payload)}]}],
        }


@pytest.mark.asyncio
async def test_frozen_research_packets_work_with_real_judge_contract(tmp_path):
    await run_demo(tmp_path / "demo")
    store = RunStore(tmp_path / "demo")
    await grade_run(
        store,
        judges=["fixture-llm"],
        judge_instances={"fixture-llm": ContractJudge()},
    )
    rows = [row for row in build_report(store)["rows"] if row["judge"] == "fixture-llm"]
    assert len(rows) == 6
    assert all(row["grading_complete"] for row in rows)


@pytest.mark.asyncio
async def test_retained_evidence_report_does_not_change_current_revision(tmp_path):
    await run_demo(tmp_path / "demo")
    store = RunStore(tmp_path / "demo")
    old_version = store.read("evidence/current.json")["version"]
    original = build_report(store)
    new_version = await research_run(store, new_revision=True, researcher=DemoResearcher())
    assert new_version != old_version
    await grade_run(store, judge_instances={"demo": DemoJudge()})
    current_pointer = store.read("evidence/current.json")
    current_manifest = store.read("manifest.json")

    retained = build_report(store.evidence_view(old_version))
    assert retained["evidence_version"] == old_version
    assert retained["rows"] == original["rows"]
    assert retained["comparison_complete"]
    assert build_report(store)["evidence_version"] == new_version
    assert store.read("evidence/current.json") == current_pointer
    assert store.read("manifest.json") == current_manifest


@pytest.mark.asyncio
async def test_changed_local_judge_factory_does_not_reuse_old_grading(tmp_path):
    await run_demo(tmp_path / "demo")
    store = RunStore(tmp_path / "demo")
    plugin = tmp_path / "judge.py"
    source = "from companybench.demo import DemoJudge\ndef create(options=None):\n    return DemoJudge()\n"
    plugin.write_text(source)
    name = f"{plugin}:create"
    await grade_run(store, judges=[name])
    version = store.read("evidence/current.json")["version"]
    index_path = f"judgments/{version}/{judge_directory(name)}/current.json"
    before = store.read(index_path)
    plugin.write_text(source + "# Revised factory implementation\n")
    await grade_run(store, judges=[name])
    after = store.read(index_path)
    assert after["config_hash"] != before["config_hash"]
    assert (
        after["implementation"]["factory"]["source_hash"]
        != before["implementation"]["factory"]["source_hash"]
    )
    assert store.read(before["path"])
    assert store.read(after["path"])
