from datetime import UTC, datetime

import pytest

from companybench.evidence import (
    bound_packet,
    extract_html,
    normalize_identity,
    packet_hash,
    validate_public_url,
)
from companybench.models import (
    CompanyCandidate,
    CompanyIdentity,
    Criterion,
    EvidencePacket,
    EvidenceSource,
    Query,
)


def packet():
    return EvidencePacket(
        query=Query(
            id="q1",
            index=1,
            query="UK firms",
            family="Firmographics",
            complexity="L1",
            industry="Any",
            conditions=[Criterion(id="hq", description="Headquartered in the UK")],
        ),
        company=CompanyIdentity(id="co1", name="Example", domain="example.com"),
        reference_time=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_identity_is_not_domain_only_or_name_only():
    a = normalize_identity(
        CompanyCandidate(position=1, name="Alpha", domain="www.example.com"), "q1"
    )
    same = normalize_identity(
        CompanyCandidate(position=2, name="Alpha", domain="https://example.com/"), "q1"
    )
    brand = normalize_identity(
        CompanyCandidate(position=3, name="Beta", domain="example.com"), "q1"
    )
    elsewhere = normalize_identity(
        CompanyCandidate(position=4, name="Alpha", domain="other.com"), "q1"
    )
    assert a.id == same.id
    assert len({a.id, brand.id, elsewhere.id}) == 3
    missing_a = normalize_identity(
        CompanyCandidate(position=1, name="Alpha"), "q1", unique_key="p1:1"
    )
    missing_b = normalize_identity(
        CompanyCandidate(position=1, name="Alpha"), "q1", unique_key="p2:1"
    )
    assert missing_a.id != missing_b.id
    assert not missing_a.resolved


def test_packet_hash_and_bounds_freeze_sources_without_mutating_input():
    p = packet()
    p.sources = [
        EvidenceSource(id=f"s{i}", url=f"https://example.com/{i}", text="x" * 10000)
        for i in range(12)
    ]
    bounded = bound_packet(p)
    assert len(bounded.sources) == 8
    assert bounded.omitted
    assert len(p.sources) == 12
    assert bounded.evidence_version == packet_hash(bounded)
    changed = bounded.model_copy(deep=True)
    changed.sources[0].text += "changed"
    assert packet_hash(changed) != bounded.evidence_version


def test_live_html_preserves_technology_markers_without_claiming_crm_use():
    title, body = extract_html(
        '<title>A &amp; B</title><script src="https://js.hs-scripts.com/123.js"></script><script>window.secret="hidden"</script><main>Manufacturer</main>'
    )
    assert title == "A & B"
    assert "Manufacturer" in body
    assert "js.hs-scripts.com/123.js" in body
    assert "hidden" not in body
    assert "CRM" not in body


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "https://user:pass@example.com",
        "file:///etc/passwd",
        "https://[::1]/",
        "http://example.com:8080",
    ],
)
def test_fetch_rejects_nonpublic_or_unsafe_destinations(url):
    with pytest.raises(ValueError):
        validate_public_url(
            url, resolver=lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))]
        )


def test_fetch_rejects_mixed_public_private_dns_answers():
    with pytest.raises(ValueError):
        validate_public_url(
            "https://example.com",
            resolver=lambda *a, **k: [
                (2, 1, 6, "", ("93.184.216.34", 443)),
                (2, 1, 6, "", ("10.0.0.1", 443)),
            ],
        )


def test_prompt_context_excludes_gold_answers_and_source_provenance():
    from companybench.evidence import packet_context
    from companybench.models import ReferenceCompany, ReferenceSet

    p = packet()
    p.query.reference = ReferenceSet(
        companies=[ReferenceCompany(name="SECRET GOLD")], exhaustive=True
    )
    p.query.source_urls = ["https://competitor.example/inspiration"]
    data = str(packet_context(p))
    assert "SECRET GOLD" not in data
    assert "competitor.example" not in data


@pytest.mark.asyncio
async def test_research_fetches_evidence_not_model_prose_and_reuses_frozen_packet(monkeypatch):
    import companybench.evidence as evidence_module
    from companybench.evidence import OpenAIResearcher

    class Context:
        def __init__(self):
            self.saved = {}
            self.calls = 0

        def load(self, key):
            return self.saved.get(key)

        def checkpoint(self, key, value):
            self.saved[key] = value

        async def post(self, url, **kwargs):
            self.calls += 1
            assert kwargs["json"]["max_tool_calls"] == 5
            return {
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "output": [
                    {
                        "type": "web_search_call",
                        "action": {"sources": [{"url": "https://example.com/about"}]},
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"urls":["https://example.com/about"],"claim":"unverified model assertion"}',
                            }
                        ],
                    },
                ],
            }

    async def fetch(url):
        return EvidenceSource(id="s" + str(len(url)), url=url, text="Actual fetched page")

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(evidence_module, "fetch_public_source", fetch)
    context = Context()
    researcher = OpenAIResearcher()
    first, usage, cost = await researcher.research(packet(), [], context)
    second, _, _ = await researcher.research(packet(), [], context)
    assert first == second
    assert context.calls == 1
    assert all(s.text == "Actual fetched page" for s in first.sources)
    assert usage.search_requests == 1
    assert cost.public_usd > 0


