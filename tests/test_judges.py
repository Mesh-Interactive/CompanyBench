import pytest
from test_evidence import packet

from companybench.judges import evaluate_query, get_judge
from companybench.models import Criterion, Query, Rule


def test_three_valued_logic_preserves_or_not_and_unknown():
    q = Query(
        id="q",
        index=1,
        query="A or not B",
        family="x",
        complexity="L2",
        industry="x",
        conditions=[Criterion(id="a", description="A"), Criterion(id="b", description="B")],
        rule=Rule(
            op="any",
            children=[
                Rule(op="condition", condition_id="a"),
                Rule(op="not", children=[Rule(op="condition", condition_id="b")]),
            ],
        ),
    )
    assert evaluate_query(q, {"a": "failed", "b": "failed"}) == "valid"
    assert evaluate_query(q, {"a": "failed", "b": "met"}) == "invalid"
    assert evaluate_query(q, {"a": "failed", "b": "unknown"}) == "unknown"
    assert evaluate_query(q, {"a": "met"}) == "valid"


class Context:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_llm_bad_evidence_id_is_grading_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    import json

    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "identity": {
                                    "verdict": "met",
                                    "reason": "yes",
                                    "evidence_ids": ["invented"],
                                },
                                "conditions": [
                                    {
                                        "criterion_id": "hq",
                                        "verdict": "met",
                                        "reason": "yes",
                                        "evidence_ids": ["invented"],
                                    }
                                ],
                            }
                        ),
                    }
                ],
            }
        ]
    }
    judgment = await get_judge("llm").grade(packet(), Context(response))
    assert judgment.verdict == "error"
    assert "evidence" in judgment.error.lower()


@pytest.mark.asyncio
async def test_decisions_refusal_is_error_and_prompt_names_condition(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    context = Context(
        {"answers": [{"name": "identity", "type": "refusal", "refusal": "unavailable"}]}
    )
    judgment = await get_judge("decisions").grade(packet(), context)
    assert judgment.verdict == "error"
    body = context.requests[0][1]["json"]
    assert "Headquartered in the UK" in str(body["questions"])
    assert body["model"] == "gpt-6-luna"


@pytest.mark.asyncio
async def test_jev_choice_does_not_pretend_to_supply_rationale(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test")
    context = Context(
        {
            "answers": {
                "identity": {
                    "choice": "unknown",
                    "probabilities": {"met": 0.1, "failed": 0.1, "unknown": 0.8},
                },
                "hq": {
                    "choice": "unknown",
                    "probabilities": {"met": 0.1, "failed": 0.1, "unknown": 0.8},
                },
            },
            "usage": {"input_tokens": 100},
        }
    )
    judgment = await get_judge("jev").grade(packet(), context)
    assert judgment.verdict == "unknown"
    assert judgment.conditions[0].reason == ""
    body = context.requests[0][1]["json"]
    assert body["model"] == "jev-1.13.0"
    assert "Headquartered in the UK" in body["questions"]["hq"]["instructions"]


@pytest.mark.asyncio
async def test_decisions_typed_probabilities_and_identity_are_preserved(monkeypatch):
    from companybench.models import EvidenceSource

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    p = packet()
    p.sources = [
        EvidenceSource(
            id="s1", url="https://example.com", text="Example is headquartered in London."
        )
    ]
    context = Context(
        {
            "answers": [
                {
                    "type": "choice",
                    "name": key,
                    "choice": "met",
                    "probabilities": [
                        {"value": "met", "probability": 0.9},
                        {"value": "failed", "probability": 0.01},
                        {"value": "unknown", "probability": 0.09},
                    ],
                    "confidence": 0.8,
                }
                for key in ["identity", "hq"]
            ],
            "usage": {"input_tokens": 1000},
        }
    )
    judgment = await get_judge("decisions").grade(p, context)
    assert judgment.verdict == "valid"
    assert judgment.conditions[0].evidence_ids == []
    assert judgment.conditions[0].probabilities["met"] == 0.9
    assert judgment.raw["identity_judgment"]["verdict"] == "met"
    assert judgment.cost.public_usd == pytest.approx(0.0001)


@pytest.mark.asyncio
async def test_missing_usage_remains_unknown_cost(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test")
    context = Context({"answers": {key: {"choice": "unknown"} for key in ["identity", "hq"]}})
    judgment = await get_judge("jev").grade(packet(), context)
    assert judgment.cost.public_usd is None


def test_local_judge_factory_and_entrypoint_collisions(monkeypatch):
    import sys
    import types

    import companybench.judges as judges_module

    module = types.ModuleType("companybench_test_judge")
    module.factory = lambda options: types.SimpleNamespace(
        grade=lambda *a: None, model=options["model"], api_key_env="TEST_API_KEY"
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(judges_module.importlib.metadata, "entry_points", lambda **kwargs: [])
    result = get_judge("companybench_test_judge:factory", {"model": "local-v1"})
    assert result.model == "local-v1"
    plugin = types.SimpleNamespace(name="llm")
    monkeypatch.setattr(judges_module.importlib.metadata, "entry_points", lambda **kwargs: [plugin])
    with pytest.raises(ValueError, match="collision"):
        get_judge("llm")


def test_installed_judge_entrypoint_loads_lazily(monkeypatch):
    import types

    import companybench.judges as judges_module

    loaded = []

    def load():
        loaded.append(True)
        return lambda options: types.SimpleNamespace(
            grade=lambda *a: None, model="plugin-model", api_key_env=""
        )

    plugin = types.SimpleNamespace(name="custom-judge", load=load)
    monkeypatch.setattr(judges_module.importlib.metadata, "entry_points", lambda **kwargs: [plugin])
    get_judge("llm")
    assert loaded == []
    assert get_judge("custom-judge").model == "plugin-model"
    assert loaded == [True]
