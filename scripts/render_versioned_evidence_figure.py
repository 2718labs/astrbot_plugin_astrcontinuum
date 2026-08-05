"""Render separated historical R2 and v0.3 Gate A evidence as one SVG."""

from __future__ import annotations

import argparse
import csv
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from render_r2_evidence_figure import (
    ARM_ORDER,
    FIXED_DENOMINATOR,
    EvidenceFigureError,
    Outcome,
    _point_x,
    _rate_label,
    _read_outcomes,
)

DEFAULT_R2_INPUT: Final = Path("docs/evidence/frozen-r2-outcomes.csv")
DEFAULT_GATE_A_INPUT: Final = Path("docs/evidence/v03-update-gate-a/aggregate.csv")
DEFAULT_OUTPUT: Final = Path("docs/assets/evidence-r2-v03-evidence.svg")
GATE_A_FIELDS: Final = (
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
GATE_A_CHECKS: Final = (
    ("Baseline publication committed", "baseline_committed"),
    ("Reorganized update committed", "update_committed"),
    ("Active pointer exactly 1 to 2", "pointer_advanced"),
    ("Durable reorganization ledger", "durable_ledger"),
    ("Required core edges retained", "required_core_edges"),
)


@dataclass(frozen=True)
class GateAOutcome:
    """The bounded structural outcomes admitted from one Gate A aggregate."""

    denominator: int
    check_counts: tuple[tuple[str, int], ...]
    non_summary_released_record_count: int


def _parse_nonnegative(row: dict[str, str], field: str) -> int:
    raw_value = row[field].strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise EvidenceFigureError(f"{field} is not an integer") from error
    if value < 0:
        raise EvidenceFigureError(f"{field} must be non-negative")
    return value


def _read_gate_a(input_path: Path) -> GateAOutcome:
    with input_path.open(encoding="utf-8", newline="") as source:
        try:
            reader = csv.DictReader(source, strict=True)
            if tuple(reader.fieldnames or ()) != GATE_A_FIELDS:
                raise EvidenceFigureError("Gate A CSV columns do not match the public contract")
            rows = list(reader)
        except csv.Error as error:
            raise EvidenceFigureError("Gate A CSV contract violation: invalid CSV") from error

    if len(rows) != 1:
        raise EvidenceFigureError("Gate A aggregate must contain exactly one row")
    row = rows[0]
    if None in row or any(row[field] is None for field in GATE_A_FIELDS):
        raise EvidenceFigureError("Gate A aggregate has a missing or extra value")
    if row["experiment_id"].strip() != "V03-WIRE-001":
        raise EvidenceFigureError("experiment_id must remain V03-WIRE-001")
    if row["scope"].strip() != "synthetic_provider_free_wiring_gate":
        raise EvidenceFigureError("scope must remain synthetic_provider_free_wiring_gate")
    if row["execution_path"].strip() != "opt_in_fenced_compaction_worker":
        raise EvidenceFigureError("execution_path must remain opt_in_fenced_compaction_worker")
    if row["ordinary_provider_runtime_reorganization_wired"].strip() != "False":
        raise EvidenceFigureError("ordinary Provider runtime must remain unwired")
    if row["e2e_summary_status"].strip() != "BLOCKED":
        raise EvidenceFigureError("e2e_summary_status must remain BLOCKED")

    denominator = _parse_nonnegative(row, "denominator")
    if denominator != FIXED_DENOMINATOR:
        raise EvidenceFigureError(f"Gate A fixed denominator must be {FIXED_DENOMINATOR}")
    check_counts = tuple((label, _parse_nonnegative(row, field)) for label, field in GATE_A_CHECKS)
    if any(count > denominator for _, count in check_counts):
        raise EvidenceFigureError("Gate A check count exceeds its denominator")
    non_summary_released_record_count = _parse_nonnegative(
        row,
        "non_summary_released_record_count",
    )
    if non_summary_released_record_count != 0:
        raise EvidenceFigureError("non_summary_released_record_count must remain 0")
    return GateAOutcome(
        denominator=denominator,
        check_counts=check_counts,
        non_summary_released_record_count=non_summary_released_record_count,
    )


def _source_label(path: Path) -> str:
    return path.resolve().as_posix() if path.is_absolute() else path.as_posix()


def _r2_rows(outcomes: dict[str, Outcome]) -> tuple[tuple[str, int, int, str, str], ...]:
    rows: list[tuple[str, int, int, str, str]] = []
    metric_specs = (
        ("structural", "structural_valid", "circle", "#857B70"),
        ("answer", "answer_delivered", "square", "#1F6B5B"),
        ("claim-v2", "claim_v2_end_to_end", "diamond", "#A44742"),
    )
    for arm in ARM_ORDER[:-1]:
        outcome = outcomes[arm]
        for metric, field, marker, colour in metric_specs:
            value = getattr(outcome, field)
            assert value is not None
            rows.append((f"{arm} / {metric}", value, outcome.denominator, marker, colour))
    oracle = outcomes["Oracle floor"]
    rows.append(
        (
            "Oracle floor / structural only",
            oracle.structural_valid,
            oracle.denominator,
            "circle",
            "#857B70",
        )
    )
    return tuple(rows)


def _marker(*, marker: str, x: int, y: int, colour: str) -> str:
    if marker == "circle":
        return f'      <circle cx="{x}" cy="{y}" r="8" fill="{colour}"/>'
    if marker == "square":
        return f'      <rect x="{x - 8}" y="{y - 8}" width="16" height="16" fill="{colour}"/>'
    return (
        f'      <path d="M {x} {y - 9} L {x + 9} {y} L {x} {y + 9} '
        f'L {x - 9} {y} Z" fill="{colour}"/>'
    )


def _render_svg(
    outcomes: dict[str, Outcome],
    gate_a: GateAOutcome,
    *,
    r2_source_label: str,
    gate_a_source_label: str,
) -> str:
    r2_rows = _r2_rows(outcomes)
    r2_labels: list[str] = []
    r2_markers: list[str] = []
    r2_values: list[str] = []
    for index, (label, count, denominator, marker, colour) in enumerate(r2_rows):
        y = 326 + index * 47
        x = _point_x(count, denominator, start=382, end=760)
        r2_labels.append(f'      <text x="360" y="{y + 5}">{html.escape(label)}</text>')
        r2_markers.append(_marker(marker=marker, x=x, y=y, colour=colour))
        r2_values.append(
            f'      <text x="778" y="{y + 5}">{_rate_label(count, denominator)}</text>'
        )

    gate_rows: list[str] = []
    for index, (label, count) in enumerate(gate_a.check_counts):
        y = 358 + index * 66
        gate_rows.extend(
            (
                f'      <line x1="920" y1="{y + 26}" x2="1584" y2="{y + 26}" stroke="#DDD6CC" stroke-width="1"/>',
                f'      <text x="940" y="{y}">{html.escape(label)}</text>',
                f'      <text x="1560" y="{y}" text-anchor="end" font-weight="700">{count} / {gate_a.denominator} verified units</text>',
            )
        )

    r2_source_label = html.escape(r2_source_label)
    gate_a_source_label = html.escape(gate_a_source_label)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1760" height="1080" viewBox="0 0 1760 1080" role="img" aria-labelledby="title desc">
  <title id="title">Figure EV-01. Version-separated evidence map</title>
  <desc id="desc">Two clearly separated evidence panels. Panel A is a pre-v0.3 historical frozen R2 synthetic aggregate with outcome counts out of 36. Panel B is v0.3 Gate A structural and ledger wiring verification, reported as verified units without a performance axis. The panels are not comparable. End-to-end comparison against refreshed Summary is blocked.</desc>
  <rect width="1760" height="1080" fill="#FFFFFF"/>
  <!-- Generated by scripts/render_versioned_evidence_figure.py. -->
  <g font-family="Arial, Helvetica, sans-serif" fill="#263238">
    <text x="110" y="78" font-family="Georgia, 'Times New Roman', serif" font-size="35" font-weight="700">Figure EV-01. Version-separated evidence map</text>
    <text x="110" y="113" fill="#5F5A53" font-size="19">Separate historical outcomes from structural wiring evidence; both records use n = 36, but their endpoints are not comparable.</text>
    <line x1="110" y1="146" x2="1650" y2="146" stroke="#263238" stroke-width="2"/>
    <rect x="110" y="170" width="1540" height="48" fill="#FFF4F1" stroke="#A44742" stroke-width="1.5"/>
    <text x="880" y="202" fill="#A44742" font-size="19" font-weight="700" text-anchor="middle">NOT A PERFORMANCE COMPARISON — no shared outcome axis, trend, delta, or ranking</text>

    <rect x="110" y="246" width="710" height="620" fill="#FFFEFC" stroke="#857B70" stroke-width="1.5"/>
    <text x="142" y="288" font-family="Georgia, 'Times New Roman', serif" font-size="30" font-weight="700">A</text>
    <text x="184" y="288" font-size="24" font-weight="700">Pre-v0.3 / R2 historical evidence</text>
    <text x="184" y="316" fill="#5F5A53" font-size="16">Frozen synthetic aggregate; count / 36 (%) — research-only</text>
    <g stroke="#DDD6CC" stroke-width="1">
      <line x1="382" y1="330" x2="382" y2="792"/>
      <line x1="477" y1="330" x2="477" y2="792"/>
      <line x1="571" y1="330" x2="571" y2="792"/>
      <line x1="666" y1="330" x2="666" y2="792"/>
      <line x1="760" y1="330" x2="760" y2="792"/>
    </g>
    <g fill="#5F5A53" font-size="15" text-anchor="middle">
      <text x="382" y="348">0</text>
      <text x="477" y="348">25</text>
      <text x="571" y="348">50</text>
      <text x="666" y="348">75</text>
      <text x="760" y="348">100</text>
    </g>
    <g font-size="16" text-anchor="end">
{chr(10).join(r2_labels)}
    </g>
    <g stroke="#263238" stroke-width="1.5">
{chr(10).join(r2_markers)}
    </g>
    <g font-size="15">
{chr(10).join(r2_values)}
    </g>
    <g font-size="15">
      <circle cx="164" cy="826" r="7" fill="#857B70" stroke="#263238" stroke-width="1.2"/>
      <text x="180" y="831">structural</text>
      <rect x="300" y="819" width="14" height="14" fill="#1F6B5B" stroke="#263238" stroke-width="1.2"/>
      <text x="324" y="831">answer</text>
      <path d="M 432 819 L 439 826 L 432 833 L 425 826 Z" fill="#A44742" stroke="#263238" stroke-width="1.2"/>
      <text x="448" y="831">claim-v2</text>
    </g>

    <rect x="860" y="246" width="790" height="620" fill="#FFFEFC" stroke="#1F6B5B" stroke-width="1.5"/>
    <text x="892" y="288" font-family="Georgia, 'Times New Roman', serif" font-size="30" font-weight="700">B</text>
    <text x="934" y="288" font-size="24" font-weight="700">v0.3 / Gate A wiring verification</text>
    <text x="934" y="316" fill="#5F5A53" font-size="16">Structural / ledger only; not an E2E outcome</text>
    <rect x="920" y="332" width="664" height="370" fill="#F8FBF9" stroke="#1F6B5B" stroke-width="1.2"/>
    <g font-size="17">
{chr(10).join(gate_rows)}
    </g>
    <rect x="920" y="730" width="664" height="102" fill="#FFFDF9" stroke="#857B70" stroke-width="1.2"/>
    <g font-size="16">
      <text x="940" y="760">Non-summary released records: {gate_a.non_summary_released_record_count}</text>
      <text x="940" y="789">Ordinary Provider runtime wired: false</text>
      <text x="940" y="818" fill="#A44742" font-weight="700">E2E versus refreshed Summary: BLOCKED</text>
    </g>

    <line x1="110" y1="908" x2="1650" y2="908" stroke="#263238" stroke-width="1.5"/>
    <text x="110" y="948" font-family="Georgia, 'Times New Roman', serif" font-size="18" font-weight="700">Figure note.</text>
    <text x="236" y="948" font-size="18">Panel A is a pre-v0.3 historical R2 aggregate; Panel B is deterministic opt-in worker wiring evidence.</text>
    <text x="110" y="979" fill="#5F5A53" font-size="17">No shared outcome axis: the identical denominator does not turn contract checks into answer-quality or performance results.</text>
    <text x="110" y="1010" fill="#5F5A53" font-size="16">Sources: {r2_source_label}; {gate_a_source_label}.</text>
    <text x="110" y="1042" fill="#A44742" font-size="17" font-weight="700">No Provider benchmark, model-quality claim, release-readiness claim, or v0.3-versus-Summary result is represented.</text>
  </g>
</svg>
"""


def render_versioned_evidence_figure(
    r2_input_path: Path,
    gate_a_input_path: Path,
    output_path: Path,
) -> None:
    """Render one separated figure from the committed R2 and Gate A extracts."""

    resolved_output = output_path.resolve()
    if resolved_output in {r2_input_path.resolve(), gate_a_input_path.resolve()}:
        raise EvidenceFigureError("output path must differ from evidence inputs")
    outcomes = _read_outcomes(r2_input_path)
    gate_a = _read_gate_a(gate_a_input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        _render_svg(
            outcomes,
            gate_a,
            r2_source_label=_source_label(r2_input_path),
            gate_a_source_label=_source_label(gate_a_input_path),
        ).encode("utf-8")
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render separated R2 historical and v0.3 Gate A evidence as SVG."
    )
    parser.add_argument("--r2-input", type=Path, default=DEFAULT_R2_INPUT)
    parser.add_argument("--gate-a-input", type=Path, default=DEFAULT_GATE_A_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    """Render the version-separated public evidence figure."""

    arguments = _parse_args()
    try:
        render_versioned_evidence_figure(
            arguments.r2_input,
            arguments.gate_a_input,
            arguments.output,
        )
    except (OSError, EvidenceFigureError) as error:
        raise SystemExit(f"versioned evidence figure export failed: {error}") from error
    print(f"versioned_evidence_figure={arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