def test_redirect_to_private_host_is_rejected_before_second_connection(monkeypatch):
    import companybench.evidence as evidence_module

    connected = []

    def validate(url):
        if "private" in url:
            raise ValueError("non-public address")
        return "http", "example.com", 80, "93.184.216.34", "/"

    class Response:
        status = 302

        def getheader(self, name):
            return "http://private/"

    class Connection:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(evidence_module, "validate_public_url", validate)
    monkeypatch.setattr(evidence_module.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(
        evidence_module.socket, "create_connection", lambda target, **k: connected.append(target)
    )
    with pytest.raises(ValueError, match="non-public"):
        evidence_module._fetch_public("http://example.com", timeout=1, max_bytes=100)
    assert connected == [("93.184.216.34", 80)]


def test_legal_suffix_variations_collapse_only_with_same_hostname():
    identities = [
        normalize_identity(CompanyCandidate(position=i + 1, name=name, domain="acme.com"), "q")
        for i, name in enumerate(["Acme", "Acme Inc.", "Acme, LLC", "Acme Limited", "Acme GmbH"])
    ]
    assert len({identity.id for identity in identities}) == 1
    assert identities[1].aliases == ["Acme Inc."]
    different = normalize_identity(
        CompanyCandidate(position=6, name="Acme Manufacturing Inc.", domain="acme.com"), "q"
    )
    assert different.id != identities[0].id
    another_domain = normalize_identity(
        CompanyCandidate(position=7, name="Acme Inc.", domain="acme.example"), "q"
    )
    assert another_domain.id != identities[0].id


@pytest.mark.asyncio
async def test_global_evidence_revision_does_not_masquerade_as_packet_hash(monkeypatch):
    from test_judges import Context

    from companybench.judges import get_judge

    monkeypatch.setenv("JEV_API_KEY", "test")
    p = bound_packet(packet())
    p.evidence_version = "whole-cohort-version"
    result = await get_judge("jev").grade(
        p, Context({"answers": {"identity": {"choice": "unknown"}, "hq": {"choice": "unknown"}}})
    )
    assert result.verdict == "unknown"
    assert result.packet_hash == packet_hash(p)


def test_relevant_excerpts_find_late_conditions_and_keep_offsets():
    from companybench.evidence import select_excerpts

    p = packet()
    content = (
        ("unrelated introductory text " * 400)
        + "Example is headquartered in the UK."
        + (" filler " * 300)
    )
    source = EvidenceSource(id="s", url="https://example.com", text=content)
    selected = select_excerpts(source, p)
    assert "headquartered in the UK" in selected.text
    assert selected.metadata["full_text_characters"] == len(content)
    assert selected.metadata["excerpt_offsets"]
    assert len(selected.text) <= 2400
    assert source.text == content


def test_pdf_text_extraction_covers_late_certificate_page():
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from companybench.evidence import extract_pdf

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (Example R2 certificate valid through 2027) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    body = BytesIO()
    writer.write(body)
    _, text, metadata = extract_pdf(body.getvalue())
    assert "Example R2 certificate" in text
    assert "[PDF page 2]" in text
    assert metadata["pdf_pages_extracted"] == 2


@pytest.mark.asyncio
async def test_source_cache_deduplicates_concurrent_fetches_and_refetches_new_revision(
    tmp_path, monkeypatch
):
    import asyncio
    import types

    import companybench.evidence as evidence_module
    from companybench.storage import RunStore

    calls = []

    async def fetch(url, *, capture=None):
        calls.append(url)
        await asyncio.sleep(0)
        if capture is not None:
            capture["body_base64"] = "dGV4dA=="
        return EvidenceSource(id="s", url=url, text="Company text")

    monkeypatch.setattr(evidence_module, "fetch_public_source", fetch)
    store = RunStore(tmp_path / "run")
    contexts = [types.SimpleNamespace(store=store, task_id=f"research:1:q:{n}") for n in range(3)]
    researcher = evidence_module.OpenAIResearcher()
    sources = await asyncio.gather(
        *(
            researcher._fetch_cached("https://example.com", packet(), context)
            for context in contexts
        )
    )
    assert len(calls) == 1
    assert sources[0] == sources[1] == sources[2]
    await researcher._fetch_cached("https://example.com", packet(), contexts[0])
    assert len(calls) == 1
    await researcher._fetch_cached(
        "https://example.com",
        packet(),
        types.SimpleNamespace(store=store, task_id="research:2:q:0"),
    )
    assert len(calls) == 2
    assert len(list(store.artifact("evidence/source_cache").glob("*.json"))) == 2
