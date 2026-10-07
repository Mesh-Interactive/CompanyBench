"""Small HTTP adapter helpers. Native lists retain their order and duplicates."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit

from companybench.models import CompanyCandidate, SearchResult, Usage

ResultStatus = Literal["completed", "partial", "failed", "refused", "truncated"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TOKEN_OPTIONS = {
    "model",
    "effort",
    "max_output_tokens",
    "timeout",
    "public_token_rates",
    "price_date",
}
CREDIT_OPTIONS = {
    "public_monthly_usd",
    "public_included_credits",
    "account_credit_price",
    "account_usd_per_credit",
}

COMPANY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "domain": {"type": ["string", "null"]},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "domain", "citations", "evidence"],
            },
        }
    },
    "required": ["companies"],
}


class HTTPProvider:
    """Configuration shared by small direct-HTTP adapters; no SDK dependencies."""

    name: str
    api_key_env: str
    default_base_url: str
    allowed_options: set[str] = set()
    allowed_efforts: set[str] | None = None

    def __init__(self, name: str | None = None, **options: Any) -> None:
        if name:
            self.name = name
        unknown = (
            options.keys()
            - self.allowed_options
            - {"base_url", "api_key", "api_key_env", "account_cost_usd"}
        )
        if unknown:
            raise ValueError(f"Unsupported {self.name} options: {', '.join(sorted(unknown))}")
        if (
            "effort" in options
            and self.allowed_efforts is not None
            and options["effort"] not in self.allowed_efforts
        ):
            raise ValueError(f"Unsupported {self.name} effort: {options['effort']!r}")
        if "model" in options and (
            not isinstance(options["model"], str) or not options["model"].strip()
        ):
            raise ValueError("model must be a nonempty model identifier")
        for key in {
            "max_output_tokens",
            "max_uses",
            "max_tool_calls",
            "max_continuations",
        } & options.keys():
            value = options[key]
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < (0 if key == "max_continuations" else 1)
            ):
                raise ValueError(
                    f"{key} must be {'nonnegative' if key == 'max_continuations' else 'positive'} integer"
                )
        for key in {
            "timeout",
            "account_cost_usd",
            "account_credit_price",
            "account_usd_per_credit",
            "public_monthly_usd",
            "public_included_credits",
        } & options.keys():
            value = options[key]
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or (value == 0 and key in {"timeout", "public_included_credits"})
            ):
                raise ValueError(
                    f"Invalid {key}: expected a finite {'positive' if key in {'timeout', 'public_included_credits'} else 'nonnegative'} amount"
                )
        if "public_token_rates" in options:
            rates = options["public_token_rates"]
            if (
                not isinstance(rates, (list, tuple))
                or len(rates) != 3
                or any(
                    not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in rates
                )
            ):
                raise ValueError(
                    "public_token_rates requires three nonnegative finite input/output/cached-input rates"
                )
        if "price_date" in options:
            date.fromisoformat(str(options["price_date"]))
        if "api_key_env" in options and (
            not isinstance(options["api_key_env"], str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", options["api_key_env"])
        ):
            raise ValueError("api_key_env must be an environment-variable name")
        self.options = options
        if options.get("api_key_env"):
            self.api_key_env = str(options["api_key_env"])
        self.base_url = str(options.get("base_url", self.default_base_url)).rstrip("/")

    def headers(self, key_header: str = "Authorization") -> dict[str, str]:
        key = self.options.get("api_key") or os.environ.get(self.api_key_env)
        if not key:
            raise ValueError(f"{self.name} requires {self.api_key_env}")
        value = f"Bearer {key}" if key_header == "Authorization" else str(key)
        return {key_header: value, "Content-Type": "application/json"}


def domain_from_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = urlsplit(text if "://" in text else "https://" + text)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        host = parsed.hostname.lower().removeprefix("www.") if parsed.hostname else None
        if host is None or re.search(r"\s", host) or "." not in host:
            return None
        return host
    except ValueError:
        return None


def citation_urls(value: Any) -> list[str]:
    """Extract URL fields from native citation objects without inventing evidence."""
    found: list[str] = []
    if isinstance(value, str):
        if value.startswith(("https://", "http://")):
            found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(citation_urls(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "url",
                "uri",
                "citations",
                "sources",
                "basis",
                "grounding",
                "annotations",
                "references",
            }:
                found.extend(citation_urls(item))
    return list(dict.fromkeys(found))


def candidates_from_payload(value: Any) -> list[CompanyCandidate]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        value = json.loads(text)
    if isinstance(value, dict):
        value = value.get("companies")
    if not isinstance(value, list):
        raise ValueError("Provider output must contain a companies array")
    candidates = []
    for position, row in enumerate(value, 1):
        if not isinstance(row, dict):
            candidates.append(CompanyCandidate(position=position, malformed=True, raw=row))
            continue
        name = row.get("name")
        domain = row.get("domain") or row.get("website") or row.get("url")
        website = row.get("website") or row.get("url")
        clean_domain = domain_from_url(domain)
        candidates.append(
            CompanyCandidate(
                position=position,
                name=name if isinstance(name, str) else None,
                domain=clean_domain,
                website=website if isinstance(website, str) else None,
                citations=citation_urls(row.get("citations", [])),
                claims={
                    k: v
                    for k, v in row.items()
                    if k not in {"name", "domain", "website", "url", "citations"}
                },
                native_id=str(row["id"]) if row.get("id") else None,
                # A name-only company remains resolvable by independent evidence.
                malformed=not (isinstance(name, str) and name.strip() or clean_domain),
                raw=row,
            )
        )
    return candidates


def parsed_result(
    name: str,
    payload: Any,
    *,
    usage: Usage,
    raw: Any,
    model: str | None = None,
    status: ResultStatus = "completed",
) -> SearchResult:
    try:
        candidates = candidates_from_payload(payload)
        return SearchResult(
            provider=name, candidates=candidates, usage=usage, model=model, status=status, raw=raw
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return SearchResult(
            provider=name,
            status=status if status in {"truncated", "refused"} else "failed",
            usage=usage,
            model=model,
            error=f"Output parsing failed: {exc}",
            raw=raw,
        )


def add_usage(total: Usage, value: Usage) -> Usage:
    """Aggregate native requests; inclusive output totals already contain thinking."""
    total.input_tokens += value.input_tokens
    total.output_tokens += value.output_tokens
    total.cached_input_tokens += value.cached_input_tokens
    total.search_requests += value.search_requests
    for key, amount in value.units.items():
        total.units[key] = total.units.get(key, 0) + amount
    for key in ("tokens_reported", "searches_reported"):
        total.metadata[key] = total.metadata.get(key, True) and value.metadata.get(key, False)
    total.metadata.setdefault("requests", []).append(value.model_dump())
    return total
