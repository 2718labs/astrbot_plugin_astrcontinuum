from __future__ import annotations

from dataclasses import dataclass

import pytest

from astrcontinuum.runtime import (
    ProjectionErrorCode,
    ProjectionInvariantError,
    ProjectionStage,
    guard_projection,
    project,
    restore,
    verify_native,
)


class EqualBox:
    def __init__(self, label: str) -> None:
        self.label = label

    def __eq__(self, other: object) -> bool:
        return isinstance(other, EqualBox) and self.label == other.label

    def __repr__(self) -> str:
        return f"EqualBox({self.label!r})"


@dataclass
class FakeMessage:
    role: str
    content: list[object]


def _assert_identity_sequence(
    actual: list[object] | tuple[object, ...],
    expected: list[object] | tuple[object, ...],
) -> None:
    assert len(actual) == len(expected)
    assert all(left is right for left, right in zip(actual, expected, strict=True))


def _native_turn() -> tuple[
    object,
    list[object],
    EqualBox,
    EqualBox,
    EqualBox,
    EqualBox,
]:
    request = object()
    system = EqualBox("system")
    history_a = EqualBox("history-a")
    history_b = EqualBox("history-b")
    current = EqualBox("current")
    messages: list[object] = [system, history_a, history_b, current]
    return request, messages, system, history_a, history_b, current


def test_guard_and_project_preserve_only_identity_selected_objects() -> None:
    request, messages, system, history_a, history_b, current = _native_turn()
    guard = guard_projection(
        request,
        messages,
        system_objects=(system,),
        current_objects=(current,),
    )
    opaque_equal_to_history = EqualBox("history-a")
    messages.insert(2, opaque_equal_to_history)
    nested_projection_part = EqualBox("projection-part")
    projection = FakeMessage("user", [nested_projection_part])

    projected = project(messages, guard, (projection,))

    assert guard.request_identity == id(request)
    assert guard.message_list_identity == id(messages)
    _assert_identity_sequence(
        guard.native_objects,
        (system, history_a, history_b, current),
    )
    _assert_identity_sequence(guard.native_history_objects, (history_a, history_b))
    _assert_identity_sequence(
        projected.pre_projection_objects,
        (system, history_a, opaque_equal_to_history, history_b, current),
    )
    _assert_identity_sequence(
        messages,
        (system, opaque_equal_to_history, projection, current),
    )
    assert projection.content[0] is nested_projection_part


def test_restore_is_exact_preprojection_plus_delta_then_native_verifies() -> None:
    request, messages, system, history_a, history_b, current = _native_turn()
    guard = guard_projection(
        request,
        messages,
        system_objects=(system,),
        current_objects=(current,),
    )
    opaque = EqualBox("foreign-projection")
    messages.insert(1, opaque)
    projection = EqualBox("ac-projection")
    projected = project(messages, guard, (projection,))
    assistant_delta = EqualBox("assistant-delta")
    tool_delta = EqualBox("tool-delta")
    messages.extend((assistant_delta, tool_delta))

    restored = restore(messages, projected)

    _assert_identity_sequence(
        messages,
        (
            system,
            opaque,
            history_a,
            history_b,
            current,
            assistant_delta,
            tool_delta,
        ),
    )
    _assert_identity_sequence(restored.delta_objects, (assistant_delta, tool_delta))

    messages[:] = [
        system,
        history_a,
        history_b,
        current,
        assistant_delta,
        tool_delta,
    ]
    before_verify = tuple(messages)
    assert verify_native(messages, restored) is True
    _assert_identity_sequence(messages, before_verify)

    messages.insert(1, EqualBox("unexpected"))
    before_failed_verify = tuple(messages)
    assert verify_native(messages, restored) is False
    _assert_identity_sequence(messages, before_failed_verify)


def test_projection_is_deterministic_for_the_same_identity_graph() -> None:
    request, messages, system, history_a, history_b, current = _native_turn()
    guard = guard_projection(
        request,
        messages,
        system_objects=(system,),
        current_objects=(current,),
    )
    opaque = EqualBox("opaque")
    messages.insert(2, opaque)
    projection = EqualBox("projection")

    first = project(messages, guard, (projection,))
    restore(messages, first)
    second = project(messages, guard, (projection,))

    _assert_identity_sequence(first.pre_projection_objects, second.pre_projection_objects)
    _assert_identity_sequence(first.projected_objects, second.projected_objects)
    _assert_identity_sequence(
        messages,
        (system, opaque, projection, current),
    )
    _assert_identity_sequence(guard.native_history_objects, (history_a, history_b))


def test_project_list_identity_mismatch_does_not_mutate_either_list() -> None:
    request, messages, system, _history_a, _history_b, current = _native_turn()
    guard = guard_projection(
        request,
        messages,
        system_objects=(system,),
        current_objects=(current,),
    )
    different_list = list(messages)
    original_messages = tuple(messages)
    original_different = tuple(different_list)

    with pytest.raises(ProjectionInvariantError) as caught:
        project(different_list, guard, (EqualBox("projection"),))

    assert caught.value.code == ProjectionErrorCode.LIST_IDENTITY_MISMATCH.value
    assert caught.value.stage == ProjectionStage.PROJECT.value
    assert caught.value.message_list_identity_match is False
    _assert_identity_sequence(messages, original_messages)
    _assert_identity_sequence(different_list, original_different)


def test_project_preserved_identity_mismatch_is_content_free_and_no_mutation() -> None:
    request, messages, system, _history_a, _history_b, current = _native_turn()
    guard = guard_projection(
        request,
        messages,
        system_objects=(system,),
        current_objects=(current,),
    )
    secret = "DO-NOT-LEAK-current-message-text"
    current.label = secret
    messages.remove(current)
    before = tuple(messages)

    with pytest.raises(ProjectionInvariantError) as caught:
        project(messages, guard, (EqualBox("projection"),))

    error = caught.value
    assert error.code == ProjectionErrorCode.PRESERVED_OBJECT_MISMATCH.value
    assert error.stage == ProjectionStage.PROJECT.value
    assert error.expected_count == 2
    assert error.actual_count == 1
    assert error.message_list_identity_match is True
    assert secret not in str(error)
    assert secret not in repr(error)
    _assert_identity_sequence(messages, before)


def test_restore_prefix_mismatch_never_guesses_or_mutates() -> None:
    request, messages, system, _history_a, _history_b, current = _native_turn()
    guard = guard_projection(
        request,
        messages,
        system_objects=(system,),
        current_objects=(current,),
    )
    projected = project(messages, guard, (EqualBox("projection"),))
    messages[0] = EqualBox("replacement-system")
    messages.append(EqualBox("assistant-delta"))
    before = tuple(messages)

    with pytest.raises(ProjectionInvariantError) as caught:
        restore(messages, projected)

    assert caught.value.code == ProjectionErrorCode.PROJECTED_PREFIX_MISMATCH.value
    assert caught.value.stage == ProjectionStage.RESTORE.value
    _assert_identity_sequence(messages, before)


def test_guard_rejects_overlapping_preserved_roles_without_content() -> None:
    request, messages, system, _history_a, _history_b, _current = _native_turn()
    before = tuple(messages)

    with pytest.raises(ProjectionInvariantError) as caught:
        guard_projection(
            request,
            messages,
            system_objects=(system,),
            current_objects=(system,),
        )

    assert caught.value.code == ProjectionErrorCode.PRESERVED_OBJECT_INVALID.value
    assert caught.value.stage == ProjectionStage.GUARD.value
    _assert_identity_sequence(messages, before)
