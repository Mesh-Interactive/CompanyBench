"""Regression tests for interrupted writes, settled costs, and frozen-run integrity."""

import json
import socket

import httpx
import pytest

from companybench.config import Settings
from companybench.demo import DemoJudge, run_demo
from companybench.models import Cost
from companybench.runner import (
    _budget,
    _save_evaluation_cost,
    evaluation_costs,
    frozen_evidence,
    grade_run,
    metric_rows,
)
from companybench.storage import RunStore
from companybench.transport import Budget


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Recovery regression attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail)
    monkeypatch.setattr(httpx.Client, "request", fail)


@pytest.fixture
async def demo_store(tmp_path):
    await run_demo(tmp_path / "demo")
    return RunStore(tmp_path / "demo")


def test_append_repairs_only_incomplete_tail_and_preserves_complete_records(tmp_path):
    store = RunStore(tmp_path)
    store.append("costs.jsonl", {"id": "settled", "cost": 1})
    path = store.artifact("costs.jsonl")
    with path.open("ab") as stream:
        stream.write(b'{"id":"interrupted","cost":')
    assert store.read_lines("costs.jsonl") == [{"id": "settled", "cost": 1}]
    store.append("costs.jsonl", {"id": "recovered", "cost": 2})
    assert store.read_lines("costs.jsonl") == [
        {"id": "settled", "cost": 1},
        {"id": "recovered", "cost": 2},
    ]
    # A complete last JSON value missing only its newline must also survive.
    path.write_text('{"id":"complete"}')
    store.append("costs.jsonl", {"id": "next"})
    assert store.read_lines("costs.jsonl") == [{"id": "complete"}, {"id": "next"}]


def test_journal_corruption_in_a_complete_record_is_not_silently_discarded(tmp_path):
    store = RunStore(tmp_path)
    store.artifact("costs.jsonl").write_text('{"id":"first"}\nnot-json\n{"id":"last"}\n')
    with pytest.raises(json.JSONDecodeError):
        store.read_lines("costs.jsonl")


def test_journal_recovers_a_crash_inside_a_multibyte_utf8_character(tmp_path):
    store = RunStore(tmp_path)
    store.append("costs.jsonl", {"id": "settled"})
    with store.artifact("costs.jsonl").open("ab") as stream:
        stream.write(b'{"note":"caf\xc3')
    assert store.read_lines("costs.jsonl") == [{"id": "settled"}]
    store.append("costs.jsonl", {"id": "next"})
    assert store.read_lines("costs.jsonl") == [{"id": "settled"}, {"id": "next"}]


def test_settlement_updates_unknown_cost_without_double_counting_and_is_durable(tmp_path):
    store = RunStore(tmp_path)
    store.write("manifest.json", {"tasks": []})
    first = {
        "task_id": "judge:recoverable",
        "stage": "judge",
        "usage": {},
        "cost": {"public_usd": None},
    }
    store.write("evaluation_costs.json", [first])
    ledger = evaluation_costs(store)
    settled = {item["task_id"]: item for item in ledger}
    budget = _budget(store, Settings())
    assert budget.unknown_operations == 1
    assert budget.reserved == 0
    updated = {**first, "cost": {"public_usd": 0.25}}
    _save_evaluation_cost(store, first["task_id"], updated, ledger, settled, budget)
    assert budget.reserved == 0.25
    assert budget.unknown_operations == 0
    assert evaluation_costs(store) == [updated]  # Journal wins even before snapshot write.
    journal = store.artifact("evaluation_costs.jsonl").read_bytes()
    _save_evaluation_cost(store, first["task_id"], updated, ledger, settled, budget)
    assert store.artifact("evaluation_costs.jsonl").read_bytes() == journal
    assert budget.reserved == 0.25
    corrected = {**updated, "cost": {"public_usd": 0.30}}
    _save_evaluation_cost(store, first["task_id"], corrected, ledger, settled, budget)
    assert budget.reserved == pytest.approx(0.30)
    assert _budget(store, Settings()).reserved == pytest.approx(0.30)


def test_settlement_replaces_reserved_estimate_with_observed_cost(tmp_path):
    store = RunStore(tmp_path)
    key = "judge:priced"
    store.write(store.operation_file(key, "paid"), {"status": "completed", "estimated_usd": 2})
    budget = Budget()
    budget.reserve(2)
    row = {"task_id": key, "cost": {"public_usd": 1.25}}
    _save_evaluation_cost(store, key, row, [], {}, budget)
    assert budget.reserved == 1.25


def test_unknown_settled_charge_retains_estimated_budget_exposure_after_restart(tmp_path):
    store = RunStore(tmp_path)
    store.write("manifest.json", {"tasks": []})
    key = "judge:unpriced-response"
    store.write(store.operation_file(key, "paid"), {"status": "completed", "estimated_usd": 2})
    budget = Budget()
    budget.reserve(2)
    row = {"task_id": key, "cost": {"public_usd": None}}
    _save_evaluation_cost(store, key, row, [], {}, budget)
    assert budget.reserved == 2
    assert evaluation_costs(store)[0]["cost"]["public_usd"] is None
    restored = _budget(store, Settings())
    assert restored.reserved == 2
    actual = {"task_id": key, "cost": {"public_usd": 1.25}}
    _save_evaluation_cost(store, key, actual, [row], {key: row}, restored)
    assert restored.reserved == 1.25
    assert _budget(store, Settings()).reserved == 1.25


