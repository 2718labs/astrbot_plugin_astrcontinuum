"""Render the frozen R2 aggregate evidence as a deterministic SVG figure."""

from __future__ import annotations

import argparse
import csv
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_INPUT: Final = Path("docs/evidence/frozen-r2-outcomes.csv")
DEFAULT_OUTPUT: Final = Path("docs/assets/evidence-r2-outcomes-rmb.svg")
FIXED_DENOMINATOR: Final = 36
REQUIRED_FIELDS: Final = (
    "arm",
    "structural_valid",
    "denominator",
    "answer_delivered",
    "claim_v2_end_to_end",
    "scope_note",
)
ARM_ORDER: Final = ("Full capsule", "Projection", "Summary", "Oracle floor")
OBSERVED_ARMS: Final = ARM_ORDER[:-1]
REGISTERED_SCOPE_NOTES: Final = {
    "Full capsule": "Frozen synthetic R2; research-only",
    "Projection": "Frozen synthetic R2; research-only",
    "Summary": "Offline comparison only; not a runtime fallback",
    "Oracle floor": "Registered encoding floor; no answer metric",
}


class EvidenceFigureError(ValueError):
    """Raised when the committed aggregate cannot support the fixed layout."""


@dataclass(frozen=True)
class Outcome:
    """One aggregate outcome arm from the frozen R2 evidence CSV."""

    arm: str
    denominator: int
    structural_valid: int
    answer_delivered: int | None
    claim_v2_end_to_end: int | None


def _parse_count(row: dict[str, str], field: str, *, required: bool) -> int | None:
    raw_value = row[field].strip()
    if not raw_value:
        if required:
            raise EvidenceFigureError(f"{row['arm']}: {field} is required")
        return None
    try:
        value = int(raw_value)
    except ValueError as error:
        raise EvidenceFigureError(f"{row['arm']}: {field} is not an integer") from error
    if value < 0:
        raise EvidenceFigureError(f"{row['arm']}: {field} must be non-negative")
    return value


def _read_outcomes(input_path: Path) -> dict[str, Outcome]:
    with input_path.open(encoding="utf-8", newline="") as source:
        try:
            reader = csv.DictReader(source, strict=True)
            if tuple(reader.fieldnames or ()) != REQUIRED_FIELDS:
                raise EvidenceFigureError("frozen R2 CSV columns do not match the public contract")
            raw_rows = list(reader)
        except csv.Error as error:
            raise EvidenceFigureError("frozen R2 CSV contract violation: invalid CSV") from error

    outcomes: dict[str, Outcome] = {}
    for line_number, row in enumerate(raw_rows, start=2):
        if None in row or any(row[field] is None for field in REQUIRED_FIELDS):
            raise EvidenceFigureError(
                f"frozen R2 CSV contract violation: row {line_number} has a missing or extra value"
            )
        arm = row["arm"].strip()
        if arm in outcomes:
            raise EvidenceFigureError(f"duplicate arm: {arm}")
        if arm not in ARM_ORDER:
            raise EvidenceFigureError(f"unexpected arm: {arm}")
        if row["scope_note"].strip() != REGISTERED_SCOPE_NOTES[arm]:
            raise EvidenceFigureError(
                f"{arm}: frozen R2 CSV contract violation: scope_note is not registered"
            )

        denominator = _parse_count(row, "denominator", required=True)
        structural_valid = _parse_count(row, "structural_valid", required=True)
        assert denominator is not None
        assert structural_valid is not None
        outcomes[arm] = Outcome(
            arm=arm,
            denominator=denominator,
            structural_valid=structural_valid,
            answer_delivered=_parse_count(
                row,
                "answer_delivered",
                required=arm in OBSERVED_ARMS,
            ),
            claim_v2_end_to_end=_parse_count(
                row,
                "claim_v2_end_to_end",
                required=arm in OBSERVED_ARMS,
            ),
        )

    if tuple(outcomes) != ARM_ORDER:
        raise EvidenceFigureError("frozen R2 CSV arms do not match the public layout")

    for outcome in outcomes.values():
        if outcome.denominator != FIXED_DENOMINATOR:
            raise EvidenceFigureError(
                f"{outcome.arm}: fixed denominator must be {FIXED_DENOMINATOR}"
            )
        for value in (
            outcome.structural_valid,
            outcome.answer_delivered,
            outcome.claim_v2_end_to_end,
        ):
            if value is not None and value > outcome.denominator:
                raise EvidenceFigureError(f"{outcome.arm}: outcome count exceeds its denominator")

    oracle = outcomes["Oracle floor"]
    if oracle.answer_delivered is not None or oracle.claim_v2_end_to_end is not None:
        raise EvidenceFigureError("Oracle floor must remain structural-only")
    return outcomes


def _rate_label(count: int, denominator: int) -> str:
    return f"{count}/{denominator} ({count / denominator * 100:.1f}%)"


def _point_x(count: int, denominator: int, *, start: int, end: int) -> int:
    return round(start + (end - start) * count / denominator)


