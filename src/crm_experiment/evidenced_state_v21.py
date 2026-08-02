"""Bounded current-support evidence around one schema-3 capsule state."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Final

from crm_experiment.contracts_v21 import (
    CapsuleStateV21,
    QueryLabelV21,
    ResidentLayoutV21,
    body_elided_state_v21,
    canonical_bytes_v21,
    canonical_json_v21,
    validate_capsule_state_v21,
)

_DICTIONARY_ID_DOMAIN: Final = "v21-dictionary-id-s3"
_RECORD_ID_DOMAIN: Final = "v21-record-id-s3"
_SEGMENT_ID_DOMAIN: Final = "v21-segment-id-s3"
_SOURCE_COMMITMENT_DOMAIN: Final = "v21-source-commitment-s3"
_FOLDED_COMMITMENT_DOMAIN: Final = "v21-folded-commitment-s3"
_EVIDENCED_STATE_HASH_DOMAIN: Final = "crm-v21-evidenced-state-s3/v1"


class EvidencedStateMismatchV21(ValueError):
    """An external expected state hash does not name the queried wrapper."""


def _require_domain_digest_v21(value: object, domain: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a {domain} domain digest")
    if re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", value) is None:
        raise ValueError(f"{label} must be a {domain} domain digest")
    return value


class ContributionSupportV21(StrEnum):
    """The two current-support forms that can back a contribution key."""

    EXACT = "EXACT"
    SEGMENT = "SEGMENT"


@dataclass(frozen=True, slots=True)
class ContributionLocatorV21:
    """One current locator; it stores no source text or transition history."""

    contribution_key: str
    support: ContributionSupportV21
    resident_id: str
    support_commitment: str

    def __post_init__(self) -> None:
        validate_contribution_locator_v21(self)


def validate_contribution_locator_v21(locator: ContributionLocatorV21) -> None:
    """Validate a locator's nominal type and support-specific digest domains."""
    if type(locator) is not ContributionLocatorV21:
        raise ValueError("contribution locator requires nominal ContributionLocatorV21")
    _require_domain_digest_v21(
        locator.contribution_key,
        _DICTIONARY_ID_DOMAIN,
        "contribution locator key",
    )
    if type(locator.support) is not ContributionSupportV21:
        raise ValueError("contribution locator support must be a closed V21 support")
    if locator.support is ContributionSupportV21.EXACT:
        _require_domain_digest_v21(
            locator.resident_id,
            _RECORD_ID_DOMAIN,
            "EXACT contribution locator resident_id",
        )
        _require_domain_digest_v21(
            locator.support_commitment,
            _SOURCE_COMMITMENT_DOMAIN,
            "EXACT contribution locator support_commitment",
        )
        return
    _require_domain_digest_v21(
        locator.resident_id,
        _SEGMENT_ID_DOMAIN,
        "SEGMENT contribution locator resident_id",
    )
    _require_domain_digest_v21(
        locator.support_commitment,
        _FOLDED_COMMITMENT_DOMAIN,
        "SEGMENT contribution locator support_commitment",
    )


@dataclass(frozen=True, slots=True)
class ContributionRequirementV21:
    """A stable dictionary-key selector for current-support queries."""

    contribution_key: str

    def __post_init__(self) -> None:
        validate_contribution_requirement_v21(self)


def validate_contribution_requirement_v21(
    requirement: ContributionRequirementV21,
) -> None:
    """Revalidate one nominal dictionary-key requirement at the query boundary."""
    if type(requirement) is not ContributionRequirementV21:
        raise TypeError(
            "V21 contribution requirements require nominal ContributionRequirementV21"
        )
    _require_domain_digest_v21(
        requirement.contribution_key,
        _DICTIONARY_ID_DOMAIN,
        "contribution requirement key",
    )


@dataclass(frozen=True, slots=True)
class EvidencedCapsuleStateV21:
    """Validated schema-3 state plus its bounded current-support index."""

    state: CapsuleStateV21
    contribution_index: tuple[ContributionLocatorV21, ...]

    def __post_init__(self) -> None:
        validate_evidenced_capsule_state_v21(self)


def _evidenced_layout_unchecked_v21(
    state: EvidencedCapsuleStateV21,
) -> ResidentLayoutV21:
    control_bytes = canonical_bytes_v21(
        {
            "state": body_elided_state_v21(state.state),
            "contribution_index": state.contribution_index,
        }
    )
    source_body_bytes = sum(
        len(record.body.encode("utf-8"))
        for record in state.state.exact_kernel + state.state.hot_cache
    )
    return ResidentLayoutV21(
        control_bytes=control_bytes,
        source_body_bytes=source_body_bytes,
        resident_bytes=control_bytes + source_body_bytes,
    )


