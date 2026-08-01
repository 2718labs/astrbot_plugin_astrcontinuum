"""Tests for deterministic claim-v2 scoring and cluster bootstrap bounds."""

from __future__ import annotations

import pytest

from crm_experiment.contracts import ClaimV2Result, ProjectionResult, QueryGold
from crm_experiment.projection import UNKNOWN
from crm_experiment.scoring import (
    normalize_text,
    paired_cluster_lower_bound,
    score_projection,
)


def _gold(
    *,
    required_ids: tuple[str, ...] = (),
    forbidden_ids: tuple[str, ...] = (),
    required_text: tuple[str, ...] = (),
    forbidden_text: tuple[str, ...] = (),
) -> QueryGold:
    return QueryGold(
        query_id="query-1",
        required_atom_ids=required_ids,
        forbidden_atom_ids=forbidden_ids,
        required_text=required_text,
        forbidden_text=forbidden_text,
        core_query=True,
    )


def _projection(
    *,
    text: str,
    covered_ids: tuple[str, ...] = (),
    supported: bool = True,
) -> ProjectionResult:
    return ProjectionResult(
        query_id="query-1",
        text=text,
        selected_atom_ids=(),
        selected_covered_ids=covered_ids,
        byte_cost=len(text.encode("utf-8")),
        supported=supported,
    )


def test_normalize_text_nfkc_casefolds_and_collapses_whitespace() -> None:
    assert normalize_text("  当前　ＡＢＣ\n\t计划  ") == "当前 abc 计划"


def test_score_projection_accepts_normalized_chinese_exact_anchor_with_coverage() -> (
    None
):
    gold = _gold(
        required_ids=("anchor-current",),
        required_text=("合同编号：ＡＢＣ－１２",),
    )
    result = _projection(
        text="合同编号:abc-12",
        covered_ids=("anchor-current",),
    )

    assert score_projection(result, gold) == ClaimV2Result(
        query_id="query-1",
        passed=True,
        omission=False,
        stale_current=False,
        missing=(),
        contradictions=(),
    )


def test_score_projection_treats_missing_coverage_as_omission() -> None:
    gold = _gold(
        required_ids=("anchor-current",),
        required_text=("当前精确锚点",),
    )
    result = _projection(text="当前精确锚点")

    claim = score_projection(result, gold)

    assert claim.passed is False
    assert claim.omission is True
    assert claim.missing == ("anchor-current",)


def test_score_projection_treats_missing_required_text_as_omission() -> None:
    gold = _gold(
        required_ids=("anchor-current",),
        required_text=("当前精确锚点",),
    )
    result = _projection(text="无关联文本", covered_ids=("anchor-current",))

    claim = score_projection(result, gold)

    assert claim.passed is False
    assert claim.omission is True
    assert claim.missing == ("当前精确锚点",)


def test_score_projection_treats_explicit_unknown_as_omission() -> None:
    gold = _gold(
        required_ids=("anchor-current",),
        required_text=("当前精确锚点",),
    )
    result = _projection(text=UNKNOWN, supported=False)

    claim = score_projection(result, gold)

    assert claim.passed is False
    assert claim.omission is True
    assert claim.missing == ("anchor-current", "当前精确锚点")


def test_score_projection_marks_forbidden_old_revision_as_stale_current() -> None:
    gold = _gold(
        required_ids=("decision-v2",),
        forbidden_ids=("decision-v1",),
        required_text=("最新决定",),
        forbidden_text=("旧决定",),
    )
    result = _projection(
        text="最新决定\n旧决定",
        covered_ids=("decision-v2", "decision-v1"),
    )

    claim = score_projection(result, gold)

    assert claim.passed is False
    assert claim.omission is False
    assert claim.stale_current is True
    assert claim.contradictions == ("decision-v1", "旧决定")


def test_paired_cluster_lower_bound_is_deterministic_and_order_independent() -> None:
    ordered = {
        "alpha": (0.0, 0.0),
        "beta": (1.0, 0.0),
        "gamma": (2.0, 0.0),
    }
    reversed_order = {
        "gamma": (2.0, 0.0),
        "beta": (1.0, 0.0),
        "alpha": (0.0, 0.0),
    }

    expected = pytest.approx(2.0 / 3.0)
    assert (
        paired_cluster_lower_bound(ordered, replicates=8, seed=17, alpha=0.25)
        == expected
    )
    assert (
        paired_cluster_lower_bound(
            reversed_order,
            replicates=8,
            seed=17,
            alpha=0.25,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("paired_by_cluster", "replicates", "alpha"),
    [
        ({}, 1, 0.05),
        ({"cluster": (1.0, 0.0)}, 0, 0.05),
        ({"cluster": (1.0, 0.0)}, -1, 0.05),
        ({"cluster": (1.0, 0.0)}, 1, 0.0),
        ({"cluster": (1.0, 0.0)}, 1, 1.0),
        ({"cluster": (1.0, 0.0)}, 1, float("nan")),
    ],
)
def test_paired_cluster_lower_bound_rejects_invalid_inputs(
    paired_by_cluster: dict[str, tuple[float, float]],
    replicates: int,
    alpha: float,
) -> None:
    with pytest.raises(ValueError):
        paired_cluster_lower_bound(
            paired_by_cluster,
            replicates=replicates,
            seed=1,
            alpha=alpha,
        )
