from __future__ import annotations

import hashlib
from dataclasses import replace
from enum import Enum

from ..runtime.budget import BudgetInvariantError, assemble
from ..runtime.pressure import PressureConfig, assess_pressure
from ..runtime.types import BudgetConfig, BudgetErrorCode, TokenCounter
from .types import (
    BYTE_FALLBACK,
    RequestBudgetOutcome,
    RequestBudgetWorkload,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerProfile,
)

_TOKENIZER_BYTE_FALLBACK = "TOKENIZER_BYTE_FALLBACK"


class RequestBudgetErrorCode(str, Enum):
    """Stable request-orchestration failures that must not trigger fallback."""

    DUPLICATE_BLOCK_ID = "DUPLICATE_BLOCK_ID"
    BLOCK_ID_TEXT_CONFLICT = "BLOCK_ID_TEXT_CONFLICT"
    BLOCK_ID_INVALID = "BLOCK_ID_INVALID"
    BLOCK_TEXT_INVALID = "BLOCK_TEXT_INVALID"
    WORKLOAD_INVALID = "WORKLOAD_INVALID"


class RequestBudgetInvariantError(ValueError):
    """Report one content-free request-workload invariant failure."""

    __slots__ = ("code",)

    def __init__(self, code: RequestBudgetErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


class RequestScopedTokenCounter:
    """Cache content-free token counts for exactly one request and profile."""

    __slots__ = (
        "_block_fingerprints",
        "_cache",
        "_counter",
        "_profile",
        "_request_cache",
        "_request_fingerprints",
        "_text_cache",
    )

    def __init__(
        self,
        counter: TokenCounter,
        *,
        profile: TokenizerProfile | None = None,
    ) -> None:
        counter_profile = getattr(counter, "profile", None)
        if counter_profile is not None and not isinstance(counter_profile, TokenizerProfile):
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
        if counter_profile is not None and profile is not None and counter_profile != profile:
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
        resolved_profile = counter_profile if counter_profile is not None else profile
        if not isinstance(resolved_profile, TokenizerProfile):
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
        self._counter = counter
        self._profile = resolved_profile
        self._cache: dict[tuple[str, str], int] = {}
        self._block_fingerprints: dict[tuple[str, str], str] = {}
        self._request_cache: dict[tuple[str, str], int] = {}
        self._request_fingerprints: dict[tuple[str, str], str] = {}
        self._text_cache: dict[tuple[str, str], int] = {}

    @property
    def profile(self) -> TokenizerProfile:
        return self._profile

    def __repr__(self) -> str:
        return f"RequestScopedTokenCounter(profile_id={self._profile.profile_id!r})"

    def count_block(self, block_id: str, text: str) -> int:
        if not isinstance(block_id, str) or not block_id:
            raise RequestBudgetInvariantError(RequestBudgetErrorCode.BLOCK_ID_INVALID)
        if not isinstance(text, str):
            raise RequestBudgetInvariantError(RequestBudgetErrorCode.BLOCK_TEXT_INVALID)
        key = (block_id, self._profile.profile_id)
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = self._block_fingerprints.get(key)
        if previous is not None and previous != fingerprint:
            raise RequestBudgetInvariantError(RequestBudgetErrorCode.BLOCK_ID_TEXT_CONFLICT)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        value = self._count_uncached(text)
        self._block_fingerprints[key] = fingerprint
        self._cache[key] = value
        return value

    def count_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise RequestBudgetInvariantError(RequestBudgetErrorCode.BLOCK_TEXT_INVALID)
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        key = (fingerprint, self._profile.profile_id)
        cached = self._text_cache.get(key)
        if cached is not None:
            return cached
        value = self._count_uncached(text)
        self._text_cache[key] = value
        return value

    def _count_request_part(self, part_id: str, text: str) -> int:
        if not isinstance(text, str):
            raise RequestBudgetInvariantError(RequestBudgetErrorCode.BLOCK_TEXT_INVALID)
        key = (part_id, self._profile.profile_id)
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = self._request_fingerprints.get(key)
        if previous is not None and previous != fingerprint:
            raise RequestBudgetInvariantError(RequestBudgetErrorCode.BLOCK_ID_TEXT_CONFLICT)
        cached = self._request_cache.get(key)
        if cached is not None:
            return cached
        value = self._count_uncached(text)
        self._request_fingerprints[key] = fingerprint
        self._request_cache[key] = value
        return value

    def _count_uncached(self, text: str) -> int:
        value: object | None = None
        try:
            value = self._counter.count_text(text)
        except TokenizerError:
            raise
        except Exception:  # noqa: BLE001 - translate through the stable boundary
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_FAILED) from None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_INVALID)
        return value


def _sum_texts(
    counter: RequestScopedTokenCounter,
    *,
    namespace: str,
    texts: tuple[str, ...],
) -> int:
    return sum(
        counter._count_request_part(f"{namespace}:{index}", text)
        for index, text in enumerate(texts)
    )


