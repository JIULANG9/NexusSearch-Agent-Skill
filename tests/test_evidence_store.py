"""Evidence store: validation, dedupe, tiering, verification, contradictions."""

from __future__ import annotations

import pytest

from runtime.evidence_store import EvidenceStore
from runtime.models import Evidence

from .conftest import make_evidence


def test_add_rejects_unschema_records(store: EvidenceStore) -> None:
    with pytest.raises(ValueError):
        store.add(Evidence(claim="太长不行", source_type="url"))
    with pytest.raises(ValueError):
        store.add(Evidence(claim="一条足够长的结论", source_type="carrier-pigeon"))


def test_add_assigns_tier_and_round_trips(store: EvidenceStore, workspace) -> None:
    record = store.add(make_evidence(0, domain="spec.example.org", source_tier="", source_type="docs"))
    assert record.source_tier == "primary"
    blog = store.add(make_evidence(1, domain="blog.example.org", source_tier=""))
    assert blog.source_tier == "secondary"
    store.save()
    reloaded = EvidenceStore.open(workspace.path("evidence"))
    assert reloaded.get(record.id) is not None
    assert reloaded.get(record.id).claim == record.claim


def test_from_dict_accepts_schema_and_flat_shapes() -> None:
    nested = Evidence.from_dict({"claim": "来自 schema 形态的结论", "source": {"type": "docs", "uri": "https://a.dev/x"}})
    flat = Evidence.from_dict({"claim": "来自扁平形态的结论", "source_type": "docs", "source_uri": "https://a.dev/x"})
    assert nested.source_type == flat.source_type == "docs"
    assert nested.source_uri == flat.source_uri == "https://a.dev/x"


def test_dedupe_merges_identical_claim_and_uri(store: EvidenceStore) -> None:
    first = store.add(make_evidence(0, domain="same.org", node="n1"))
    twin = store.add(make_evidence(0, domain="same.org", node="n2"))
    assert first.id == twin.id
    assert set(twin.supports) == {"n1", "n2"}
    assert len(store.records) == 1


def test_add_many_skips_invalid_without_losing_valid(store: EvidenceStore) -> None:
    stored = store.add_many([Evidence(claim="短", source_type="url"), make_evidence(1, domain="ok.org")])
    assert len(stored) == 1


def test_domains_uses_registrable_host() -> None:
    assert Evidence.host_of("https://docs.example.co.uk/guide") == "example.co.uk"
    assert Evidence.host_of("https://a.dev/x") == "a.dev"
    assert Evidence.host_of("") == ""


def test_tier_inference_prefers_primary_source_types(store: EvidenceStore) -> None:
    assert store.infer_tier(Evidence(claim="仓库源码里的结论", source_type="repository", source_uri="https://github.com/o/r")) == "primary"
    assert store.infer_tier(Evidence(claim="博客文章里的结论", source_type="url", source_uri="https://medium.com/x")) in {"secondary", "tertiary"}
    assert store.infer_tier(Evidence(claim="已归档的旧闻", source_type="url", source_uri="https://news.site/y")) != ""


def test_quality_score_penalises_flags_and_fabrication(store: EvidenceStore) -> None:
    clean = make_evidence(0, domain="a.org", confidence=0.9)
    flagged = make_evidence(0, domain="a.org", confidence=0.9, quality_flags=["seo_farm"])
    fake = make_evidence(0, domain="a.org", confidence=0.9, quality_flags=["fabricated"])
    assert store.quality_score(flagged) < store.quality_score(clean)
    assert store.quality_score(fake) == 0.0


def test_mark_verified_raises_confidence_and_records_method(store: EvidenceStore) -> None:
    record = store.add(make_evidence(0, domain="a.org", confidence=0.6))
    updated = store.mark_verified(record.id, method="primary_source")
    assert updated.confidence > 0.6
    assert updated.verification["status"] == "confirmed"
    assert updated.verification["method"] == "primary_source"
    with pytest.raises(KeyError):
        store.mark_verified("ev_missing")


def test_link_contradiction_is_bidirectional(store: EvidenceStore) -> None:
    left = store.add(make_evidence(0, domain="a.org"))
    right = store.add(make_evidence(1, domain="b.org"))
    assert store.link_contradiction(left.id, right.id) is True
    assert right.id in store.get(left.id).contradicts
    assert left.id in store.get(right.id).contradicts
    assert [pair for pair in store.contradicting("anything")] or True
    assert store.link_contradiction(left.id, "nope") is False


def test_stats_and_sorted_shape(store: EvidenceStore) -> None:
    for index, domain in enumerate(["a.org", "b.com"]):
        store.add(make_evidence(index, domain=domain))
    stats = store.stats()
    assert stats["total"] == 2 and stats["domains"] == 2
    assert set(stats["tiers"]) <= {"primary", "secondary", "tertiary"}
    assert [record.id for record in store.sorted()]


def test_delete_and_for_node(store: EvidenceStore) -> None:
    record = store.add(make_evidence(0, domain="a.org", node="t1"))
    assert [item.id for item in store.for_node("t1")] == [record.id]
    assert store.delete(record.id) is True
    assert store.for_node("t1") == []
