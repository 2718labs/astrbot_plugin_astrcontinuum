"""Deterministic receipt harness for the v0.3 opt-in update path.

This is deliberately not a semantic-model benchmark.  Each frozen synthetic
case drives the real injected/fenced worker through an initial publication and
one opt-in reorganization update, then records only hashes, counts, and stable
status codes.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import astrcontinuum as ac
from astrcontinuum.storage import CanonicalMetricObservation
from astrcontinuum.tokenization import CANONICAL_O200K

SCENARIO_SCHEMA_VERSION = "v03-update-scenarios-v1"
RECEIPT_SCHEMA_VERSION = "v03-update-benchmark-receipt-v1"
EXECUTION_PATH = "opt_in_fenced_compaction_worker"
ORDINARY_PROVIDER_RUNTIME_REORGANIZATION_WIRED = False
E2E_SUMMARY_STATUS = "BLOCKED"
EXPECTED_SCENARIO_COUNT = 12
FROZEN_TRIALS = 3
DEFAULT_TOKEN_BUDGET = 10_000
FROZEN_FIXTURE_SHA256 = "36ba7b902c57903b5b1abcce21378812c21f54267f018b8706e57a4be3a648d9"
FROZEN_NOW = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
EMPTY_SHA256 = "0" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS_PATH = Path(__file__).with_name("v03_update_scenarios.json")
EXPERIMENT_ID = "V03-WIRE-001"
EXPERIMENT_SCOPE = "synthetic_provider_free_wiring_gate"

RECEIPT_ROW_FIELDS = (
    "scenario_code",
    "trial",
    "status_code",
    "baseline_status_code",
    "execution_path",
    "ordinary_provider_runtime_reorganization_wired",
    "e2e_summary_status",
    "input_sha256",
    "snapshot_sha256",
    "ledger_sha256",
    "pointer_before",
    "pointer_after",
    "ledger_record_count",
    "retained_record_count",
    "approximate_record_count",
    "released_record_count",
    "non_summary_released_record_count",
    "required_record_count",
    "failure_codes",
)

AGGREGATE_ROW_FIELDS = (
    "experiment_id",
    "scope",
    "execution_path",
    "denominator",
    "baseline_committed",
    "update_committed",
    "pointer_advanced",
    "durable_ledger",
    "required_core_edges",
    "non_summary_released_record_count",
    "ordinary_provider_runtime_reorganization_wired",
    "e2e_summary_status",
    "fixture_sha256",
    "result_sha256",
)


@dataclass(frozen=True, slots=True)
class FrozenUpdateScenario:
    """One fixed synthetic initial event and one fixed synthetic update."""

    scenario_code: str
    initial_event: str
    update_event: str

    def __post_init__(self) -> None:
        if not self.scenario_code.strip():
            raise ValueError("scenario_code must be non-empty")
        if not self.initial_event.strip() or not self.update_event.strip():
            raise ValueError("frozen scenario events must be non-empty")

    @property
    def input_sha256(self) -> str:
        return _sha256_json(
            {
                "scenario_code": self.scenario_code,
                "initial_event": self.initial_event,
                "update_event": self.update_event,
            }
        )


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    """Content-free aggregate of one frozen v0.3 update benchmark run."""

    total_trials: int
    committed_trials: int
    result_sha256: str


@dataclass(frozen=True, slots=True)
class _ReceiptRow:
    scenario_code: str
    trial: int
    status_code: str
    baseline_status_code: str
    input_sha256: str
    snapshot_sha256: str
    ledger_sha256: str
    pointer_before: int
    pointer_after: int
    ledger_record_count: int
    retained_record_count: int
    approximate_record_count: int
    released_record_count: int
    non_summary_released_record_count: int
    required_record_count: int
    failure_codes: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "scenario_code": self.scenario_code,
            "trial": self.trial,
            "status_code": self.status_code,
            "baseline_status_code": self.baseline_status_code,
            "execution_path": EXECUTION_PATH,
            "ordinary_provider_runtime_reorganization_wired": (
                ORDINARY_PROVIDER_RUNTIME_REORGANIZATION_WIRED
            ),
            "e2e_summary_status": E2E_SUMMARY_STATUS,
            "input_sha256": self.input_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "ledger_sha256": self.ledger_sha256,
            "pointer_before": self.pointer_before,
            "pointer_after": self.pointer_after,
            "ledger_record_count": self.ledger_record_count,
            "retained_record_count": self.retained_record_count,
            "approximate_record_count": self.approximate_record_count,
            "released_record_count": self.released_record_count,
            "non_summary_released_record_count": self.non_summary_released_record_count,
            "required_record_count": self.required_record_count,
            "failure_codes": list(self.failure_codes),
        }


class _DeterministicAppendBackend:
    """A provider-free backend that adds one structured synthetic capsule."""

    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        source_events = request.source_events
        if not source_events:
            raise ValueError("synthetic compiler requires at least one source event")
        source_ids = tuple(event.event_id for event in source_events)
        source_digest = _sha256_json(
            {
                "event_ids": source_ids,
                "content_sha256": tuple(_sha256_text(event.content) for event in source_events),
            }
        )
        first_event = source_events[0]
        last_event = source_events[-1]
        capsule = ac.ContextCapsuleEnvelope(
            capsule_id=f"synthetic-capsule-{source_digest[:24]}",
            schema_version="1.0.0",
            level=ac.CapsuleLevel.MICRO,
            session_key=first_event.session_key,
            covered_event_start=first_event.sequence,
            covered_event_end=last_event.sequence,
            source_event_ids=source_ids,
            goals=(
                ac.CapsuleClaim(
                    claim_id=f"synthetic-goal-{source_digest[:24]}",
                    text=f"synthetic-source:{source_digest[:16]}",
                    status=ac.SemanticStatus.ACTIVE,
                    confidence=1.0,
                    source_event_ids=source_ids,
                ),
            ),
            constraints=(),
            decisions=(),
            progress=(),
            open_loops=(),
            preferences=(),
            entities=(),
            emotional_context=(),
            exact_anchors=(
                ac.CapsuleAnchor(
                    anchor_id=f"synthetic-anchor-{source_digest[:24]}",
                    anchor_type=ac.AnchorType.CODE,
                    exact_text=f"synthetic-anchor:{source_digest[:16]}",
                    source_event_ids=source_ids,
                    status=ac.AnchorStatus.ACTIVE,
                    importance=1.0,
                ),
            ),
            dependencies=(
                ac.Dependency(
                    dependency_id=f"synthetic-dependency-{source_digest[:24]}",
                    kind="runtime",
                    target_id="synthetic-v03-update",
                    source_event_ids=source_ids,
                ),
            ),
            narrative_summary=f"synthetic-summary:{source_digest[:16]}",
            token_cost=1,
            quality=ac.CapsuleQuality(
                mechanical_passed=True,
                source_coverage=1.0,
                anchor_recall=1.0,
                unsupported_critical_claims=0,
                coverage_gap=0,
            ),
            created_at=last_event.created_at,
        )
        return ac.CompilerOutput(
            capsules=(*request.base_capsules, capsule),
            rendered_context=f"synthetic-context:{source_digest}",
        )


def load_frozen_scenarios(path: Path) -> tuple[FrozenUpdateScenario, ...]:
    """Load the one closed 12-scenario fixture without accepting drift."""

    try:
        fixture_bytes = path.read_bytes()
        payload = json.loads(fixture_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("frozen scenario fixture is unreadable") from error
    if _sha256_bytes(fixture_bytes) != FROZEN_FIXTURE_SHA256:
        raise ValueError("frozen scenario fixture SHA-256 does not match the registered fixture")
    if not isinstance(payload, dict) or payload.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise ValueError("frozen scenario fixture schema is invalid")
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) != EXPECTED_SCENARIO_COUNT:
        raise ValueError("frozen scenario fixture must contain exactly 12 scenarios")

    scenarios: list[FrozenUpdateScenario] = []
    for raw in raw_scenarios:
        if not isinstance(raw, dict) or set(raw) != {
            "scenario_code",
            "initial_event",
            "update_event",
        }:
            raise ValueError("frozen scenario entry shape is invalid")
        values = tuple(raw[name] for name in ("scenario_code", "initial_event", "update_event"))
        if any(not isinstance(value, str) for value in values):
            raise ValueError("frozen scenario values must be strings")
        scenarios.append(FrozenUpdateScenario(*values))

    expected_codes = tuple(f"UPD-{index:02d}" for index in range(1, EXPECTED_SCENARIO_COUNT + 1))
    if tuple(item.scenario_code for item in scenarios) != expected_codes:
        raise ValueError("frozen scenario codes are not canonical")
    return tuple(scenarios)


def run_v03_update_benchmark(
    *,
    scenarios_path: Path,
    output_dir: Path,
    workspace: Path,
    trials: int = FROZEN_TRIALS,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> BenchmarkSummary:
    """Run the frozen provider-free v0.3 update gate and write content-free receipts."""

    if trials != FROZEN_TRIALS:
        raise ValueError("frozen v0.3 update benchmark requires exactly three trials")
    if token_budget != DEFAULT_TOKEN_BUDGET:
        raise ValueError(
            f"frozen v0.3 update benchmark requires token_budget={DEFAULT_TOKEN_BUDGET}"
        )
    _validate_workspace(workspace)
    scenarios = load_frozen_scenarios(scenarios_path)
    fixture_sha256 = FROZEN_FIXTURE_SHA256
    run_sha256 = _sha256_json(
        {
            "fixture_sha256": fixture_sha256,
            "trials": trials,
            "token_budget": token_budget,
            "execution_path": EXECUTION_PATH,
        }
    )
    run_workspace = workspace / f"v03-update-{run_sha256[:16]}"
    if run_workspace.exists():
        raise FileExistsError("workspace already contains this frozen benchmark run")
    _validate_output_directory(output_dir)
    run_workspace.mkdir(parents=True)

    rows = tuple(
        _run_trial(
            scenario=scenario,
            trial=trial,
            run_workspace=run_workspace,
            token_budget=token_budget,
        )
        for scenario in scenarios
        for trial in range(1, trials + 1)
    )
    row_payloads = [row.to_json() for row in rows]
    result_sha256 = _sha256_json(row_payloads)
    _write_receipts(
        output_dir=output_dir,
        fixture_sha256=fixture_sha256,
        trials=trials,
        rows=row_payloads,
        aggregate=_aggregate_row(
            rows=rows,
            fixture_sha256=fixture_sha256,
            result_sha256=result_sha256,
        ),
        result_sha256=result_sha256,
    )
    committed_trials = sum(row.status_code == "COMMITTED" for row in rows)
    return BenchmarkSummary(
        total_trials=len(rows),
        committed_trials=committed_trials,
        result_sha256=result_sha256,
    )


def _run_trial(
    *,
    scenario: FrozenUpdateScenario,
    trial: int,
    run_workspace: Path,
    token_budget: int,
) -> _ReceiptRow:
    case_code = f"{scenario.scenario_code}-T{trial:02d}"
    case_workspace = run_workspace / case_code
    session_key = ac.SessionKey(
        platform_instance_id="astrcontinuum-v03-benchmark",
        message_type="friend_message",
        session_id=f"v03-{case_code}",
        group_id=None,
        user_id=f"benchmark-{case_code}",
        conversation_id=f"v03-update-{case_code}",
        persona_id=None,
    )
    store = _secure_repository(case_workspace, case_code)
    counter = ac.RuntimeUtf8ByteTokenCounter()
    input_sha256 = scenario.input_sha256

    try:
        _capture_event(
            store=store,
            session_key=session_key,
            scenario_code=scenario.scenario_code,
            trial=trial,
            sequence=1,
            content=scenario.initial_event,
        )
        store.raise_compaction_intent(
            job_id=f"{case_code}-baseline",
            session_key=session_key,
            target_high_water_mark=1,
            now=FROZEN_NOW + timedelta(seconds=10),
        )
        baseline = _run_fenced_worker(
            store=store,
            counter=counter,
            worker_id=f"{case_code}-baseline-worker",
            now=FROZEN_NOW + timedelta(seconds=20),
            token_budget=None,
        )
        baseline_status = _result_status_code(baseline)
        baseline_view = store.read_request_view(session_key)
        pointer_before = baseline_view.pointer_version
        if baseline_status != "COMMITTED" or baseline_view.snapshot is None:
            return _failed_row(
                scenario=scenario,
                trial=trial,
                baseline_status_code=baseline_status,
                input_sha256=input_sha256,
                pointer_before=pointer_before,
                failure_codes=("BASELINE_NOT_COMMITTED",),
            )

        _capture_event(
            store=store,
            session_key=session_key,
            scenario_code=scenario.scenario_code,
            trial=trial,
            sequence=2,
            content=scenario.update_event,
        )
        store.raise_compaction_intent(
            job_id=f"{case_code}-update",
            session_key=session_key,
            target_high_water_mark=2,
            now=FROZEN_NOW + timedelta(seconds=30),
        )
        update = _run_fenced_worker(
            store=store,
            counter=counter,
            worker_id=f"{case_code}-update-worker",
            now=FROZEN_NOW + timedelta(seconds=40),
            token_budget=token_budget,
        )
        update_status = _result_status_code(update)
        update_view = store.read_request_view(session_key)
        if update_status != "COMMITTED" or update_view.snapshot is None:
            return _failed_row(
                scenario=scenario,
                trial=trial,
                baseline_status_code=baseline_status,
                input_sha256=input_sha256,
                pointer_before=pointer_before,
                pointer_after=update_view.pointer_version,
                failure_codes=("UPDATE_NOT_COMMITTED", update_status),
            )

        records = store.read_snapshot_reorganization_records(update_view.snapshot.snapshot_id)
        failure_codes: list[str] = []
        if pointer_before != 1 or update_view.pointer_version != 2:
            failure_codes.append("ACTIVE_POINTER_NOT_1_TO_2")
        if not records:
            failure_codes.append("REORGANIZATION_LEDGER_EMPTY")
        non_summary_released = sum(
            record.status is ac.ReorganizationStatus.RELEASED and record.kind != "narrative_summary"
            for record in records
        )
        if non_summary_released:
            failure_codes.append("NON_SUMMARY_RELEASED_RECORD")
        required_kinds = {record.kind for record in records if record.required}
        if not {"exact_anchor", "dependency"}.issubset(required_kinds):
            failure_codes.append("REQUIRED_CORE_EDGE_MISSING")

        return _ReceiptRow(
            scenario_code=scenario.scenario_code,
            trial=trial,
            status_code="COMMITTED" if not failure_codes else "FAILED",
            baseline_status_code=baseline_status,
            input_sha256=input_sha256,
            snapshot_sha256=_sha256_text(update_view.snapshot.snapshot_id),
            ledger_sha256=_ledger_sha256(records),
            pointer_before=pointer_before,
            pointer_after=update_view.pointer_version,
            ledger_record_count=len(records),
            retained_record_count=sum(
                record.status is ac.ReorganizationStatus.RETAINED for record in records
            ),
            approximate_record_count=sum(
                record.status is ac.ReorganizationStatus.APPROXIMATE for record in records
            ),
            released_record_count=sum(
                record.status is ac.ReorganizationStatus.RELEASED for record in records
            ),
            non_summary_released_record_count=non_summary_released,
            required_record_count=sum(record.required for record in records),
            failure_codes=tuple(failure_codes),
        )
    except Exception:  # noqa: BLE001 - receipt contract must not expose exception text.
        return _failed_row(
            scenario=scenario,
            trial=trial,
            baseline_status_code="HARNESS_EXCEPTION",
            input_sha256=input_sha256,
            failure_codes=("HARNESS_EXCEPTION",),
        )


def _capture_event(
    *,
    store: ac.SQLiteRepository,
    session_key: ac.SessionKey,
    scenario_code: str,
    trial: int,
    sequence: int,
    content: str,
) -> None:
    counter = ac.RuntimeUtf8ByteTokenCounter()
    event_code = f"{scenario_code}-T{trial:02d}-E{sequence}"
    store.capture_user_event(
        event_id=f"event-{event_code}",
        session_key=session_key,
        content=content,
        idempotency_key=f"capture-{event_code}",
        token_count=counter.count_text(content),
        canonical=CanonicalMetricObservation(CANONICAL_O200K.profile_id, None),
        created_at=FROZEN_NOW + timedelta(seconds=sequence),
    )


def _run_fenced_worker(
    *,
    store: ac.SQLiteRepository,
    counter: ac.RuntimeUtf8ByteTokenCounter,
    worker_id: str,
    now: datetime,
    token_budget: int | None,
) -> ac.CompactionRunResult | None:
    worker = ac.CompactionWorker(
        repository=store,
        compiler_backend=_DeterministicAppendBackend(),
        counter=counter,
        clock=lambda: now,
        config=ac.CompactionWorkerConfig(
            token_ceiling=DEFAULT_TOKEN_BUDGET,
            worker_id=worker_id,
            lease_duration=timedelta(minutes=5),
            retry_delay=timedelta(seconds=1),
            reorganization_token_budget=token_budget,
        ),
    )
    return asyncio.run(worker.run_once())


def _secure_repository(case_workspace: Path, case_code: str) -> ac.SQLiteRepository:
    key = hashlib.sha256(f"v03-update-benchmark:{case_code}".encode()).digest()
    factory = ac.SQLiteConnectionFactory(case_workspace, busy_timeout_ms=5_000)
    activation = ac.activate_storage_security(
        factory,
        ac.ResolvedKeyMaterial(
            active=ac.KeyMaterial.from_raw(key),
            previous=None,
            source=ac.KeySource.ENVIRONMENT,
            local_degraded=False,
        ),
    )
    return ac.SQLiteRepository(factory, codec=activation.codec)


def _result_status_code(result: ac.CompactionRunResult | None) -> str:
    if result is None:
        return "NO_JOB"
    if result.publication is not None:
        return result.publication.outcome.value
    return result.job.error_code or result.job.state.value


def _ledger_sha256(records: Sequence[ac.ReorganizationRecord]) -> str:
    return _sha256_json(
        [
            {
                "kind": record.kind,
                "item_id": record.item_id,
                "source_capsule_id": record.source_capsule_id,
                "status": record.status.value,
                "before_tokens": record.before_tokens,
                "after_tokens": record.after_tokens,
                "required": record.required,
            }
            for record in records
        ]
    )


def _failed_row(
    *,
    scenario: FrozenUpdateScenario,
    trial: int,
    baseline_status_code: str,
    input_sha256: str,
    pointer_before: int = 0,
    pointer_after: int = 0,
    failure_codes: tuple[str, ...],
) -> _ReceiptRow:
    return _ReceiptRow(
        scenario_code=scenario.scenario_code,
        trial=trial,
        status_code="FAILED",
        baseline_status_code=baseline_status_code,
        input_sha256=input_sha256,
        snapshot_sha256=EMPTY_SHA256,
        ledger_sha256=EMPTY_SHA256,
        pointer_before=pointer_before,
        pointer_after=pointer_after,
        ledger_record_count=0,
        retained_record_count=0,
        approximate_record_count=0,
        released_record_count=0,
        non_summary_released_record_count=0,
        required_record_count=0,
        failure_codes=failure_codes,
    )


def _write_receipts(
    *,
    output_dir: Path,
    fixture_sha256: str,
    trials: int,
    rows: list[dict[str, object]],
    aggregate: dict[str, object],
    result_sha256: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "execution_path": EXECUTION_PATH,
        "ordinary_provider_runtime_reorganization_wired": (
            ORDINARY_PROVIDER_RUNTIME_REORGANIZATION_WIRED
        ),
        "e2e_summary_status": E2E_SUMMARY_STATUS,
        "fixture_sha256": fixture_sha256,
        "scenario_count": EXPECTED_SCENARIO_COUNT,
        "trials_per_scenario": trials,
        "result_sha256": result_sha256,
        "rows": rows,
    }
    (output_dir / "receipt.json").write_bytes(
        (json.dumps(receipt, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    with (output_dir / "receipt.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=RECEIPT_ROW_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "failure_codes": ";".join(str(code) for code in row["failure_codes"]),
                }
            )
    with (output_dir / "aggregate.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=AGGREGATE_ROW_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(aggregate)


def _aggregate_row(
    *,
    rows: Sequence[_ReceiptRow],
    fixture_sha256: str,
    result_sha256: str,
) -> dict[str, object]:
    """Return the public, content-free Gate A aggregate for one frozen run."""

    return {
        "experiment_id": EXPERIMENT_ID,
        "scope": EXPERIMENT_SCOPE,
        "execution_path": EXECUTION_PATH,
        "denominator": len(rows),
        "baseline_committed": sum(row.baseline_status_code == "COMMITTED" for row in rows),
        "update_committed": sum(row.status_code == "COMMITTED" for row in rows),
        "pointer_advanced": sum(row.pointer_before == 1 and row.pointer_after == 2 for row in rows),
        "durable_ledger": sum(
            bool(row.ledger_sha256) and row.ledger_record_count > 0 for row in rows
        ),
        "required_core_edges": sum(row.required_record_count >= 2 for row in rows),
        "non_summary_released_record_count": sum(
            row.non_summary_released_record_count for row in rows
        ),
        "ordinary_provider_runtime_reorganization_wired": (
            ORDINARY_PROVIDER_RUNTIME_REORGANIZATION_WIRED
        ),
        "e2e_summary_status": E2E_SUMMARY_STATUS,
        "fixture_sha256": fixture_sha256,
        "result_sha256": result_sha256,
    }


def _validate_workspace(workspace: Path) -> None:
    resolved_workspace = workspace.resolve()
    if _is_relative_to(resolved_workspace, REPOSITORY_ROOT.resolve()):
        raise ValueError("workspace must not be the repository root or inside it")


def _validate_output_directory(output_dir: Path) -> None:
    if any(
        (output_dir / name).exists() for name in ("receipt.json", "receipt.csv", "aggregate.csv")
    ):
        raise FileExistsError("output directory already contains a benchmark receipt")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_json(value: object) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=FROZEN_TRIALS)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the frozen experiment CLI without printing source content."""

    args = _parse_args(argv)
    summary = run_v03_update_benchmark(
        scenarios_path=args.scenarios,
        output_dir=args.output_dir,
        workspace=args.workspace,
        trials=args.trials,
        token_budget=args.token_budget,
    )
    print(
        json.dumps(
            {
                "committed_trials": summary.committed_trials,
                "result_sha256": summary.result_sha256,
                "total_trials": summary.total_trials,
            },
            sort_keys=True,
        )
    )
    return 0 if summary.committed_trials == summary.total_trials else 1


if __name__ == "__main__":
    raise SystemExit(main())
