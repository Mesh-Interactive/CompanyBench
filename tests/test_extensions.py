"""Trusted file factories share import semantics across every extension surface."""

import json
import os
from types import SimpleNamespace

from typer.testing import CliRunner

from companybench.cli import app
from companybench.datasets import load_dataset
from companybench.extensions import load_factory
from companybench.judges import get_judge, judge_names


def test_python_dataset_supports_dataclasses_with_postponed_annotations(tmp_path):
    path = tmp_path / "queries.py"
    path.write_text("""from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Seed:
    name: str = "CUSTOM-1"

def cases():
    seed = Seed()
    return [{"id": seed.name, "index": 1, "query": "Find manufacturers",
        "family": "firmographic", "complexity": "L1", "industry": "Manufacturing",
        "acceptance": "Company manufactures products",
        "conditions": [{"id": "fit", "description": "Manufactures products"}]}]
""")
    assert load_dataset(f"{path}:cases")[0].id == "CUSTOM-1"


def test_local_judge_file_factory_receives_options_dictionary(tmp_path):
    path = tmp_path / "judge.py"
    path.write_text("""from __future__ import annotations
from dataclasses import dataclass

@dataclass
class LocalJudge:
    model: str
    api_key_env: str = ""

    async def grade(self, packet, context):
        raise NotImplementedError

def create(*, options):
    return LocalJudge(model=options["model"])
""")
    judge = get_judge(f"{path}:create", {"model": "local-version"})
    assert judge.model == "local-version"
    assert type(judge).__module__.startswith("companybench_extension_")


def test_file_factory_identity_changes_with_source_bytes_even_with_unchanged_stat(tmp_path):
    path = tmp_path / "extension.py"
    path.write_text('def create():\n    return "version-one"\n')
    first = load_factory(f"{path}:create")
    initial = path.stat()
    path.write_text('def create():\n    return "version-two"\n')
    os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
    second = load_factory(f"{path}:create")
    assert first.__module__ != second.__module__
    assert first() == "version-one"
    assert second() == "version-two"


def test_judge_discovery_and_cli_do_not_load_installed_plugins(monkeypatch):
    import companybench.judges as judges

    def fail_if_loaded():
        raise AssertionError("Listing must not execute a plugin")

    plugin = SimpleNamespace(name="custom-judge", load=fail_if_loaded)
    monkeypatch.setattr(judges.importlib.metadata, "entry_points", lambda **kwargs: [plugin])
    assert "custom-judge" in judge_names()
    result = CliRunner().invoke(app, ["judges"])
    assert result.exit_code == 0
    row = next(row for row in json.loads(result.stdout) if row["name"] == "custom-judge")
    assert row["readiness"] == "not loaded"