def validate_evidenced_capsule_state_v21(state: EvidencedCapsuleStateV21) -> None:
    """Fail closed unless every locator names current, verified support."""
    if type(state) is not EvidencedCapsuleStateV21:
        raise TypeError("V21 evidenced state requires nominal EvidencedCapsuleStateV21")
    validate_capsule_state_v21(state.state)
    if type(state.contribution_index) is not tuple:
        raise ValueError("V21 contribution index must be a tuple")

    locators = state.contribution_index
    keys: list[str] = []
    current_dictionary_keys = {entry.key for entry in state.state.dictionary}
    current_records = {
        record.record_id: record
        for record in state.state.exact_kernel + state.state.hot_cache
    }
    current_segments = {
        segment.segment_id: segment for segment in state.state.capsule_segments
    }
    for locator in locators:
        validate_contribution_locator_v21(locator)
        keys.append(locator.contribution_key)
        if locator.contribution_key not in current_dictionary_keys:
            raise ValueError(
                "contribution locator key must remain in the current dictionary"
            )
        if locator.support is ContributionSupportV21.EXACT:
            record = current_records.get(locator.resident_id)
            if record is None:
                raise ValueError(
                    "EXACT contribution locator must bind a current exact or hot record"
                )
            if locator.support_commitment != record.commitment:
                raise ValueError(
                    "EXACT contribution locator commitment must bind its current record"
                )
            continue
        segment = current_segments.get(locator.resident_id)
        if segment is None:
            raise ValueError("SEGMENT contribution locator must bind a current segment")
        if locator.support_commitment != segment.folded_commitment_root:
            raise ValueError(
                "SEGMENT contribution locator commitment must bind its current segment"
            )

    if tuple(keys) != tuple(sorted(keys)):
        raise ValueError("contribution index keys must be canonically sorted")
    if len(set(keys)) != len(keys):
        raise ValueError("contribution index keys must be unique")
    if (
        len(state.state.dictionary) + len(locators)
        > state.state.bounds.max_dictionary_entries
    ):
        raise ValueError("contribution index exceeds shared dictionary entry capacity")
    if (
        canonical_bytes_v21(state.state.dictionary) + canonical_bytes_v21(locators)
        > state.state.bounds.max_dictionary_bytes
    ):
        raise ValueError("contribution index exceeds shared dictionary byte capacity")
    if (
        _evidenced_layout_unchecked_v21(state).resident_bytes
        > state.state.frame.accepted_budget
    ):
        raise ValueError("evidenced resident state exceeds its accepted budget")


def evidenced_resident_layout_v21(
    state: EvidencedCapsuleStateV21,
) -> ResidentLayoutV21:
    """Return wrapper C/B/R accounting with source bodies retained exactly once."""
    validate_evidenced_capsule_state_v21(state)
    return _evidenced_layout_unchecked_v21(state)


def evidenced_state_hash_v21(state: EvidencedCapsuleStateV21) -> str:
    """Hash the full state and canonical current-support index in a new domain."""
    validate_evidenced_capsule_state_v21(state)
    digest = sha256(
        canonical_json_v21(
            {
                "domain": _EVIDENCED_STATE_HASH_DOMAIN,
                "state": state.state,
                "contribution_index": state.contribution_index,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"{_EVIDENCED_STATE_HASH_DOMAIN}:{digest}"


def query_evidenced_v21(
    wrapper: EvidencedCapsuleStateV21,
    requirements: tuple[ContributionRequirementV21, ...],
    *,
    expected_state_hash: str,
) -> QueryLabelV21:
    """Return a label only when a trusted external hash names this wrapper."""
    validate_evidenced_capsule_state_v21(wrapper)
    expected_hash = _require_domain_digest_v21(
        expected_state_hash,
        _EVIDENCED_STATE_HASH_DOMAIN,
        "expected evidenced state hash",
    )
    actual_hash = evidenced_state_hash_v21(wrapper)
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise EvidencedStateMismatchV21(
            "expected evidenced state hash does not match the queried wrapper"
        )
    if type(requirements) is not tuple or not requirements:
        raise ValueError(
            "a nonempty evidenced contribution requirement set is required"
        )
    locators_by_key = {
        locator.contribution_key: locator for locator in wrapper.contribution_index
    }
    saw_segment = False
    for requirement in requirements:
        if type(requirement) is not ContributionRequirementV21:
            raise TypeError(
                "V21 contribution requirements require nominal ContributionRequirementV21"
            )
        validate_contribution_requirement_v21(requirement)
        locator = locators_by_key.get(requirement.contribution_key)
        if locator is None:
            return QueryLabelV21.RELEASED_MISS
        if locator.support is ContributionSupportV21.SEGMENT:
            saw_segment = True
    if saw_segment:
        return QueryLabelV21.CAPSULE_APPROX
    return QueryLabelV21.EXACT
