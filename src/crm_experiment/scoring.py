"""Deterministic claim-v2 projection scoring and paired bootstrap bounds."""

from __future__ import annotations

import math
import random
import unicodedata

from crm_experiment.contracts import ClaimV2Result, ProjectionResult, QueryGold


def normalize_text(value: str) -> str:
    """Normalize text for deterministic claim-v2 substring comparisons."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def score_projection(result: ProjectionResult, gold: QueryGold) -> ClaimV2Result:
    """Score coverage and normalized textual claims against offline gold data."""
    selected = set(result.selected_covered_ids)
    missing_ids = sorted(set(gold.required_atom_ids) - selected)
    stale_ids = sorted(set(gold.forbidden_atom_ids) & selected)
    normalized = normalize_text(result.text)
    missing_text = sorted(
        token for token in gold.required_text if normalize_text(token) not in normalized
    )
    forbidden_text = sorted(
        token for token in gold.forbidden_text if normalize_text(token) in normalized
    )
    missing = tuple(missing_ids + missing_text)
    contradictions = tuple(stale_ids + forbidden_text)
    omission = not result.supported or bool(missing)
    stale = bool(contradictions)
    return ClaimV2Result(
        query_id=gold.query_id,
        passed=not omission and not stale,
        omission=omission,
        stale_current=stale,
        missing=missing,
        contradictions=contradictions,
    )


def paired_cluster_lower_bound(
    paired_by_cluster: dict[str, tuple[float, float]],
    replicates: int,
    seed: int,
    alpha: float = 0.05,
) -> float:
    """Return the preregistered lower-tail paired cluster-bootstrap bound."""
    if not paired_by_cluster:
        raise ValueError("paired_by_cluster must not be empty")
    if replicates <= 0:
        raise ValueError("replicates must be positive")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")

    cluster_ids = tuple(sorted(paired_by_cluster))
    rng = random.Random(seed)
    deltas = []
    for _ in range(replicates):
        sample = [rng.choice(cluster_ids) for _ in cluster_ids]
        delta = sum(
            paired_by_cluster[key][0] - paired_by_cluster[key][1] for key in sample
        ) / len(sample)
        deltas.append(delta)
    deltas.sort()
    index = max(0, math.floor(alpha * replicates) - 1)
    return deltas[index]
