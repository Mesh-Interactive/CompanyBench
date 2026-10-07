import json

from companybench.report import html_report, redact


def test_redaction_removes_nested_secrets_identifiers_and_raw_payloads_but_keeps_costs():
    data = {
        "provider": "openai",
        "model": "gpt-6-astra",
        "cost": {"public_usd": 1.2, "account_usd": 0.8, "confirmed_usd": 0.8},
        "usage": {"input_tokens": 100, "output_tokens": 30, "search_requests": 2},
        "metadata": {
            "OPENAI_API_KEY": "secret-1",
            "workspaceId": "ws1",
            "BillingAccountId": "ba1",
            "clientSecret": "secret-2",
            "authorization": "Bearer abc",
            "artifact_directory": "/private/run",
        },
        "rows": [
            {
                "raw": {"all_the_secret_things": "secret-3"},
                "conditions": [],
                "judge": "llm",
                "packet_hash": "hash",
                "raw_response": "secret-4",
            }
        ],
        "source": {"body_base64": "cHJpdmF0ZQ==", "id": "s1"},
    }
    result = redact(data)
    serialized = json.dumps(result)
    assert "secret-" not in serialized
    assert "ws1" not in serialized
    assert "ba1" not in serialized
    assert "/private" not in serialized
    assert "body_base64" not in serialized
    assert result["cost"] == data["cost"]
    assert result["usage"] == data["usage"]
    assert result["rows"][0]["packet_hash"] == "hash"
    assert data["metadata"]["OPENAI_API_KEY"] == "secret-1"


def test_judge_raw_removed_without_losing_identity_assessment():
    result = redact(
        {
            "judge": "llm",
            "conditions": [],
            "packet_hash": "hash",
            "raw": {
                "response": {"output": [{"secret": "no"}]},
                "identity_judgment": {
                    "verdict": "met",
                    "evidence_ids": ["s1"],
                    "reason": "Identity matches",
                },
            },
        }
    )
    assert "raw" not in result
    assert result["identity_judgment"]["evidence_ids"] == ["s1"]
    assert "response" not in result


def test_credentials_inside_signed_urls_and_errors_are_redacted():
    data = {
        "url": "https://user:password@example.com/report.pdf?X-Amz-Signature=signature-secret&format=pdf",
        "error": "HTTP failed Authorization: Bearer sensitive-secret",
        "nested": {"reason": "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"},
    }
    cleaned = redact(data)
    serialized = json.dumps(cleaned)
    assert "password" not in serialized
    assert "signature-secret" not in serialized
    assert "sensitive-secret" not in serialized
    assert "abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "format=pdf" in cleaned["url"]
    assert "example.com/report.pdf" in cleaned["url"]


def test_html_embedded_data_cannot_break_out_of_inert_script():
    payload = '</script><script>alert("owned")</script><img src=x onerror=alert(1)>'
    output = html_report({"probe": payload})
    assert payload not in output
    assert "\\u003c/script\\u003e" in output
    # Decode only the inert JSON, proving escaping preserves the original data.
    text = output.split('<script type="application/json" id="data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(text)["probe"] == payload


def test_publication_bundle_omits_raw_judge_and_unlicensed_evidence(tmp_path):
    from companybench.report import publish_bundle
    from companybench.storage import RunStore

    store = RunStore(tmp_path / "run")
    store.write("manifest.json", {"tasks": [], "workspaceId": "private-workspace"})
    report = {
        "comparison_complete": True,
        "selected_queries": [],
        "evidence": [
            {
                "query": {"id": "q"},
                "sources": [
                    {
                        "id": "s1",
                        "url": "https://example.com",
                        "text": "Private captured quotation",
                        "redistributable": False,
                    }
                ],
            }
        ],
        "rows": [
            {
                "judge": "llm",
                "conditions": [],
                "packet_hash": "hash",
                "raw": {
                    "response": {"sensitive": "NATIVE_PAYLOAD"},
                    "identity_judgment": {"verdict": "met", "evidence_ids": ["s1"]},
                },
            }
        ],
    }
    target = tmp_path / "publication"
    publish_bundle(store, report, target)
    public = (target / "report.json").read_text()
    assert "NATIVE_PAYLOAD" not in public
    assert "Private captured quotation" not in public
    assert "text_sha256" in public
    assert "identity_judgment" in public
    assert "private-workspace" not in (target / "manifest.json").read_text()
    assert "NATIVE_PAYLOAD" not in (target / "report.html").read_text()
