from __future__ import annotations

from typing import NoReturn

from .types import (
    ProjectedView,
    ProjectionErrorCode,
    ProjectionGuard,
    ProjectionStage,
    RestoredView,
)


class ProjectionInvariantError(ValueError):
    """Report one reversible-projection failure without opaque content."""

    def __init__(
        self,
        code: ProjectionErrorCode,
        stage: ProjectionStage,
        *,
        expected_count: int,
        actual_count: int,
        message_list_identity_match: bool,
    ) -> None:
        self.code = code.value
        self.stage = stage.value
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.message_list_identity_match = message_list_identity_match
        super().__init__(self.code)


def _invalid(
    code: ProjectionErrorCode,
    stage: ProjectionStage,
    *,
    expected_count: int,
    actual_count: int,
    message_list_identity_match: bool,
) -> NoReturn:
    raise ProjectionInvariantError(
        code,
        stage,
        expected_count=expected_count,
        actual_count=actual_count,
        message_list_identity_match=message_list_identity_match,
    )


def _same_identity_sequence(
    left: tuple[object, ...] | list[object],
    right: tuple[object, ...] | list[object],
) -> bool:
    return len(left) == len(right) and all(
        first is second for first, second in zip(left, right, strict=True)
    )


def _identity_occurrences(messages: list[object], targets: tuple[object, ...]) -> int:
    return sum(message is target for target in targets for message in messages)


def _identity_is_unique(objects: tuple[object, ...]) -> bool:
    return len({id(item) for item in objects}) == len(objects)


def _contains_identity(objects: tuple[object, ...], target: object) -> bool:
    return any(item is target for item in objects)


def _validate_message_list(
    messages: object,
    *,
    stage: ProjectionStage,
    expected_identity: int | None,
) -> list[object]:
    if not isinstance(messages, list):
        _invalid(
            ProjectionErrorCode.MESSAGE_LIST_INVALID,
            stage,
            expected_count=0,
            actual_count=0,
            message_list_identity_match=False,
        )
    if expected_identity is not None and id(messages) != expected_identity:
        _invalid(
            ProjectionErrorCode.LIST_IDENTITY_MISMATCH,
            stage,
            expected_count=0,
            actual_count=len(messages),
            message_list_identity_match=False,
        )
    return messages


def _validate_preserved(
    messages: list[object],
    guard: ProjectionGuard,
    *,
    stage: ProjectionStage,
) -> None:
    preserved = (*guard.system_objects, *guard.current_objects)
    actual_count = _identity_occurrences(messages, preserved)
    if actual_count != len(preserved):
        _invalid(
            ProjectionErrorCode.PRESERVED_OBJECT_MISMATCH,
            stage,
            expected_count=len(preserved),
            actual_count=actual_count,
            message_list_identity_match=True,
        )

    for group in (guard.system_objects, guard.current_objects):
        positions = tuple(
            index for target in group for index, message in enumerate(messages) if message is target
        )
        if positions != tuple(sorted(positions)):
            _invalid(
                ProjectionErrorCode.PRESERVED_OBJECT_MISMATCH,
                stage,
                expected_count=len(group),
                actual_count=len(positions),
                message_list_identity_match=True,
            )


def guard_projection(
    request: object,
    messages: list[object],
    *,
    system_objects: tuple[object, ...],
    current_objects: tuple[object, ...],
) -> ProjectionGuard:
    """Capture the exact native request/list/object identity graph."""

    checked_messages = _validate_message_list(
        messages,
        stage=ProjectionStage.GUARD,
        expected_identity=None,
    )
    preserved = (*system_objects, *current_objects)
    if (
        not isinstance(system_objects, tuple)
        or not isinstance(current_objects, tuple)
        or not current_objects
        or not _identity_is_unique(preserved)
    ):
        _invalid(
            ProjectionErrorCode.PRESERVED_OBJECT_INVALID,
            ProjectionStage.GUARD,
            expected_count=len(preserved),
            actual_count=_identity_occurrences(checked_messages, preserved),
            message_list_identity_match=True,
        )
    actual_count = _identity_occurrences(checked_messages, preserved)
    if actual_count != len(preserved):
        _invalid(
            ProjectionErrorCode.PRESERVED_OBJECT_MISMATCH,
            ProjectionStage.GUARD,
            expected_count=len(preserved),
            actual_count=actual_count,
            message_list_identity_match=True,
        )
    native_objects = tuple(checked_messages)
    native_history_objects = tuple(
        item for item in native_objects if not _contains_identity(preserved, item)
    )
    return ProjectionGuard(
        request_identity=id(request),
        message_list_identity=id(checked_messages),
        native_objects=native_objects,
        native_history_objects=native_history_objects,
        system_objects=system_objects,
        current_objects=current_objects,
    )


