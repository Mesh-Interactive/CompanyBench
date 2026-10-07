"""Shared, serializable contracts for providers, datasets, evidence, and judges."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Criterion(BaseModel):
    id: str
    description: str
    evidence_types: list[str] = Field(default_factory=lambda: ["public_text"])


class Rule(BaseModel):
    op: Literal["all", "any", "not", "condition"] = "all"
    condition_id: str | None = None
    children: list[Rule] = Field(default_factory=list)


class ReferenceCompany(BaseModel):
    name: str
    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)


class ReferenceSet(BaseModel):
    companies: list[ReferenceCompany] = Field(default_factory=list)
    exhaustive: bool = False
    source_urls: list[str] = Field(default_factory=list)
    as_of: str | None = None
    notes: str = ""


class Query(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    index: int = Field(ge=1)
    query: str
    family: str
    complexity: str
    industry: str
    geography: str = "Any"
    geography_basis: str = ""
    size_band: str = "Any"
    business_model: str = "Any"
    filter_tags: list[str] = Field(default_factory=list)
    signal_tags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    time_window: str = ""
    acceptance: str = ""
    conditions: list[Criterion]
    rule: Rule | None = None
    company_unit: str = (
        "operating business; subsidiaries count separately only if independently operated"
    )
    evidence: str = ""
    verification_methods: list[str] = Field(default_factory=lambda: ["public_text"])
    observability: str = "public evidence may be incomplete"
    expected_count: str = "unknown"
    satisfiability: str = "unverified"
    pitfalls: str = ""
    gtm_use_case: str = ""
    source_basis: str = "original"
    source_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    reference: ReferenceSet | None = None

    @model_validator(mode="after")
    def validate_conditions(self) -> Query:
        ids = [condition.id for condition in self.conditions]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("Each query needs uniquely named acceptance conditions")

        def visit(rule: Rule) -> None:
            if rule.op == "condition":
                if rule.condition_id not in ids or rule.children:
                    raise ValueError("Condition rules must refer to a defined condition")
            elif rule.op == "not":
                if len(rule.children) != 1 or rule.condition_id:
                    raise ValueError("NOT requires exactly one child")
            elif not rule.children or rule.condition_id:
                raise ValueError("AND/OR require child rules")
            for child in rule.children:
                visit(child)

        if self.rule:
            visit(self.rule)
        return self


class SearchRequest(BaseModel):
    query: Query
    target_count: int = Field(default=50, ge=1)
    reference_time: datetime
    task_id: str = ""
    trial: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def normalize_time(self) -> SearchRequest:
        if self.reference_time.tzinfo is None:
            raise ValueError("Reference time must include a timezone")
        self.reference_time = self.reference_time.astimezone(UTC)
        return self


class Usage(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    search_requests: int = Field(default=0, ge=0)
    units: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Cost(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    public_usd: float | None = Field(default=None, ge=0)
    account_usd: float | None = Field(default=None, ge=0)
    confirmed_usd: float | None = Field(default=None, ge=0)
    basis: str = "unknown"
    source_urls: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class CompanyCandidate(BaseModel):
    position: int = Field(ge=1)
    name: str | None = None
    domain: str | None = None
    website: str | None = None
    citations: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)
    native_id: str | None = None
    malformed: bool = False
    raw: Any = None


class SearchResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    provider: str
    candidates: list[CompanyCandidate] = Field(default_factory=list)
    status: Literal["completed", "partial", "failed", "refused", "truncated"] = "completed"
    usage: Usage = Field(default_factory=Usage)
    cost: Cost = Field(default_factory=Cost)
    latency_seconds: float | None = Field(default=None, ge=0)
    timing_censored: bool = False
    model: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: Any = None


class SearchJob(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    handles: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    result: SearchResult | None = None
    error: str | None = None


class CompanyIdentity(BaseModel):
    id: str
    name: str
    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)
    resolved: bool = True
    notes: str = ""


class EvidenceSource(BaseModel):
    id: str
    url: str
    text: str = ""
    title: str = ""
    fetched_at: datetime | None = None
    published_at: str | None = None
    method: str = "public_text"
    error: str | None = None
    redistributable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidencePacket(BaseModel):
    query: Query
    company: CompanyIdentity
    reference_time: datetime
    sources: list[EvidenceSource] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    omitted: list[str] = Field(default_factory=list)
    evidence_version: str = ""


class CriterionJudgment(BaseModel):
    criterion_id: str
    verdict: Literal["met", "failed", "unknown"]
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None


class CompanyJudgment(BaseModel):
    query_id: str
    entity_id: str
    judge: str
    verdict: Literal["valid", "invalid", "unknown", "error"]
    conditions: list[CriterionJudgment] = Field(default_factory=list)
    error: str | None = None
    packet_hash: str = ""
    rubric_hash: str = ""
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    cost: Cost = Field(default_factory=Cost)
    raw: Any = None
