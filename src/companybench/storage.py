"""Atomic local artifacts and an append-only record of run events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)

    def artifact(self, relative: str) -> Path:
        target = (self.path / relative).resolve()
        if not target.is_relative_to(self.path):
            raise ValueError("Artifact path must be inside the run directory")
        return target

    def write(self, relative: str, value: Any) -> None:
        target = self.artifact(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = canonical_json(value) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, relative: str, default: Any = None) -> Any:
        target = self.artifact(relative)
        return json.loads(target.read_text()) if target.exists() else default

    def append(self, relative: str, value: Any) -> None:
        target = self.artifact(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size:
            with target.open("rb+") as existing:
                existing.seek(-1, os.SEEK_END)
                if existing.read(1) != b"\n":
                    existing.seek(0)
                    data = existing.read()
                    start = data.rfind(b"\n") + 1
                    try:
                        json.loads(data[start:])
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        existing.truncate(start)
                    else:
                        existing.seek(0, os.SEEK_END)
                        existing.write(b"\n")
                    existing.flush()
                    os.fsync(existing.fileno())
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def read_lines(self, relative: str) -> list[Any]:
        """Read a journal, tolerating only a last line cut short by a crash.

        Paid-operation records remain authoritative if a cost journal's final write
        was interrupted. Malformed earlier lines are an error, not silently discarded.
        """
        path = self.artifact(relative)
        if not path.exists():
            return []
        lines = path.read_bytes().splitlines(keepends=True)
        rows = []
        for index, line in enumerate(lines):
            try:
                rows.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                if index != len(lines) - 1 or line.endswith(b"\n"):
                    raise
        return rows

    def event(self, event: str, **values: Any) -> None:
        self.append("events.jsonl", {"at": now_iso(), "event": event, **values})

    @staticmethod
    def task_file(task_id: str) -> str:
        return f"tasks/{fingerprint(task_id)[:24]}.json"

    def task(self, task_id: str) -> dict[str, Any]:
        return self.read(self.task_file(task_id), {"task_id": task_id, "checkpoints": {}})

    def save_task(self, task_id: str, **values: Any) -> None:
        state = self.task(task_id)
        state.update(values)
        self.write(self.task_file(task_id), state)

    def checkpoint(self, task_id: str, key: str, value: Any) -> None:
        state = self.task(task_id)
        state.setdefault("checkpoints", {})[key] = value
        self.write(self.task_file(task_id), state)

    def operation_file(self, task_id: str, operation: str) -> str:
        return f"operations/{fingerprint(task_id)[:24]}/{fingerprint(operation)[:24]}.json"

    def evidence_view(self, version: str) -> RunStore:
        """Select a retained evidence revision for reporting without changing pointers."""
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise ValueError("Evidence version must be its full SHA-256 identifier")
        metadata = self.read(f"evidence/{version}/metadata.json")
        if not metadata or metadata.get("version") != version:
            raise ValueError("Retained evidence metadata not found for that version")

        class EvidenceView(RunStore):
            def read(self, relative: str, default: Any = None) -> Any:
                if relative == "evidence/current.json":
                    return metadata
                value = super().read(relative, default)
                if relative == "manifest.json" and value:
                    value["evidence_version"] = version
                    value["declared_judges"] = value["settings"]["judges"]
                return value

        return EvidenceView(self.path)

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            with FileLock(str(self.path / ".run.lock"), timeout=0):
                yield
        except Timeout as error:
            raise RuntimeError("Run lock is held by another process") from error
