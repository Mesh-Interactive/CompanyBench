"""Explicit run settings and TOML configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    providers: list[str] = Field(default_factory=list)
    judges: list[str] = Field(default_factory=lambda: ["llm"])
    target_count: int = Field(default=50, ge=1)
    trials: int = Field(default=1, ge=1)
    concurrency: int = Field(default=4, ge=1)
    provider_concurrency: int = Field(default=2, ge=1)
    poll_interval: float = Field(default=5, ge=0)
    timeout: float = Field(default=7200, gt=0)
    seed: int = 42
    budget_usd: float | None = Field(default=None, ge=0)
    provider_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    judge_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    research: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_options(self) -> Settings:
        if len(self.providers) != len(set(self.providers)) or len(self.judges) != len(
            set(self.judges)
        ):
            raise ValueError("Providers and judges cannot be repeated")

        def check(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if str(key).casefold() in {
                        "api_key",
                        "apikey",
                        "token",
                        "access_token",
                        "password",
                        "authorization",
                    }:
                        raise ValueError(
                            "Put credentials in environment variables, not saved configuration"
                        )
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        for options in [
            *self.provider_options.values(),
            *self.judge_options.values(),
            self.research,
        ]:
            check(options)
        return self


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        return Settings()
    data = tomllib.loads(path.read_text())
    allowed = {"run", "providers", "judges", "research"}
    if data.keys() - allowed:
        raise ValueError(f"Unknown configuration sections: {sorted(data.keys() - allowed)}")
    return Settings(
        **data.get("run", {}),
        provider_options=data.get("providers", {}),
        judge_options=data.get("judges", {}),
        research=data.get("research", {}),
    )
