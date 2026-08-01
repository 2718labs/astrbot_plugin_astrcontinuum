"""Deterministic offline baselines and named CRM ablations."""

from __future__ import annotations

from dataclasses import dataclass, replace

from crm_experiment.canonical import canonical_json, utf8_bytes
from crm_experiment.contracts import (
    CapsuleState,
    KernelSchema,
    LossPolicy,
    ProjectionResult,
    QuerySpec,
    RecompositionRequest,
    SemanticAtom,
)
from crm_experiment.kernel import select_kernel
from crm_experiment.matrix import atomize
from crm_experiment.projection import UNKNOWN
from crm_experiment.recompose import recompose_capsule

ARM_ORDER = (
    "crm",
    "legacy_capsule",
    "recursive_summary",
    "crm_greedy",
    "crm_no_projection",
    "crm_no_kernel",
)


def _state_bytes(state: CapsuleState) -> int:
    return utf8_bytes(canonical_json(state))


def _max_as_of(base: CapsuleState | None, atoms: tuple[SemanticAtom, ...]) -> int:
    previous = 0 if base is None else base.high_water
    return max((previous, *(atom.as_of for atom in atoms)))


def _make_state(
    base: CapsuleState | None,
    atoms: tuple[SemanticAtom, ...],
    kernel: tuple[SemanticAtom, ...],
    body: tuple[SemanticAtom, ...],
    byte_budget: int,
    weight_version: str,
) -> CapsuleState:
    return CapsuleState(
        generation=1 if base is None else base.generation + 1,
        high_water=_max_as_of(base, atoms),
        accepted_budget=byte_budget,
        kernel=kernel,
        body=body,
        weight_version=weight_version,
    )


class LegacyCapsuleBaseline:
    """Direct-only scalar weight/byte baseline with stable tie-breaking."""

    def advance(
        self,
        base_state: CapsuleState | None,
        delta: tuple[SemanticAtom, ...],
        byte_budget: int,
        kernel_schema: KernelSchema,
        loss_policy: LossPolicy,
        weight_version: str,
    ) -> CapsuleState:
        del loss_policy
        atoms = atomize(base_state, delta)
        kernel_selection = select_kernel(atoms, kernel_schema)
        kernel = kernel_selection.atoms if kernel_selection.valid else ()
        kernel_ids = {atom.atom_id for atom in kernel}
        candidates = sorted(
            (
                atom
                for atom in atoms
                if atom.atom_id not in kernel_ids and atom.status.value == "active"
            ),
            key=lambda atom: (
                -(atom.weight / max(1, utf8_bytes(atom.text))),
                atom.atom_id,
            ),
        )
        selected: list[SemanticAtom] = []
        for atom in candidates:
            trial = _make_state(
                base_state,
                atoms,
                kernel,
                tuple(selected + [atom]),
                byte_budget,
                weight_version,
            )
            if _state_bytes(trial) <= byte_budget:
                selected.append(atom)
        return _make_state(
            base_state,
            atoms,
            kernel,
            tuple(sorted(selected, key=lambda atom: (atom.role.value, atom.atom_id))),
            byte_budget,
            weight_version,
        )


class NoProjectionAblation:
    """State-equivalent arm whose runner injects a query-blind stable prefix."""

    def advance(
        self,
        base_state: CapsuleState | None,
        delta: tuple[SemanticAtom, ...],
        byte_budget: int,
        kernel_schema: KernelSchema,
        loss_policy: LossPolicy,
        weight_version: str,
    ) -> CapsuleState:
        return recompose_capsule(
            RecompositionRequest(
                base_state=base_state,
                delta=delta,
                byte_budget=byte_budget,
                kernel_schema=kernel_schema,
                loss_policy=loss_policy,
                weight_version=weight_version,
            )
        ).state

    def project(
        self,
        state: CapsuleState,
        query_id: str,
        byte_budget: int,
    ) -> ProjectionResult:
        selected: list[SemanticAtom] = []
        blocks: list[str] = []
        used = 0
        for atom in state.kernel + state.body:
            block = canonical_json(atom)
            cost = utf8_bytes(block) + (1 if blocks else 0)
            if used + cost > byte_budget:
                break
            selected.append(atom)
            blocks.append(block)
            used += cost
        if not selected:
            return ProjectionResult(
                query_id=query_id,
                text=UNKNOWN,
                selected_atom_ids=(),
                selected_covered_ids=(),
                byte_cost=utf8_bytes(UNKNOWN),
                supported=False,
            )
        covered = tuple(
            sorted(
                {
                    item
                    for atom in selected
                    for item in (atom.covered_atom_ids or (atom.atom_id,))
                }
            )
        )
        return ProjectionResult(
            query_id=query_id,
            text="\n".join(blocks),
            selected_atom_ids=tuple(atom.atom_id for atom in selected),
            selected_covered_ids=covered,
            byte_cost=used,
            supported=True,
        )


