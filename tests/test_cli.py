"""Exercise the real command surface without keys, HTTP, or paid operations."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from companybench.cli import app
from companybench.config import Settings
from companybench.datasets import load_dataset

runner = CliRunner()
ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for variable in (
        "AVINA_API_KEY",
        "EXA_API_KEY",
        "PARALLEL_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "XAI_API_KEY",
        "JEV_API_KEY",
        "TYPESAFE_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_full_dry_run_selects_14_configurations_without_credentials():
    result = runner.invoke(
        app, ["run", "--config", str(ROOT / "configs/full-comparison.toml"), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)["plan"]
    assert plan["queries"] == 250
    assert plan["configurations"] == 14
    assert plan["search_tasks"] == 3500
    assert plan["possible_company_positions"] == 175000


def test_cli_inclusive_range_and_intersecting_selection():
    result = runner.invoke(app, ["queries", "--range", "100:150"])
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)) == 51
    result = runner.invoke(app, ["queries", "--where", "index >= 100", "--indices", "[12,101,142]"])
    assert result.exit_code == 0
    assert [row["index"] for row in json.loads(result.stdout)] == [101, 142]


def test_paid_search_preflight_checks_only_selected_search_key(tmp_path):
    result = runner.invoke(
        app,
        ["search", "--provider", "exa-websets", "--sample", "1", "--output", str(tmp_path / "run")],
    )
    assert result.exit_code == 1
    assert "EXA_API_KEY" in result.output
    assert "AVINA_API_KEY" not in result.output
    assert "OPENAI_API_KEY" not in result.output
    assert not (tmp_path / "run/manifest.json").exists()


def test_offline_demo_reports_and_review_exports(tmp_path):
    destination = tmp_path / "demo"
    result = runner.invoke(app, ["demo", "--output", str(destination)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "report",
            str(destination),
            "--segment",
            "simple",
            "--prefix",
            "10",
            "--output",
            str(tmp_path / "report"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report/report.html").exists()
    export = tmp_path / "review.jsonl"
    result = runner.invoke(app, ["export", str(export), "--review", str(destination)])
    assert result.exit_code == 0, result.output
    assert all(not row["reviewed"] for row in map(json.loads, export.read_text().splitlines()))
    identities = tmp_path / "identities.json"
    result = runner.invoke(app, ["export", str(identities), "--identity-review", str(destination)])
    assert result.exit_code == 0, result.output
    assert "overrides" in json.loads(identities.read_text())


def test_examples_are_executable_contracts(tmp_path):
    dataset = str(ROOT / "examples/queries.py") + ":create_queries"
    provider = str(ROOT / "examples/provider.py") + ":create"
    assert len(load_dataset(dataset)) == 1
    result = runner.invoke(
        app,
        [
            "search",
            "--provider",
            provider,
            "--dataset",
            dataset,
            "--output",
            str(tmp_path / "example"),
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["resume", str(tmp_path / "example"), "--search-only"])
    assert result.exit_code == 0, result.output


def test_configuration_rejects_nested_credentials_and_nonfinite_budget():
    with pytest.raises(ValueError, match="environment"):
        Settings(provider_options={"avina": {"headers": {"Authorization": "private"}}})
    with pytest.raises(ValueError):
        Settings(budget_usd=float("nan"))
