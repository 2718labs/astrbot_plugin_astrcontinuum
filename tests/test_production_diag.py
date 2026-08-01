from dataclasses import replace

from crm_experiment.production_diag import diagnose_production_eligibility
from crm_experiment.recompose import recompose_capsule


def test_lossy_result_is_never_production_eligible(normal_request) -> None:
    result = recompose_capsule(normal_request)

    diagnostic = diagnose_production_eligibility(result)

    assert diagnostic.attempted is False
    assert diagnostic.strict_eligible is False
    assert diagnostic.codes == ("EXPERIMENT_LOSSY",)


def test_lossless_result_still_has_no_formal_envelope(normal_request) -> None:
    result = recompose_capsule(normal_request)
    result = replace(result, loss=replace(result.loss, released_atom_ids=()))

    diagnostic = diagnose_production_eligibility(result)

    assert diagnostic.attempted is False
    assert diagnostic.strict_eligible is False
    assert diagnostic.codes == ("NO_FORMAL_ENVELOPE",)