@dataclass(frozen=True, slots=True)
class _SummaryRecord:
    role: str
    semantic_key: str
    atom_id: str
    revision: int
    text: str

    def render(self) -> str:
        safe_text = self.text.replace("\n", " ").replace("|", "/")
        return (
            f"{self.role}|{self.semantic_key}|{self.atom_id}|r{self.revision}|"
            f"{safe_text}"
        )


def _parse_summary(previous_text: str) -> list[_SummaryRecord]:
    records: list[_SummaryRecord] = []
    for line in previous_text.splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5 or not parts[3].startswith("r"):
            continue
        try:
            revision = int(parts[3][1:])
        except ValueError:
            continue
        records.append(_SummaryRecord(parts[0], parts[1], parts[2], revision, parts[4]))
    return records


class RecursiveSummaryBaseline:
    """Newline summary that sees only its previous text and current delta."""

    def advance(
        self,
        previous_text: str,
        delta: tuple[SemanticAtom, ...],
        byte_budget: int,
    ) -> str:
        records = _parse_summary(previous_text)
        for atom in sorted(
            delta, key=lambda item: (item.as_of, item.revision, item.atom_id)
        ):
            records = [
                record
                for record in records
                if set((record.semantic_key,)).isdisjoint(atom.semantic_keys)
            ]
            records.extend(
                _SummaryRecord(
                    role=atom.role.value,
                    semantic_key=semantic_key,
                    atom_id=atom.atom_id,
                    revision=atom.revision,
                    text=atom.text,
                )
                for semantic_key in atom.semantic_keys
            )
        records.sort(
            key=lambda record: (
                record.role,
                record.semantic_key,
                -record.revision,
                record.atom_id,
            )
        )
        selected: list[str] = []
        used = 0
        for record in records:
            line = record.render()
            cost = utf8_bytes(line) + (1 if selected else 0)
            if used + cost > byte_budget:
                continue
            selected.append(line)
            used += cost
        return "\n".join(selected)

    def project(
        self,
        summary_text: str,
        query: QuerySpec,
        byte_budget: int,
    ) -> ProjectionResult:
        records = sorted(
            (
                record
                for record in _parse_summary(summary_text)
                if record.role == query.role.value
                and record.semantic_key == query.semantic_key
            ),
            key=lambda record: (-record.revision, record.atom_id),
        )
        selected: list[_SummaryRecord] = []
        lines: list[str] = []
        used = 0
        for record in records:
            line = record.render()
            cost = utf8_bytes(line) + (1 if lines else 0)
            if used + cost > byte_budget:
                continue
            selected.append(record)
            lines.append(line)
            used += cost
        if not selected:
            return ProjectionResult(
                query_id=query.query_id,
                text=UNKNOWN,
                selected_atom_ids=(),
                selected_covered_ids=(),
                byte_cost=utf8_bytes(UNKNOWN),
                supported=False,
            )
        selected_ids = tuple(record.atom_id for record in selected)
        return ProjectionResult(
            query_id=query.query_id,
            text="\n".join(lines),
            selected_atom_ids=selected_ids,
            selected_covered_ids=selected_ids,
            byte_cost=used,
            supported=True,
        )


class NoKernelAblation:
    """Offline copy that clears core flags and removes the continuity kernel."""

    def advance(
        self,
        base_state: CapsuleState | None,
        delta: tuple[SemanticAtom, ...],
        byte_budget: int,
        kernel_schema: KernelSchema,
        loss_policy: LossPolicy,
        weight_version: str,
    ) -> CapsuleState:
        del kernel_schema
        cleared_delta = tuple(replace(atom, core_required=False) for atom in delta)
        cleared_base = None
        if base_state is not None:
            cleared_base = replace(
                base_state,
                kernel=tuple(
                    replace(atom, core_required=False) for atom in base_state.kernel
                ),
                body=tuple(
                    replace(atom, core_required=False) for atom in base_state.body
                ),
            )
        empty_schema = KernelSchema(slots=(), metadata_reserve_bytes=0)
        return recompose_capsule(
            RecompositionRequest(
                base_state=cleared_base,
                delta=cleared_delta,
                byte_budget=byte_budget,
                kernel_schema=empty_schema,
                loss_policy=loss_policy,
                weight_version=weight_version,
            )
        ).state
