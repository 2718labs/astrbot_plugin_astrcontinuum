"""Production-eligibility diagnostics for the isolated CRM experiment."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RecompositionResult


@dataclass(frozen=True, slots=True)
class ProductionDiagnostic:
    attempted: bool
    strict_eligible: bool
    codes: tuple[str, ...]


def diagnose_production_eligibility(
    result: RecompositionResult,
) -> ProductionDiagnostic:
    """Report why an experiment result cannot enter production."""
    if result.loss.released_atom_ids:
        return ProductionDiagnostic(False, False, ("EXPERIMENT_LOSSY",))
    return ProductionDiagnostic(False, False, ("NO_FORMAL_ENVELOPE",))
