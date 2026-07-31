from crm_experiment.contracts import AtomRole, KernelSchema, KernelSlot
from crm_experiment.kernel import default_kernel_schema, derive_kernel_ceiling


def test_default_kernel_ceiling_is_frozen() -> None:
    schema = default_kernel_schema()

    assert derive_kernel_ceiling(schema) == 4608


def test_custom_kernel_ceiling_uses_utf8_escape_reserve() -> None:
    schema = KernelSchema(
        slots=(KernelSlot(AtomRole.ROOT_GOAL, 1, 100),),
        metadata_reserve_bytes=200,
    )

    assert derive_kernel_ceiling(schema) == 400


def test_kernel_schema_has_only_continuity_roles() -> None:
    schema = default_kernel_schema()

    assert tuple(slot.role for slot in schema.slots) == (
        AtomRole.ROOT_GOAL,
        AtomRole.CURRENT_FOCUS,
        AtomRole.OPEN_LOOP,
        AtomRole.HARD_CONSTRAINT,
        AtomRole.DECISION,
        AtomRole.EXACT_ANCHOR,
    )