class RecoveringJudge(DemoJudge):
    model = "synthetic-recovery-v1"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def grade(self, packet, context):
        self.calls.append((packet.query.id, packet.company.id))
        judgment = await super().grade(packet, context)
        if packet.company.name.endswith("Alpha"):
            judgment.cost = Cost(public_usd=None if self.fail else 0.125)
            if self.fail:
                judgment.verdict = "error"
                judgment.error = "Synthetic recoverable failure before usage was known"
        return judgment


@pytest.mark.asyncio
async def test_real_grade_resume_retries_errors_and_updates_cost_ledger(demo_store):
    first = RecoveringJudge(fail=True)
    await grade_run(demo_store, judge_instances={"demo": first})
    before = evaluation_costs(demo_store)
    assert sum(row["cost"]["public_usd"] is None for row in before) == 3
    second = RecoveringJudge()
    await grade_run(demo_store, judge_instances={"demo": second})
    assert len(second.calls) == 3  # Successful company judgments were reused.
    after = evaluation_costs(demo_store)
    assert len(after) == len(before)
    assert all(row["cost"]["public_usd"] is not None for row in after)
    assert sum(row["cost"]["public_usd"] for row in after) == pytest.approx(0.375)
    manifest = demo_store.read("manifest.json")
    budget = _budget(demo_store, Settings.model_validate(manifest["settings"]))
    assert budget.reserved == pytest.approx(1.05 + 0.375)
    assert budget.unknown_operations == 0


@pytest.mark.asyncio
async def test_company_shards_recover_after_failure_before_consolidated_write(
    demo_store, monkeypatch
):
    original_write = demo_store.write

    def crash_on_consolidation(relative, value):
        if relative.startswith("judgments/") and isinstance(value, list):
            raise OSError("Synthetic crash before consolidated judgments")
        original_write(relative, value)

    judge = RecoveringJudge()
    monkeypatch.setattr(demo_store, "write", crash_on_consolidation)
    with pytest.raises(OSError, match="Synthetic crash"):
        await grade_run(demo_store, judge_instances={"demo": judge})
    assert len(judge.calls) == 15
    journal_after_crash = evaluation_costs(demo_store)
    monkeypatch.setattr(demo_store, "write", original_write)
    recovered = RecoveringJudge()
    await grade_run(demo_store, judge_instances={"demo": recovered})
    assert recovered.calls == []
    assert evaluation_costs(demo_store) == journal_after_crash
    assert all(row["grading_complete"] for row in metric_rows(demo_store))


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact", ["individual", "consolidated"])
async def test_resuming_grading_rejects_edits_to_successful_judgments(demo_store, artifact):
    version = demo_store.read("evidence/current.json")["version"]
    index = demo_store.read(f"judgments/{version}/demo/current.json")
    if artifact == "individual":
        path = next(
            demo_store.artifact(f"judgments/{version}/demo/{index['config_hash']}/companies").glob(
                "*.json"
            )
        )
        relative = str(path.relative_to(demo_store.path))
        changed = demo_store.read(relative)
        changed["verdict"] = "invalid" if changed["verdict"] == "valid" else "valid"
    else:
        relative = index["path"]
        changed = demo_store.read(relative)
        changed[0]["verdict"] = "invalid" if changed[0]["verdict"] == "valid" else "valid"
    demo_store.write(relative, changed)
    with pytest.raises(ValueError, match="[Ff]rozen|conflicts"):
        await grade_run(demo_store, judge_instances={"demo": DemoJudge()})


@pytest.mark.parametrize(
    "field", ["cost", "candidate", "status", "request", "settings", "query", "reference_time"]
)
def test_frozen_evidence_rejects_changed_search_inputs_results_and_configuration(demo_store, field):
    manifest = demo_store.read("manifest.json")
    task = manifest["tasks"][0]
    state = demo_store.task(task["task_id"])
    if field == "cost":
        state["result"]["cost"]["public_usd"] = 999
    elif field == "candidate":
        state["result"]["candidates"][0]["name"] = "A different company"
    elif field == "status":
        state["result"]["status"] = "failed"
    elif field == "request":
        state["request"]["target_count"] = 4
    elif field == "settings":
        manifest["settings"]["target_count"] = 4
    elif field == "query":
        manifest["queries"][0]["conditions"][0]["description"] = "A changed acceptance rule"
    elif field == "reference_time":
        manifest["reference_time"] = "2030-01-01T00:00:00+00:00"
    demo_store.write("manifest.json", manifest)
    demo_store.write(demo_store.task_file(task["task_id"]), state)
    with pytest.raises(ValueError, match="[Ff]rozen|reference|configuration"):
        frozen_evidence(demo_store)
