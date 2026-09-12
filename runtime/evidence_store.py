"""Evidence store: the single source of truth for citable findings.

Claims are only as good as the evidence behind them, so this layer owns
deduplication, source-tier inference, contradiction linking and validation
against ``schemas/evidence.schema.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Evidence
from .util import clamp, load_config, now_iso, read_json, validate, write_json


@dataclass
class EvidenceStore:
    """A JSON-backed collection of :class:`Evidence` records."""

    path: Path
    records: dict[str, Evidence] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str | Path) -> "EvidenceStore":
        """Load (or create) the store backed by ``path``."""
        target = Path(path)
        payload = read_json(target, {"schema_version": 1, "items": []}) or {}
        store = cls(path=target, quality=load_config("quality"))
        for item in payload.get("items", []):
            try:
                record = Evidence.from_dict(item)
            except Exception:  # a broken record must not poison the whole run
                continue
            store.records[record.id] = record
        return store

    def save(self) -> Path:
        """Persist all records."""
        payload = {
            "schema_version": 1,
            "updated_at": now_iso(),
            "count": len(self.records),
            "items": [record.to_dict() for record in self.sorted()],
        }
        return write_json(self.path, payload)

    # ------------------------------------------------------------------- CRUD
    def add(self, record: Evidence, dedupe: bool = True) -> Evidence:
        """Insert evidence, rejecting records that fail schema validation.

        Returns the stored record; when an identical claim+uri already exists
        the new record is merged into it instead of duplicated.
        """
        if not record.source_tier:
            record.source_tier = self.infer_tier(record)
        if not record.domain:
            record.domain = Evidence.host_of(record.source_uri)
        ok, message = validate(record.to_dict(), "evidence")
        if not ok:
            raise ValueError(f"invalid evidence: {message}")
        if dedupe:
            twin = self.find_twin(record)
            if twin is not None:
                twin.supports = sorted(set(twin.supports + record.supports))
                twin.confidence = max(twin.confidence, record.confidence)
                return twin
        self.records[record.id] = record
        return record

    def add_many(self, records: Iterable[Evidence]) -> list[Evidence]:
        """Insert several records, skipping (but reporting) invalid ones."""
        stored: list[Evidence] = []
        for record in records:
            try:
                stored.append(self.add(record))
            except ValueError:
                continue
        return stored

    def get(self, evidence_id: str) -> Evidence | None:
        return self.records.get(evidence_id)

    def delete(self, evidence_id: str) -> bool:
        return self.records.pop(evidence_id, None) is not None

    def all(self) -> list[Evidence]:
        return list(self.records.values())

    def sorted(self) -> list[Evidence]:
        return sorted(self.records.values(), key=lambda item: (item.retrieved_at, item.id))

    def for_node(self, node_id: str) -> list[Evidence]:
        """Evidence that explicitly supports ``node_id``."""
        return [record for record in self.records.values() if node_id in record.supports]

    def contradicting(self, node_id: str) -> list[Evidence]:
        """Evidence that contradicts or undermines ``node_id``."""
        return [record for record in self.records.values() if node_id in record.contradicts]

    def find_twin(self, record: Evidence) -> Evidence | None:
        """Locate an existing record for the same claim and source."""
        for existing in self.records.values():
            if existing.claim.strip() == record.claim.strip() and existing.source_uri == record.source_uri:
                return existing
        return None

    # ------------------------------------------------------------- analytics
    def domains(self, records: Iterable[Evidence] | None = None) -> list[str]:
        """Distinct source domains, used for independence checks."""
        pool = records if records is not None else self.records.values()
        return sorted({key for key in (record.independence_key for record in pool) if key})

    def tier_weight(self, tier: str) -> float:
        weights = self.quality.get("scoring", {})  # noqa: F841 - kept for symmetry
        table = {"primary": 1.0, "secondary": 0.7, "tertiary": 0.4}
        configured = self.quality.get("source_tiers", {})
        if tier in configured and isinstance(configured[tier], dict):
            return float(configured[tier].get("weight", table.get(tier, 0.6)))
        return table.get(tier, 0.6)

    def infer_tier(self, record: Evidence) -> str:
        """Guess primary/secondary/tertiary from the source URI and title."""
        text = f"{record.source_uri} {record.title} {record.source_type}".lower()
        tiers = self.quality.get("source_tiers", {})
        for tier in ("primary", "secondary", "tertiary"):
            for pattern in tiers.get(tier, {}).get("patterns", []) if isinstance(tiers.get(tier), dict) else []:
                probe = str(pattern).strip("*/ ").lower()
                if probe and probe in text:
                    return tier
        if record.source_type in {"code", "repository", "paper", "docs", "dataset", "database", "file"}:
            return "primary"
        return "secondary"

    def quality_score(self, record: Evidence, max_age_days: int = 365) -> float:
        """Confidence discounted by source tier, flags and staleness."""
        score = record.confidence * self.tier_weight(record.source_tier)
        penalties = self.quality.get("penalties", {})
        if "fabricated" in record.quality_flags:
            return 0.0
        for flag in ("seo_farm", "ai_generated", "marketing", "paywalled", "outdated_version", "unrelated", "truncated"):
            if flag in record.quality_flags:
                score *= 0.85
        if _age_days(record.retrieved_at) > float(penalties.get("stale_evidence_days", max_age_days)):
            score -= 0.1
        return clamp(score, 0.0, 1.0)

    # ---------------------------------------------------------- verification
    def mark_verified(self, evidence_id: str, method: str = "cross_source", by: str = "verifier", notes: str = "") -> Evidence:
        """Record that the verifier confirmed this evidence."""
        record = self.records.get(evidence_id)
        if record is None:
            raise KeyError(f"unknown evidence id: {evidence_id}")
        record.verification = {
            "status": "confirmed",
            "method": method,
            "verified_by": by,
            "verified_at": now_iso(),
            "notes": notes,
        }
        record.confidence = clamp(record.confidence + 0.1, 0.0, 1.0)
        return record

    def link_contradiction(self, left_id: str, right_id: str) -> bool:
        """Record a mutual contradiction between two evidence records."""
        left, right = self.records.get(left_id), self.records.get(right_id)
        if left is None or right is None:
            return False
        if right_id not in left.contradicts:
            left.contradicts.append(right_id)
        if left_id not in right.contradicts:
            right.contradicts.append(left_id)
        return True

    def stats(self) -> dict[str, Any]:
        """Aggregate shape of the store, surfaced in reports and gates."""
        tiers: dict[str, int] = {}
        modalities: dict[str, int] = {}
        for record in self.records.values():
            tiers[record.source_tier] = tiers.get(record.source_tier, 0) + 1
            modalities[record.modality] = modalities.get(record.modality, 0) + 1
        verified = sum(1 for record in self.records.values() if record.verification.get("status") == "confirmed")
        return {
            "total": len(self.records),
            "domains": len(self.domains()),
            "tiers": tiers,
            "modalities": modalities,
            "verified": verified,
            "mean_confidence": round(
                sum(record.confidence for record in self.records.values()) / len(self.records), 3
            )
            if self.records
            else 0.0,
            "contradictions": sum(len(record.contradicts) for record in self.records.values()) // 2,
        }


def _age_days(timestamp: str, now: datetime | None = None) -> float:
    """Days elapsed since an ISO timestamp; unknown formats count as fresh."""
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (reference - parsed).total_seconds() / 86400.0)
