import pytest
from astrcontinuum.auditor import DeterministicLossAuditor
from astrcontinuum.models import Claim, ClaimStatus, ContextCapsule, ContextEvent, ExactAnchor, Snapshot


def raw_event() -> ContextEvent:
    return ContextEvent("e1", "s", 1, "user_message", "user", "仓库名是 Ayleovelle/SylannEngine", 1.0)


@pytest.mark.asyncio
async def test_audit_rejects_missing_anchor_source() -> None:
    capsule = ContextCapsule("c1", "micro", "s", 1, 1, exact_anchors=(ExactAnchor("a1", "name", "Ayleovelle/SylannEngine", "missing"),))
    candidate = Snapshot("s1", "s", 1, 1, (capsule,), False, 1.0)
    report = await DeterministicLossAuditor().audit(None, candidate, [raw_event()])
    assert not report.passed and report.critical_anchor_recall == 0.0


@pytest.mark.asyncio
async def test_audit_accepts_sourced_claim_and_anchor() -> None:
    claim = Claim("cl1", "fact", "用户拥有 SylannEngine 仓库", ClaimStatus.ACTIVE, 1.0, ("e1",))
    anchor = ExactAnchor("a1", "name", "Ayleovelle/SylannEngine", "e1")
    capsule = ContextCapsule("c1", "micro", "s", 1, 1, claims=(claim,), exact_anchors=(anchor,), token_cost=20, source_coverage=1.0)
    candidate = Snapshot("s1", "s", 1, 1, (capsule,), False, 1.0)
    report = await DeterministicLossAuditor().audit(None, candidate, [raw_event()])
    assert report.passed
