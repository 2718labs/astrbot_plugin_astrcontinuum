"""RED-first checks for the preregistered V21-007 stress-data arm."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from crm_experiment.contracts_v21 import canonical_json_v21
from crm_experiment.stress_data_v21 import (
    PPM_SCALE_V21,
    PREREGISTERED_STRESS_PROTOCOL_V21,
    PREREGISTERED_STRESS_SCHEMA_V21,
    PreregisteredStressDataV21,
    build_preregistered_stress_setup_v21,
    canonical_preregistered_stress_json_v21,
    parse_preregistered_stress_json_v21,
    run_preregistered_stress_v21,
    validate_preregistered_stress_data_v21,
)

WORKLOAD_ROOT_DOMAIN_V21 = "crm-v21-008-workload-root-s3/v1"


def _expected_workload_root_v21(data: PreregisteredStressDataV21) -> str:
    digest = sha256(
        canonical_json_v21(
            {
                "domain": WORKLOAD_ROOT_DOMAIN_V21,
                "value": {
                    "protocol_id": data.protocol_id,
                    "workload": data.workload,
                },
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"{WORKLOAD_ROOT_DOMAIN_V21}:{digest}"


def test_preregistered_stress_runner_is_public() -> None:
    assert callable(run_preregistered_stress_v21)


def test_setup_materializes_one_frozen_twelve_round_arm() -> None:
    setup = build_preregistered_stress_setup_v21()

    assert len(setup.rounds) == 12
    assert tuple(round_input.round_index for round_input in setup.rounds) == tuple(
        range(12)
    )
    assert len(setup.initial_state.state.dictionary) == 31
    assert setup.initial_state.state.bounds.max_dictionary_entries >= 62
    assert setup.initial_state.state.bounds.max_segments >= 12
    assert not hasattr(setup, "plans")

    expected_batches = (1, 2, 3, 4) * 3
    expected_body_bytes = (3_072,) * 4 + (4_096,) * 4 + (5_120,) * 4
    for round_input, batch_size, body_bytes in zip(
        setup.rounds,
        expected_batches,
        expected_body_bytes,
        strict=True,
    ):
        envelope = round_input.source_envelope
        incoming = tuple(
            source
            for source in envelope.source_records
            if source.record_id in envelope.incoming_record_ids
        )
        assert len(envelope.source_records) == batch_size + 1
        assert len(incoming) == batch_size
        assert envelope.hard_edges == ()
        assert all(
            len(source.body.encode("ascii")) == body_bytes for source in incoming
        )
        assert round_input.policy.max_matrix_rows == 5
        assert round_input.policy.max_new_segments == 1
        assert round_input.policy.max_plan_evaluations == 16
        assert round_input.policy.max_loss_units == 4
        assert round_input.policy.segment_loss_units == 1


@pytest.fixture(scope="module")
def stress_data() -> PreregisteredStressDataV21:
    return run_preregistered_stress_v21()


def test_run_has_the_preregistered_schedule_metrics_and_v21_gates(
    stress_data: PreregisteredStressDataV21,
) -> None:
    validate_preregistered_stress_data_v21(stress_data)

    assert stress_data.schema_version == PREREGISTERED_STRESS_SCHEMA_V21
    assert stress_data.protocol_id == PREREGISTERED_STRESS_PROTOCOL_V21
    assert stress_data.replay_match is True
    assert stress_data.terminal_status == "COMPLETED"
    assert stress_data.terminal_exit_reason == "COMPLETED"
    assert stress_data.terminal_failed_round_index is None
    assert stress_data.terminal_reason is None
    assert len(stress_data.records) == 12
    assert stress_data.evaluator_run_root.startswith("crm-v21-multiround-run-s3/v1:")
    assert stress_data.workload_root == _expected_workload_root_v21(stress_data)
    assert stress_data.data_root.startswith("crm-v21-008-stress-data-root-s3/v1:")

    expected_batches = (1, 2, 3, 4) * 3
    expected_body_bytes = (3_072,) * 4 + (4_096,) * 4 + (5_120,) * 4
    previous = None
    for round_index, (record, batch_size, body_bytes) in enumerate(
        zip(stress_data.records, expected_batches, expected_body_bytes, strict=True),
        start=1,
    ):
        assert record.round_index == round_index
        assert record.batch_size == batch_size
        assert record.incoming_count == batch_size
        assert record.body_bytes_per_record == body_bytes
        assert record.incoming_body_bytes == batch_size * body_bytes
        assert record.release_count == batch_size
        assert record.release_ppm == PPM_SCALE_V21
        assert record.loss_delta == batch_size
        assert record.cumulative_loss == sum(expected_batches[:round_index])
        assert record.before_control_bytes + record.before_source_body_bytes == (
            record.before_resident_bytes
        )
        assert record.control_bytes + record.source_body_bytes == record.resident_bytes
        assert record.control_delta_bytes == (
            record.control_bytes - record.before_control_bytes
        )
        assert record.source_body_delta_bytes == (
            record.source_body_bytes - record.before_source_body_bytes
        )
        assert record.resident_growth_bytes == (
            record.resident_bytes - record.before_resident_bytes
        )
        denominator = record.before_resident_bytes + record.incoming_body_bytes
        assert denominator > 0
        assert record.avoided_incremental_bytes == denominator - record.resident_bytes
        assert record.avoided_incremental_bytes > 0
        assert (
            record.retention_ppm == record.resident_bytes * PPM_SCALE_V21 // denominator
        )
        assert record.reduction_ppm == (
            record.avoided_incremental_bytes * PPM_SCALE_V21 // denominator
        )
        assert record.loss_per_avoided_ppm == (
            record.loss_delta * PPM_SCALE_V21 // record.avoided_incremental_bytes
        )
        assert record.after_generation == record.before_generation + 1
        assert record.after_high_water > record.before_high_water
        assert record.source_envelope_root.startswith("crm-v21-source-envelope-s3/v1:")
        assert record.policy_root.startswith("crm-v21-evidenced-policy-s3/v1:")
        assert record.matrix_root.startswith("crm-v21-evidenced-matrix-s3/v1:")
        assert record.transition_root.startswith("crm-v21-evidenced-transition-s3/v1:")
        if previous is not None:
            assert record.before_state_hash == previous.after_state_hash
            assert record.before_advance_head_root == previous.after_advance_head_root
            assert record.before_generation == previous.after_generation
            assert record.before_high_water == previous.after_high_water
        previous = record


def test_replay_and_canonical_json_are_byte_identical_and_body_free(
    stress_data: PreregisteredStressDataV21,
) -> None:
    replay = run_preregistered_stress_v21()
    encoded = canonical_preregistered_stress_json_v21(stress_data)

    assert encoded == canonical_preregistered_stress_json_v21(replay)
    assert parse_preregistered_stress_json_v21(encoded) == stress_data
    payload = json.loads(encoded)
    assert set(payload) == {
        "data_root",
        "evaluator_run_root",
        "protocol_id",
        "records",
        "replay_match",
        "schema_version",
        "terminal",
        "workload",
        "workload_root",
    }
    assert payload["workload_root"].startswith(WORKLOAD_ROOT_DOMAIN_V21 + ":")
    assert set(payload["terminal"]) == {
        "exit_reason",
        "failed_round_index",
        "reason",
        "status",
    }
    assert len(payload["records"]) == 12
    assert all(
        "body" not in key
        or key
        in {
            "body_bytes_per_record",
            "incoming_body_bytes",
            "before_source_body_bytes",
            "source_body_bytes",
            "source_body_delta_bytes",
        }
        for key in payload["records"][0]
    )
    assert "candidate" not in encoded
    assert '"source_records"' not in encoded
    assert '"source_envelope"' not in encoded
    assert '"state": {' not in encoded
    assert '"head": {' not in encoded


def test_result_and_canonical_artifact_tampering_fail_validation(
    stress_data: PreregisteredStressDataV21,
) -> None:
    with pytest.raises(ValueError, match="replay"):
        validate_preregistered_stress_data_v21(replace(stress_data, replay_match=False))

    payload = json.loads(canonical_preregistered_stress_json_v21(stress_data))
    payload["records"][0]["resident_bytes"] += 1
    tampered = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(ValueError):
        parse_preregistered_stress_json_v21(tampered)

    payload = json.loads(canonical_preregistered_stress_json_v21(stress_data))
    payload["data_root"] = "crm-v21-008-stress-data-root-s3/v1:tampered"
    tampered_root = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(ValueError, match="data_root"):
        parse_preregistered_stress_json_v21(tampered_root)

    payload = json.loads(canonical_preregistered_stress_json_v21(stress_data))
    payload["workload"]["max_source_rows"] = 4
    tampered_workload = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(ValueError, match="workload_root"):
        parse_preregistered_stress_json_v21(tampered_workload)

    tampered_data = replace(
        stress_data,
        workload=replace(stress_data.workload, max_source_rows=4),
    )
    payload = json.loads(canonical_preregistered_stress_json_v21(stress_data))
    payload["workload"]["max_source_rows"] = 4
    payload["workload_root"] = _expected_workload_root_v21(tampered_data)
    synchronized_workload_tamper = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(ValueError, match="workload differs"):
        parse_preregistered_stress_json_v21(synchronized_workload_tamper)

    payload = json.loads(canonical_preregistered_stress_json_v21(stress_data))
    payload["workload_root"] = WORKLOAD_ROOT_DOMAIN_V21 + ":tampered"
    tampered_workload_root = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(ValueError, match="workload_root"):
        parse_preregistered_stress_json_v21(tampered_workload_root)


def test_module_emits_the_canonical_body_free_json_to_stdout_only(
    stress_data: PreregisteredStressDataV21,
) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "crm_experiment.stress_data_v21"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert (
        completed.stdout == canonical_preregistered_stress_json_v21(stress_data) + "\n"
    )