def _render_svg(outcomes: dict[str, Outcome], *, source_label: str) -> str:
    structure_y = dict(zip(ARM_ORDER, (372, 442, 512, 582), strict=True))
    delivery_y = dict(zip(OBSERVED_ARMS, (380, 470, 560), strict=True))
    claim_y = dict(zip(OBSERVED_ARMS, (407, 497, 587), strict=True))

    structure_points = "\n".join(
        f'      <circle cx="{_point_x(outcome.structural_valid, outcome.denominator, start=305, end=721)}" cy="{structure_y[arm]}" r="10"/>'
        for arm, outcome in outcomes.items()
    )
    structure_labels = "\n".join(
        f'      <text x="{_point_x(outcome.structural_valid, outcome.denominator, start=305, end=721)}" y="{structure_y[arm] - 22}">{_rate_label(outcome.structural_valid, outcome.denominator)}</text>'
        for arm, outcome in outcomes.items()
    )

    delivery_points = "\n".join(
        f'      <rect x="{_point_x(outcome.answer_delivered or 0, outcome.denominator, start=1040, end=1320) - 9}" y="{delivery_y[arm] - 9}" width="18" height="18"/>'
        for arm, outcome in outcomes.items()
        if arm in OBSERVED_ARMS
    )
    claim_points = "\n".join(
        _diamond(
            _point_x(outcome.claim_v2_end_to_end or 0, outcome.denominator, start=1040, end=1320),
            claim_y[arm],
        )
        for arm, outcome in outcomes.items()
        if arm in OBSERVED_ARMS
    )
    observed_labels = "\n".join(
        line
        for arm, outcome in outcomes.items()
        if arm in OBSERVED_ARMS
        for line in (
            f'      <text x="1344" y="{delivery_y[arm] + 6}">{_rate_label(outcome.answer_delivered or 0, outcome.denominator)}</text>',
            f'      <text x="1344" y="{claim_y[arm] + 7}">{_rate_label(outcome.claim_v2_end_to_end or 0, outcome.denominator)}</text>',
        )
    )

    arm_labels = "\n".join(
        f'      <text x="285" y="{structure_y[arm] + 6}">{html.escape(arm)}{_footnote(arm)}</text>'
        for arm in ARM_ORDER
    )
    observed_arm_labels = "\n".join(
        f'      <text x="1020" y="{delivery_y[arm] + 10}">{html.escape(arm)}{_footnote(arm)}</text>'
        for arm in OBSERVED_ARMS
    )

    source_label = html.escape(source_label)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="960" viewBox="0 0 1600 960" role="img" aria-labelledby="title desc">
  <title id="title">Figure R2. Frozen synthetic evaluation: fixed-denominator outcomes</title>
  <desc id="desc">Two-panel dot plot of committed aggregate outcomes from a frozen synthetic evaluation. Panel A shows structural validity for four arms. Panel B shows answer delivery and claim-v2 end-to-end outcomes for the three arms with observed outcomes. Every value is reported as count out of 36 and percent. This is not a v0.3 evaluation or a release-to-release performance comparison. The figure is research-only and does not establish production performance or model quality.</desc>
  <rect width="1600" height="960" fill="#FFFFFF"/>

  <!-- Generated by scripts/render_r2_evidence_figure.py. -->
  <g font-family="Arial, Helvetica, sans-serif" fill="#263238">
    <text x="120" y="82" font-family="Georgia, 'Times New Roman', serif" font-size="34" font-weight="700">Figure R2. Frozen synthetic evaluation: fixed-denominator outcomes</text>
    <text x="120" y="116" fill="#5F5A53" font-size="19">Pre-v0.3 historical R2 aggregate, n = 36 (research-only)</text>
    <line x1="120" y1="146" x2="1480" y2="146" stroke="#263238" stroke-width="2"/>

    <g aria-label="Outcome legend" font-size="18">
      <circle cx="132" cy="190" r="9" fill="#857B70" stroke="#263238" stroke-width="1.5"/>
      <text x="150" y="196">Structural validity</text>
      <rect x="366" y="181" width="18" height="18" fill="#1F6B5B" stroke="#263238" stroke-width="1.5"/>
      <text x="396" y="196">Answer delivered</text>
      <path d="M 620 181 L 630 190 L 620 199 L 610 190 Z" fill="#A44742" stroke="#263238" stroke-width="1.5"/>
      <text x="642" y="196">claim-v2 end-to-end</text>
    </g>

    <text x="120" y="260" font-family="Georgia, 'Times New Roman', serif" font-size="32" font-weight="700">A</text>
    <text x="164" y="260" font-size="25" font-weight="700">Structural validity</text>
    <text x="164" y="288" fill="#5F5A53" font-size="16">All registered arms; one neutral marker per arm</text>

    <g stroke="#DDD6CC" stroke-width="1.5">
      <line x1="305" y1="340" x2="305" y2="600"/>
      <line x1="409" y1="340" x2="409" y2="600"/>
      <line x1="513" y1="340" x2="513" y2="600"/>
      <line x1="617" y1="340" x2="617" y2="600"/>
      <line x1="721" y1="340" x2="721" y2="600"/>
    </g>
    <line x1="305" y1="600" x2="721" y2="600" stroke="#263238" stroke-width="2"/>
    <g fill="#5F5A53" font-size="16" text-anchor="middle">
      <text x="305" y="326">0</text>
      <text x="409" y="326">25</text>
      <text x="513" y="326">50</text>
      <text x="617" y="326">75</text>
      <text x="721" y="326">100</text>
    </g>
    <g font-size="18" text-anchor="end">
{arm_labels}
    </g>
    <g fill="#857B70" stroke="#263238" stroke-width="2">
{structure_points}
    </g>
    <g font-size="16" text-anchor="middle">
{structure_labels}
    </g>
    <text x="513" y="638" font-size="17" font-weight="700" text-anchor="middle">Successful units / fixed denominator (%)</text>
    <text x="120" y="680" fill="#5F5A53" font-size="17">‡ Oracle floor: structural-only reference; delivery and claim-v2 are not applicable.</text>

    <text x="830" y="260" font-family="Georgia, 'Times New Roman', serif" font-size="32" font-weight="700">B</text>
    <text x="874" y="260" font-size="25" font-weight="700">Observed delivery and end-to-end outcomes</text>
    <text x="874" y="288" fill="#5F5A53" font-size="16">Arms with observed outcomes; marker shape and colour both encode the outcome</text>

    <g stroke="#DDD6CC" stroke-width="1.5">
      <line x1="1040" y1="340" x2="1040" y2="600"/>
      <line x1="1110" y1="340" x2="1110" y2="600"/>
      <line x1="1180" y1="340" x2="1180" y2="600"/>
      <line x1="1250" y1="340" x2="1250" y2="600"/>
      <line x1="1320" y1="340" x2="1320" y2="600"/>
    </g>
    <line x1="1040" y1="600" x2="1320" y2="600" stroke="#263238" stroke-width="2"/>
    <g fill="#5F5A53" font-size="16" text-anchor="middle">
      <text x="1040" y="326">0</text>
      <text x="1110" y="326">25</text>
      <text x="1180" y="326">50</text>
      <text x="1250" y="326">75</text>
      <text x="1320" y="326">100</text>
    </g>
    <g font-size="18" text-anchor="end">
{observed_arm_labels}
    </g>
    <g fill="#1F6B5B" stroke="#263238" stroke-width="1.8">
{delivery_points}
    </g>
    <g fill="#A44742" stroke="#263238" stroke-width="1.8">
{claim_points}
    </g>
    <g font-size="17">
{observed_labels}
    </g>
    <text x="1180" y="638" font-size="17" font-weight="700" text-anchor="middle">Successful units / fixed denominator (%)</text>
    <text x="830" y="680" fill="#5F5A53" font-size="17">† Summary: offline comparator, never a runtime fallback.</text>

    <line x1="120" y1="726" x2="1480" y2="726" stroke="#263238" stroke-width="1.5"/>
    <text x="120" y="766" font-size="18"><tspan font-family="Georgia, 'Times New Roman', serif" font-weight="700">Figure note.</tspan><tspan dx="8">Fixed-denominator success rates in the pre-v0.3 historical R2 aggregate (n = 36).</tspan></text>
    <text x="120" y="796" font-size="17">Values are committed aggregate counts, shown as count/36 (%). No uncertainty interval or hypothesis test is available from this aggregate.</text>
    <text x="120" y="836" fill="#5F5A53" font-size="18">Source and provenance: {source_label}; see docs/EVIDENCE.md.</text>
    <text x="120" y="876" fill="#A44742" font-size="18" font-weight="700">Scope boundary: frozen R2 evidence; not a v0.3 evaluation or a release-to-release performance comparison.</text>
    <text x="120" y="906" fill="#A44742" font-size="18" font-weight="700">It does not establish real-user, real-semantic-model, or Provider results.</text>
    <text x="120" y="936" fill="#A44742" font-size="18" font-weight="700">It also does not establish v0.3 production-performance or release-readiness results.</text>
  </g>
</svg>
"""


def _diamond(x: int, y: int) -> str:
    return f'      <path d="M {x} {y - 10} L {x + 10} {y} L {x} {y + 10} L {x - 10} {y} Z"/>'


def _footnote(arm: str) -> str:
    if arm == "Summary":
        return "†"
    if arm == "Oracle floor":
        return "‡"
    return ""


def render_r2_evidence_figure(input_path: Path, output_path: Path) -> None:
    """Render an evidence SVG from the committed frozen R2 aggregate."""

    if input_path.resolve() == output_path.resolve():
        raise EvidenceFigureError("output path must differ from input path")
    outcomes = _read_outcomes(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        _render_svg(
            outcomes,
            source_label=(
                input_path.resolve().as_posix()
                if input_path.is_absolute()
                else input_path.as_posix()
            ),
        ).encode("utf-8")
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the frozen R2 aggregate evidence figure as SVG."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    """Render the public evidence figure and return a shell-friendly status."""

    arguments = _parse_args()
    try:
        render_r2_evidence_figure(arguments.input, arguments.output)
    except (OSError, EvidenceFigureError) as error:
        raise SystemExit(f"evidence figure export failed: {error}") from error
    print(f"evidence_figure={arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