def _validate_workload(workload: RequestBudgetWorkload) -> None:
    if not isinstance(workload, RequestBudgetWorkload):
        raise RequestBudgetInvariantError(RequestBudgetErrorCode.WORKLOAD_INVALID)
    block_ids = tuple(candidate.block_id for candidate in workload.prepared.candidates)
    if len(block_ids) != len(set(block_ids)):
        raise RequestBudgetInvariantError(RequestBudgetErrorCode.DUPLICATE_BLOCK_ID)


def _run_once(
    workload: RequestBudgetWorkload,
    counter: RequestScopedTokenCounter,
) -> RequestBudgetOutcome:
    prepared = workload.prepared
    host = workload.host
    profile = workload.profile

    host_cost = _sum_texts(
        counter,
        namespace="host",
        texts=host.all_host_texts,
    )
    opaque_cost = _sum_texts(
        counter,
        namespace="opaque",
        texts=host.opaque_texts,
    )
    fixed_cost = _sum_texts(
        counter,
        namespace="fixed",
        texts=host.fixed_required_texts,
    )
    current_cost = counter._count_request_part(
        "current-input",
        prepared.current_input,
    )
    pressure = assess_pressure(
        trusted_token_usage=prepared.trusted_token_usage,
        estimated_input_usage=host_cost + current_cost,
        current_input_cost=current_cost,
        config=PressureConfig(
            context_limit=profile.context_limit,
            reserved_output_and_tools=profile.reserved_output_and_tools,
            compact_ratio=profile.compaction_start_ratio,
            project_ratio=profile.provider_view_switch_ratio,
        ),
    )

    assembly = None
    if pressure.should_project:
        block_token_counts = {
            candidate.block_id: counter.count_block(
                candidate.block_id,
                candidate.text,
            )
            for candidate in prepared.candidates
        }
        try:
            assembly = assemble(
                prepared.view,
                prepared.candidates,
                current_input=prepared.current_input,
                opaque_token_cost=opaque_cost,
                fixed_required_cost=fixed_cost,
                counter=counter,
                config=BudgetConfig(
                    target_input_budget=profile.target_input_budget,
                    hard_input_ceiling=profile.hard_input_ceiling,
                    model_context_limit=profile.context_limit,
                    reserved_output_and_tools=profile.reserved_output_and_tools,
                    safety_margin=profile.safety_margin,
                ),
                block_token_counts=block_token_counts,
            )
        except BudgetInvariantError as error:
            if error.code != BudgetErrorCode.REQUIRED_INPUT_EXCEEDS_BUDGET.value:
                raise
            return RequestBudgetOutcome(
                pressure=pressure,
                assembly=None,
                tokenizer_profile_id=counter.profile.profile_id,
                tokenizer_mode=counter.profile.mode.value,
                fallback_code="NONE",
                stable_code=BudgetErrorCode.REQUIRED_INPUT_EXCEEDS_BUDGET.value,
                mutation_allowed=False,
                primary_result_discarded=False,
            )
        required_ids = {
            candidate.block_id for candidate in prepared.candidates if candidate.required
        }
        selected_ids = {candidate.block_id for candidate in assembly.selected_blocks}
        if not required_ids.issubset(selected_ids):
            return RequestBudgetOutcome(
                pressure=pressure,
                assembly=None,
                tokenizer_profile_id=counter.profile.profile_id,
                tokenizer_mode=counter.profile.mode.value,
                fallback_code="NONE",
                stable_code=BudgetErrorCode.REQUIRED_INPUT_EXCEEDS_BUDGET.value,
                mutation_allowed=False,
                primary_result_discarded=False,
            )

    return RequestBudgetOutcome(
        pressure=pressure,
        assembly=assembly,
        tokenizer_profile_id=counter.profile.profile_id,
        tokenizer_mode=counter.profile.mode.value,
        fallback_code="NONE",
        stable_code=profile.stable_code,
        mutation_allowed=assembly is not None,
        primary_result_discarded=False,
    )


def _is_tokenizer_count_failure(error: BudgetInvariantError) -> bool:
    return error.code == BudgetErrorCode.TOKEN_COUNTER_FAILURE.value


def run_request_budget(
    workload: RequestBudgetWorkload,
    *,
    primary: TokenCounter,
    fallback: TokenCounter,
) -> RequestBudgetOutcome:
    """Run a request atomically, replaying only tokenizer failures from source."""

    _validate_workload(workload)
    try:
        primary_outcome = _run_once(
            workload,
            RequestScopedTokenCounter(
                primary,
                profile=workload.profile.tokenizer_profile,
            ),
        )
    except TokenizerError:
        pass
    except BudgetInvariantError as error:
        if not _is_tokenizer_count_failure(error):
            raise
    else:
        return primary_outcome

    fallback_outcome = _run_once(
        workload,
        RequestScopedTokenCounter(fallback, profile=BYTE_FALLBACK),
    )
    return replace(
        fallback_outcome,
        fallback_code=_TOKENIZER_BYTE_FALLBACK,
        primary_result_discarded=True,
    )