def project(
    messages: list[object],
    guard: ProjectionGuard,
    projection_objects: tuple[object, ...],
) -> ProjectedView:
    """Replace only guarded native history while retaining every opaque object."""

    checked_messages = _validate_message_list(
        messages,
        stage=ProjectionStage.PROJECT,
        expected_identity=guard.message_list_identity,
    )
    _validate_preserved(checked_messages, guard, stage=ProjectionStage.PROJECT)
    if (
        not isinstance(projection_objects, tuple)
        or not _identity_is_unique(projection_objects)
        or any(
            message is projection
            for projection in projection_objects
            for message in checked_messages
        )
    ):
        _invalid(
            ProjectionErrorCode.PROJECTION_OBJECT_INVALID,
            ProjectionStage.PROJECT,
            expected_count=max(1, len(projection_objects)),
            actual_count=len(projection_objects),
            message_list_identity_match=True,
        )

    pre_projection_objects = tuple(checked_messages)
    retained = [
        message
        for message in checked_messages
        if not _contains_identity(guard.native_history_objects, message)
    ]
    current_start = next(
        index for index, message in enumerate(retained) if message is guard.current_objects[0]
    )
    projected_objects = (
        *retained[:current_start],
        *projection_objects,
        *retained[current_start:],
    )
    checked_messages[:] = projected_objects
    return ProjectedView(
        guard=guard,
        request_identity=guard.request_identity,
        message_list_identity=guard.message_list_identity,
        pre_projection_objects=pre_projection_objects,
        projection_objects=projection_objects,
        projected_objects=projected_objects,
    )


def restore(messages: list[object], projected: ProjectedView) -> RestoredView:
    """Restore the exact pre-AC prefix and preserve the appended Delta."""

    guard = projected.guard
    checked_messages = _validate_message_list(
        messages,
        stage=ProjectionStage.RESTORE,
        expected_identity=None,
    )
    if projected.request_identity != guard.request_identity:
        _invalid(
            ProjectionErrorCode.REQUEST_IDENTITY_MISMATCH,
            ProjectionStage.RESTORE,
            expected_count=1,
            actual_count=0,
            message_list_identity_match=True,
        )
    current_positions = tuple(
        index
        for target in guard.current_objects
        for index, message in enumerate(checked_messages)
        if message is target
    )
    if len(current_positions) != len(guard.current_objects) or current_positions != tuple(
        sorted(current_positions)
    ):
        _invalid(
            ProjectionErrorCode.PROJECTED_PREFIX_MISMATCH,
            ProjectionStage.RESTORE,
            expected_count=len(guard.current_objects),
            actual_count=len(current_positions),
            message_list_identity_match=(id(checked_messages) == projected.message_list_identity),
        )
    delta_objects = tuple(checked_messages[current_positions[-1] + 1 :])
    if any(
        message is projection
        for projection in projected.projection_objects
        for message in delta_objects
    ):
        _invalid(
            ProjectionErrorCode.PROJECTED_PREFIX_MISMATCH,
            ProjectionStage.RESTORE,
            expected_count=0,
            actual_count=1,
            message_list_identity_match=(id(checked_messages) == projected.message_list_identity),
        )
    restored_objects = (*projected.pre_projection_objects, *delta_objects)
    checked_messages[:] = restored_objects
    return RestoredView(
        projected=projected,
        delta_objects=delta_objects,
        restored_objects=restored_objects,
    )


def verify_native(messages: list[object], restored: RestoredView) -> bool:
    """Verify native history plus the exact captured Delta without mutation."""

    if not isinstance(messages, list):
        return False
    guard = restored.projected.guard
    expected = (*guard.native_objects, *restored.delta_objects)
    return _same_identity_sequence(messages, expected)
