"""Descriptive bounded-work benchmark for the CRM v2 source optimizer."""

from __future__ import annotations

import json
import math
import statistics
import time
import tracemalloc
from dataclasses import replace

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    CandidatePolicyV2,
    DeltaEnvelopeV2,
    LogicalAtomV2,
    PackingPolicyV2,
    RecompositionRequestV2,
    SolverModeV2,
    SourceWeightV2,
    WeightPolicyV2,
    recomposition_policy_hash_v2,
)
from crm_experiment.kernel_v2 import default_kernel_schema_v2, select_kernel_v2
from crm_experiment.logical_v2 import resolve_latest_v2
from crm_experiment.matrix_v2 import build_source_matrix_v2
from crm_experiment.optimizer_v2 import optimize_sources_v2
from crm_experiment.recompose_v2 import encode_plan_v2


def _atom(index: int, *, core: bool = False) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=f"benchmark-{index}",
        semantic_keys=(f"topic:{index:03d}",),
        role=AtomRole.ROOT_GOAL if core else AtomRole.CONTEXT,
        text=(
            "preserve bounded optimizer evidence"
            if core
            else f"shared apparatus calibration observation :: {index:03d}"
        ),
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=1,
        provenance=(f"benchmark:{index:03d}",),
        exact=False,
        depends_on=(),
        core_required=core,
    )


def _case(
    row_count: int,
    released_count: int,
    *,
    with_kernel: bool,
    accepted_budget: int = 100_000,
):
    core_count = 1 if with_kernel else 0
    atoms = tuple(_atom(index, core=index < core_count) for index in range(row_count))
    resolution = resolve_latest_v2(
        None,
        DeltaEnvelopeV2(0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))),
        key_registry_limit=256,
        max_semantic_key_bytes=128,
    )
    released_ids = {
        atom.source_id
        for atom in sorted(
            (atom for atom in atoms if not atom.core_required),
            key=lambda atom: atom.source_id,
        )[:released_count]
    }
    resolution = replace(
        resolution,
        active_records=tuple(
            record
            for record in resolution.active_records
            if record.atom.source_id not in released_ids
        ),
    )
    weights = WeightPolicyV2.create(
        version="benchmark-v1",
        source_weights=tuple(
            SourceWeightV2(source_id, float(index + 1))
            for index, source_id in enumerate(
                sorted({winner.source_id for winner in resolution.frontier})
            )
        ),
    )
    packing = PackingPolicyV2(
        codec="dmc1-lcp-lcs-v1",
        immutable_source_ids=tuple(
            sorted(atom.source_id for atom in atoms if not atom.core_required)
        ),
        max_records_per_block=8,
        max_decoded_block_bytes=65_536,
    )
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.GREEDY,
        exact_small_limit=8,
        reference_row_limit=8,
        max_pack_neighbors=3,
        max_pack_proposals=512,
        max_plan_evaluations=96,
        max_greedy_steps=1,
        max_refinement_evaluations=4,
    )
    provisional = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(0, 1, ()),
        requested_budget=accepted_budget,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=weights,
        next_packing_policy=packing,
        candidate_policy=policy,
        key_registry_limit=256,
        max_semantic_key_bytes=128,
    )
    kernel = select_kernel_v2(
        resolution.active_records,
        resolution.frontier,
        resolution.receipts,
        weights,
        provisional.kernel_schema,
        generation=1,
        high_water=1,
        requested_budget=accepted_budget,
        key_registry_limit=256,
        max_semantic_key_bytes=128,
        recomposition_policy_hash=recomposition_policy_hash_v2(
            provisional.kernel_schema,
            provisional.candidate_policy,
        ),
    )
    if not kernel.valid:
        raise RuntimeError(f"benchmark kernel invalid: {kernel.reasons}")
    matrix = build_source_matrix_v2(resolution, weights, packing, policy)
    return resolution, matrix, kernel.records, provisional


def _run_once(prepared) -> dict:
    resolution, matrix, kernel_records, request = prepared
    encoded_by_plan = {}

    def oracle(plan):
        encoded = encode_plan_v2(
            resolution,
            matrix,
            plan,
            kernel_records,
            request,
            generation=1,
        )
        encoded_by_plan[plan] = encoded
        return encoded.evaluation

    started = time.perf_counter_ns()
    selection = optimize_sources_v2(
        matrix,
        request.candidate_policy,
        request.requested_budget,
        oracle,
    )
    encoded = encoded_by_plan[selection.plan]
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "elapsed_ms": elapsed_ms,
        "pack_proposals": matrix.pack_proposal_count,
        "total_candidates": matrix.total_candidate_count,
        "oracle_evaluations": selection.work.oracle_evaluations,
        "greedy_steps": selection.work.greedy_steps,
        "refinement_evaluations": selection.work.refinement_evaluations,
        "codec_attempts": encoded.codec_attempts,
        "emitted_packs": encoded.evaluation.emitted_pack_count,
        "encoder_full_state_evaluations": encoded.full_state_evaluations,
        "work_limit_hit": selection.work.work_limit_hit,
        "retained_sources": len(selection.plan.retained_source_ids),
        "released_sources": len(selection.released_source_ids),
        "resident_bytes": selection.evaluation.resident_bytes,
        "accepted_budget": request.requested_budget,
    }


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _benchmark(
    name: str,
    row_count: int,
    released_count: int,
    with_kernel: bool,
    *,
    accepted_budget: int = 100_000,
):
    prepared = _case(
        row_count,
        released_count,
        with_kernel=with_kernel,
        accepted_budget=accepted_budget,
    )
    samples: list[dict] = []
    peaks: list[int] = []
    for _ in range(3):
        tracemalloc.start()
        sample = _run_once(prepared)
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        samples.append(sample)
        peaks.append(peak_bytes)
    elapsed = [float(sample["elapsed_ms"]) for sample in samples]
    stable = {key: samples[0][key] for key in samples[0] if key != "elapsed_ms"}
    if any(
        any(sample[key] != expected for key, expected in stable.items())
        for sample in samples[1:]
    ):
        raise RuntimeError("benchmark logical result or work counters are unstable")
    return {
        "case": name,
        "runs": len(samples),
        "median_ms": statistics.median(elapsed),
        "p95_ms": _p95(elapsed),
        "peak_bytes_max": max(peaks),
        **stable,
    }


def main() -> None:
    results = (
        _benchmark("n72-kernel", 72, 0, True),
        _benchmark("n77-no-kernel-7-released", 77, 7, False),
        _benchmark("n72-kernel-constrained", 72, 0, True, accepted_budget=60_000),
        _benchmark(
            "n77-no-kernel-7-released-constrained",
            77,
            7,
            False,
            accepted_budget=65_000,
        ),
    )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
