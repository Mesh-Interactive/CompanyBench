"""Rebuild the site's reproducible synthetic fixture; never call a provider API.

The queries and configurations are real. Every count, price, time, company and
judgment is invented for interface development and must not be published as a
measurement. This fixture intentionally gives different providers different
strengths; Avina is not assigned the highest overall result.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / "src/companybench/data/queries.jsonl"
OUTPUT = ROOT / "website/dist/data/demo.json"

PROFILES = {
    "avina": ("Avina", "Avina", "#7956DE", 0.79, 0.89, 2.3, 48),
    "exa-websets": ("Exa Websets", "Exa", "#278B9B", 0.91, 0.90, 2.5, 78),
    "exa-agent-high": ("Exa Agent High", "Exa", "#1D7180", 0.86, 0.93, 7.4, 155),
    "exa-agent-auto": ("Exa Agent Auto", "Exa", "#49A9B3", 0.83, 0.87, 4.1, 95),
    "parallel-core": ("Parallel Core", "Parallel", "#C27732", 0.86, 0.85, 1.7, 42),
    "parallel-pro": ("Parallel Pro", "Parallel", "#A25921", 0.96, 0.94, 4.0, 91),
    "openai-sol-medium": ("GPT-6.1 Sol", "OpenAI", "#387964", 0.75, 0.90, 3.2, 77),
    "openai-astra-max": ("GPT-6 Astra", "OpenAI", "#23604D", 0.90, 0.95, 7.8, 190),
    "claude-sonnet-medium": ("Claude Sonnet 5.5", "Anthropic", "#C87560", 0.76, 0.91, 3.8, 103),
    "claude-opus-max": ("Claude Opus 5.5", "Anthropic", "#A85241", 0.89, 0.95, 9.2, 230),
    "gemini-flash-high": ("Gemini 3.8 Flash", "Google", "#5684CE", 0.72, 0.84, 0.8, 29),
    "gemini-pro-high": ("Gemini 3.1 Pro", "Google", "#3C61A3", 0.85, 0.92, 4.3, 118),
    "grok-low": ("Grok 4.7 Low", "xAI", "#77838C", 0.73, 0.82, 1.0, 25),
    "grok-xhigh": ("Grok 4.7 xHigh", "xAI", "#4F606E", 0.84, 0.91, 5.0, 108),
}

COLORS = {
    "Avina": "#805AD5",
    "Exa": "#8D6FC3",
    "Parallel": "#E49489",
    "OpenAI": "#718096",
    "Anthropic": "#DC8DAE",
    "Google": "#B37DC7",
    "xAI": "#4A5568",
}

FACET_EXCLUDED = {
    "id",
    "index",
    "query",
    "conditions",
    "rule",
    "acceptance",
    "reference",
    "evidence",
    "pitfalls",
    "gtm_use_case",
    "source_urls",
    "source_ids",
    "fixture",
    "logic",
    "verification_limitations",
}


def random_for(value: str) -> random.Random:
    return random.Random(int(hashlib.sha256(value.encode()).hexdigest()[:16], 16))


def presets() -> dict:
    tree = ast.parse((ROOT / "src/companybench/providers/registry.py").read_text())
    return next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "PRESETS"
    )


def facets(query: dict) -> dict:
    return {
        key: value
        for key, value in query.items()
        if key not in FACET_EXCLUDED
        and (
            isinstance(value, (str, int, float, bool))
            or isinstance(value, list)
            and all(isinstance(x, (str, int, float, bool)) for x in value)
        )
    }


def make_row(query: dict, provider: str) -> dict:
    rng = random_for(f"companybench-site-v1:{query['id']}:{provider}")
    _, _, _, volume, precision, price, seconds = PROFILES[provider]
    complexity = int(query["complexity"][1])
    difficulty = (complexity - 1) * 0.038
    high_effort = provider.endswith(("high", "max", "pro"))
    difficulty *= 0.60 if high_effort else 1.0
    # Different slices intentionally have different strongest systems.
    if query["signal_tags"] and provider == "avina":
        volume += 0.055
        precision += 0.025
    if "technographic" in query.get("primary_category", "") and provider.startswith("exa"):
        precision += 0.025
    if query["industry"] == "Manufacturing" and provider.startswith("parallel"):
        volume += 0.03
    if query["industry"] == "Software" and provider.startswith("openai"):
        precision += 0.02
    exhaustive = query.get("reference", {}).get("exhaustive", False)
    attainable = min(50, len(query.get("reference", {}).get("companies", []))) if exhaustive else 50
    returned = max(0, min(50, round(attainable * (volume - difficulty + rng.uniform(-0.14, 0.10)))))
    if exhaustive:
        returned = min(50, max(1, round(attainable * rng.uniform(0.55, 1.12))))
    status = "completed"
    if rng.random() < 0.011:
        status, returned = "failed", 0
    elif rng.random() < 0.022:
        status = (
            "truncated"
            if provider.startswith(("openai", "claude", "gemini", "grok"))
            else "partial"
        )
        returned = max(1, round(returned * 0.68))
    duplicate = int(returned > 8 and rng.random() < 0.27)
    malformed = int(returned > 10 and rng.random() < 0.12)
    unknown = min(
        returned - duplicate - malformed,
        max(0, round(returned * (0.035 + complexity * 0.01 + rng.uniform(-0.025, 0.025)))),
    )
    unresolved = int(returned > 5 and rng.random() < 0.16)
    decidable = max(0, returned - duplicate - malformed - unknown - unresolved)
    valid = max(
        0, min(attainable, round(decidable * (precision - difficulty + rng.uniform(-0.09, 0.06))))
    )
    categories = ["V"] * valid + ["I"] * (decidable - valid) + ["U"] * unknown + ["N"] * unresolved
    rng.shuffle(categories)
    if duplicate:
        categories.insert(max(1, len(categories) // 3), "D")
    if malformed:
        categories.insert(len(categories) // 2, "M")
    category_map = {"llm": "".join(categories)}
    for judge, strictness in [("decisions", 0.02), ("jev", -0.01)]:
        altered = categories.copy()
        jrng = random_for(f"{query['id']}:{provider}:{judge}")
        for i, value in enumerate(altered):
            if value == "V" and jrng.random() < 0.035 + max(0, strictness):
                altered[i] = "U" if jrng.random() < 0.45 else "I"
            elif value == "I" and jrng.random() < 0.025 - strictness:
                altered[i] = "V"
        if exhaustive:
            for i in reversed(range(len(altered))):
                if altered.count("V") <= attainable:
                    break
                if altered[i] == "V":
                    altered[i] = "I"
        category_map[judge] = "".join(altered)
    time = round(seconds * (1 + 0.10 * (complexity - 1)) * rng.uniform(0.65, 1.55), 1)
    if status == "failed":
        time *= 0.38
    return {
        "query_id": query["id"],
        "provider": provider,
        "trial": 1,
        "requested_count": 50,
        "returned_companies": returned,
        "categories": category_map,
        "cost_usd": round(price * (1 + 0.07 * (complexity - 1)) * rng.uniform(0.70, 1.40), 4),
        "latency_seconds": round(time, 1),
        "search_status": status,
        "search_error": "Synthetic example: provider request failed."
        if status == "failed"
        else None,
        "timing_censored": False,
        "synthetic": True,
    }


def main() -> None:
    queries = [json.loads(line) for line in CATALOGUE.read_text().splitlines() if line.strip()]
    for query in queries:
        query["facets"] = facets(query)
    configured = presets()
    providers = []
    for name, (label, vendor, _color, *_rest) in PROFILES.items():
        options = configured[name]["options"]
        providers.append(
            {
                "id": name,
                "label": label,
                "vendor": vendor,
                "color": COLORS[vendor],
                "model": options.get("model"),
                "effort": options.get("effort", options.get("generator")),
                "configuration": configured[name],
            }
        )
    payload = {
        "meta": {
            "schema_version": "1.0",
            "synthetic": True,
            "title": "CompanyBench · synthetic preview",
            "disclosure": "All performance results, companies, costs, times and judgments are invented. They are not provider measurements or real company claims.",
            "dataset_version": "2.0.0",
            "reference_time": "2026-10-06T00:00:00Z",
            "fixture_seed": "companybench-site-v1",
            "dataset_hash": hashlib.sha256(CATALOGUE.read_bytes()).hexdigest(),
            "requested_count": 50,
            "trials": 1,
            "code_url": "https://github.com/Mesh-Interactive/CompanyBench",
        },
        "queries": queries,
        "providers": providers,
        "judges": [
            {"id": "llm", "label": "LLM judge"},
            {"id": "decisions", "label": "OpenAI Decisions"},
            {"id": "jev", "label": "Jev / TypeSafe"},
        ],
        "rows": [make_row(query, provider) for query in queries for provider in PROFILES],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(
        f"Wrote {len(queries)} real queries and {len(payload['rows'])} invented search rows to {OUTPUT}"
    )


if __name__ == "__main__":
    main()
