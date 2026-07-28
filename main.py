"""AstrContinuum AstrBot plugin composition root."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar, cast

from astrbot.api import logger  # type: ignore[import-not-found]
from astrbot.api.event import AstrMessageEvent, filter  # type: ignore[import-not-found]
from astrbot.api.star import Context, Star, StarTools, register  # type: ignore[import-not-found]

if TYPE_CHECKING or not __package__:
    from astrcontinuum.adapters import (
        AdapterFault,
        AstrBotAdapterError,
        AstrBotHookBridge,
        PreparedRequest,
        build_projection_objects,
        extract_host_session_key,
        projection_factory_from_user_message,
        select_projection_boundaries,
    )
    from astrcontinuum.compaction import (
        AstrBotExtractiveCompilerBackend,
        CompactionProviderBinding,
        CompactionWorker,
        CompactionWorkerConfig,
        SessionProviderRegistry,
    )
    from astrcontinuum.context_graph import ContextEngineMode, LiveContextTrace
    from astrcontinuum.domain import EventEnvelope, SessionKey
    from astrcontinuum.runtime import (
        BudgetConfig,
        BudgetInvariantError,
        PressureDecision,
        ProjectedView,
        ProjectionGuard,
        ProjectionInvariantError,
        RestoredView,
        Utf8ByteTokenCounter,
        guard_projection,
        project,
        restore,
        verify_native,
    )
    from astrcontinuum.runtime.types import BudgetErrorCode
    from astrcontinuum.storage import (
        KeySource,
        SecurityErrorCode,
        SQLiteConnectionFactory,
        SQLiteRepository,
        StorageMaintenanceState,
        StorageSecurityError,
        activate_storage_security,
        resolve_key_material,
    )
    from astrcontinuum.storage.security import resolve_key_source
    from astrcontinuum.tokenization import (
        CANONICAL_O200K,
        OPENAI_O200K,
        REFERENCE_O200K,
        ContextLimitDecision,
        ContextLimitResolver,
        HostBudgetView,
        RequestBudgetOutcome,
        RequestBudgetProfile,
        TokenizerError,
        TokenizerErrorCode,
        TokenizerRegistry,
        TokenizerRoute,
        TokenizerRouter,
        normalize_model_identity,
        resolve_astrbot_request_metadata,
    )
else:
    from .astrcontinuum.adapters import (
        AdapterFault,
        AstrBotAdapterError,
        AstrBotHookBridge,
        PreparedRequest,
        build_projection_objects,
        extract_host_session_key,
        projection_factory_from_user_message,
        select_projection_boundaries,
    )
    from .astrcontinuum.compaction import (
        AstrBotExtractiveCompilerBackend,
        CompactionProviderBinding,
        CompactionWorker,
        CompactionWorkerConfig,
        SessionProviderRegistry,
    )
    from .astrcontinuum.context_graph import ContextEngineMode, LiveContextTrace
    from .astrcontinuum.domain import EventEnvelope, SessionKey
    from .astrcontinuum.runtime import (
        BudgetConfig,
        BudgetInvariantError,
        PressureDecision,
        ProjectedView,
        ProjectionGuard,
        ProjectionInvariantError,
        RestoredView,
        Utf8ByteTokenCounter,
        guard_projection,
        project,
        restore,
        verify_native,
    )
    from .astrcontinuum.runtime.types import BudgetErrorCode  # noqa: F401
    from .astrcontinuum.storage import (
        KeySource,
        SecurityErrorCode,
        SQLiteConnectionFactory,
        SQLiteRepository,
        StorageMaintenanceState,
        StorageSecurityError,
        activate_storage_security,
        resolve_key_material,
    )
    from .astrcontinuum.storage.security import resolve_key_source
    from .astrcontinuum.tokenization import (
        CANONICAL_O200K,
        OPENAI_O200K,  # noqa: F401
        REFERENCE_O200K,  # noqa: F401
        ContextLimitDecision,
        ContextLimitResolver,
        HostBudgetView,
        RequestBudgetOutcome,
        RequestBudgetProfile,
        TokenizerError,
        TokenizerErrorCode,  # noqa: F401
        TokenizerRegistry,
        TokenizerRoute,
        TokenizerRouter,
        normalize_model_identity,
        resolve_astrbot_request_metadata,
    )

_PLUGIN_NAME = "astrbot_plugin_astrcontinuum"
_REQUEST_STATE_KEY = "astrcontinuum.v1.request-state"
_ENCRYPTION_FORMAT = "AES-256-GCM / envelope-v1"
_TRACE_INTEGER_LIMIT = 1_000_000
_EVENT_TYPES = (
    "USER_MESSAGE",
    "ASSISTANT_MESSAGE",
    "TOOL_CALL",
    "TOOL_RESULT",
)
_T = TypeVar("_T")


@dataclass(slots=True, repr=False)
class _RequestState:
    request: Any = field(repr=False)
    prepared: PreparedRequest | None = field(default=None, repr=False)
    guard: ProjectionGuard | None = field(default=None, repr=False)
    projected: ProjectedView | None = field(default=None, repr=False)
    restored: RestoredView | None = field(default=None, repr=False)
    assistant_event: EventEnvelope | None = field(default=None, repr=False)
    pressure: PressureDecision | None = None
    outcome: RequestBudgetOutcome | None = field(default=None, repr=False)
    original_conversation: object | None = field(default=None, repr=False)
    intent_raised: bool = False
    next_tool_ordinal: int = 0
    pending_tool_ordinals: list[int] = field(default_factory=list, repr=False)
    faults: list[AdapterFault] = field(default_factory=list, repr=False)


@dataclass(frozen=True, slots=True)
class _StorageRuntimeStatus:
    protection: str
    key_source: str
    key_id: str
    maintenance: str
    security_code: str


@dataclass(frozen=True, slots=True)
class _SessionInspection:
    snapshot_suffix: str
    pointer_version: int
    covered_range: str
    high_water_mark: int
    delta_range: str
    event_counts: tuple[tuple[str, int], ...]
    capsule_slots: tuple[tuple[str, int], ...]
    pending_job_state: str
    retry_code: str


@dataclass(frozen=True, slots=True)
class _ContextEngineInspection:
    mode: str
    state: str
    stable_code: str
    candidate_count: str
    selected_count: str
    relation_count: str
    constraint_count: str
    retained_count: str
    reduced_count: str
    selected_budget_units: str
    reduction_ratio: str
    residual_band: str
    recovery_count: str
    required_coverage: str
    provenance_coverage: str


async def _complete_thread_call(
    function: Callable[..., _T],
    /,
    *args: object,
) -> _T:
    """Wait for startup-only blocking work even if the lifecycle task is cancelled."""

    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def _generic_fault(code: str, stage: str, *, capability_available: bool = False) -> AdapterFault:
    return AdapterFault(
        code=code,
        stage=stage,
        missing_field_count=0,
        capability_available=capability_available,
    )


def _projection_fault(error: ProjectionInvariantError) -> AdapterFault:
    return AdapterFault(
        code=error.code,
        stage=error.stage,
        missing_field_count=max(0, error.expected_count - error.actual_count),
        capability_available=True,
    )


def _contains_identity(objects: tuple[object, ...], target: object) -> bool:
    return any(item is target for item in objects)


_HOST_ATTRIBUTE_MISSING = object()


def _host_attribute(target: object, name: str) -> object:
    try:
        if isinstance(target, Mapping):
            return target.get(name, _HOST_ATTRIBUTE_MISSING)
        return getattr(target, name, _HOST_ATTRIBUTE_MISSING)
    except Exception:  # noqa: BLE001 - hostile Hook properties stay redacted
        raise TypeError("host property is unavailable") from None


def _host_texts(message: object) -> tuple[str, ...]:
    content = _host_attribute(message, "content")
    if content is _HOST_ATTRIBUTE_MISSING:
        raise TypeError("host content is unavailable")
    if isinstance(content, str):
        return (content,)
    if not isinstance(content, (list, tuple)):
        return ()
    texts: list[str] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        for name in ("text", "think", "image_url", "audio_url"):
            value = _host_attribute(part, name)
            if value is _HOST_ATTRIBUTE_MISSING:
                continue
            if isinstance(value, str):
                texts.append(value)
    return tuple(texts)


@register(
    "astrbot_plugin_astrcontinuum",
    "Ayleovelle",
    "Non-blocking infinite context runtime for AstrBot",
    "0.2.0",
)
class AstrContinuumPlugin(Star):
    """One standards-compliant Star with reversible provider projection."""

    def __init__(
        self,
        context: Context,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self._enabled = bool(self.config.get("enabled", True))
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._bridge: AstrBotHookBridge | None = None
        self._providers: SessionProviderRegistry | None = None
        self._worker: CompactionWorker | None = None
        self._tokenizer_registry = TokenizerRegistry()
        self._tokenizer_router = TokenizerRouter()
        self._context_limit_resolver = ContextLimitResolver()
        self._context_engine_mode = self._configured_context_engine_mode()
        self._storage_status = self._initial_storage_status()

    def _configured_context_engine_mode(self) -> ContextEngineMode:
        value = self.config.get("context_engine_mode", ContextEngineMode.ACTIVE.value)
        if not isinstance(value, str):
            return ContextEngineMode.ACTIVE
        try:
            return ContextEngineMode(value.strip().upper())
        except ValueError:
            return ContextEngineMode.ACTIVE

    @staticmethod
    def _key_source_label(source: KeySource) -> str:
        if source is KeySource.FILE:
            return "服务器密钥文件"
        if source is KeySource.LOCAL:
            return "自动管理"
        return "环境变量"

    def _configured_key_source_label(self) -> str:
        try:
            return self._key_source_label(resolve_key_source(self.config))
        except StorageSecurityError:
            return "unknown"

    def _initial_storage_status(self) -> _StorageRuntimeStatus:
        if not self._enabled:
            return _StorageRuntimeStatus(
                protection="DISABLED",
                key_source="none",
                key_id="NONE",
                maintenance="INACTIVE",
                security_code="NONE",
            )
        return _StorageRuntimeStatus(
            protection="LOCKED",
            key_source=self._configured_key_source_label(),
            key_id="NONE",
            maintenance="UNKNOWN",
            security_code="STORAGE_NOT_INITIALIZED",
        )

    def _locked_storage_status(
        self,
        code: str,
        *,
        key_source: str | None = None,
        key_id: str | None = None,
    ) -> _StorageRuntimeStatus:
        current = self._storage_status
        return _StorageRuntimeStatus(
            protection="LOCKED",
            key_source=key_source or current.key_source,
            key_id=key_id or current.key_id,
            maintenance="UNKNOWN",
            security_code=code,
        )

    @staticmethod
    def _storage_lock_hint(status: _StorageRuntimeStatus) -> str:
        if status.security_code == SecurityErrorCode.STORAGE_KEY_MISSING.value:
            return (
                "下一步：将密钥来源设为“环境变量”并在服务器进程中注入 "
                "ASTRCONTINUUM_MASTER_KEY，或改为“自动管理”/“服务器密钥文件”"
                "后重载插件；"
                "不要在 WebUI、聊天或日志中粘贴密钥正文。"
            )
        return "提示：修复密钥配置后重载插件；不要在 WebUI、聊天或日志中粘贴密钥正文。"

    def _budget_values(self) -> tuple[int, int, int, int]:
        defaults = BudgetConfig()
        target = self.config.get(
            "target_input_budget",
            defaults.target_input_budget,
        )
        hard = self.config.get(
            "hard_input_ceiling",
            defaults.hard_input_ceiling,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (target, hard)
        ):
            logger.warning(
                "AstrContinuum configuration fallback code=%s",
                "BUDGET_CONFIG_INVALID",
            )
            target = defaults.target_input_budget
            hard = defaults.hard_input_ceiling
        return (
            target,
            hard,
            defaults.reserved_output_and_tools,
            defaults.safety_margin,
        )

    def _request_profile(
        self,
        route: TokenizerRoute,
        context_limit: ContextLimitDecision,
        *,
        model_identity: str | None,
    ) -> RequestBudgetProfile:
        target, hard, reserved, safety = self._budget_values()
        compact_ratio = self.config.get("compaction_start_ratio", 0.75)
        project_ratio = self.config.get("provider_view_switch_ratio", 0.80)
        stable_code = (
            context_limit.stable_code if context_limit.stable_code != "NONE" else route.stable_code
        )
        try:
            return RequestBudgetProfile(
                model_identity=model_identity,
                context_limit=context_limit.limit,
                context_limit_source=context_limit.source,
                tokenizer_profile=route.profile,
                target_input_budget=target,
                hard_input_ceiling=hard,
                reserved_output_and_tools=reserved,
                safety_margin=safety,
                compaction_start_ratio=compact_ratio,
                provider_view_switch_ratio=project_ratio,
                stable_code=stable_code,
            )
        except (TypeError, ValueError):
            logger.warning(
                "AstrContinuum configuration fallback code=%s",
                "PRESSURE_CONFIG_INVALID",
            )
            return RequestBudgetProfile(
                model_identity=model_identity,
                context_limit=context_limit.limit,
                context_limit_source=context_limit.source,
                tokenizer_profile=route.profile,
                target_input_budget=target,
                hard_input_ceiling=hard,
                reserved_output_and_tools=reserved,
                safety_margin=safety,
                stable_code=stable_code,
            )

    def _worker_config(self) -> CompactionWorkerConfig:
        target, hard, _reserved, _safety = self._budget_values()
        defaults = {
            "lease_seconds": 300.0,
            "poll_interval_seconds": 1.0,
            "retry_base_seconds": 5.0,
            "retry_max_seconds": 300.0,
            "max_attempts": 3,
        }
        try:
            return CompactionWorkerConfig(
                token_ceiling=min(
                    target,
                    hard,
                ),
                lease_seconds=self.config.get(
                    "worker_lease_seconds",
                    defaults["lease_seconds"],
                ),
                poll_interval_seconds=self.config.get(
                    "worker_poll_interval_seconds",
                    defaults["poll_interval_seconds"],
                ),
                retry_base_seconds=self.config.get(
                    "worker_retry_base_seconds",
                    defaults["retry_base_seconds"],
                ),
                retry_max_seconds=self.config.get(
                    "worker_retry_max_seconds",
                    defaults["retry_max_seconds"],
                ),
                max_attempts=self.config.get(
                    "worker_max_attempts",
                    defaults["max_attempts"],
                ),
            )
        except (TypeError, ValueError):
            logger.warning(
                "AstrContinuum configuration fallback code=%s",
                "WORKER_CONFIG_INVALID",
            )
            return CompactionWorkerConfig(
                token_ceiling=min(
                    target,
                    hard,
                ),
            )

    async def _generate_compaction(
        self,
        provider_id: str,
        system_prompt: str,
        prompt: str,
    ) -> str:
        generate = getattr(self.context, "llm_generate", None)
        if not callable(generate):
            raise TypeError("AstrBot llm_generate is unavailable")
        response = await generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
            tools=None,
        )
        content = getattr(response, "completion_text", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("compaction provider returned no text")
        return content

    def _resolve_explicit_compaction_binding(self) -> CompactionProviderBinding | None:
        configured = self.config.get("compaction_provider_id", "")
        provider_id = configured.strip() if isinstance(configured, str) else ""
        if not provider_id:
            return None
        resolver = getattr(self.context, "get_provider_by_id", None)
        if not callable(resolver):
            return None
        try:
            provider = resolver(provider_id)
            if provider is None:
                return None
            get_model = getattr(provider, "get_model", None)
            model_identity = (
                normalize_model_identity(get_model()) if callable(get_model) else None
            )
            provider_limit: int | None = None
            provider_config = getattr(provider, "provider_config", None)
            if isinstance(provider_config, Mapping):
                raw_limit = provider_config.get("max_context_tokens")
                if (
                    not isinstance(raw_limit, bool)
                    and isinstance(raw_limit, int)
                    and raw_limit > 0
                ):
                    provider_limit = raw_limit
            context_limit = self._context_limit_resolver.resolve(
                self.config.get("model_context_limit", 0),
                provider_limit=provider_limit,
                request_model=model_identity,
                provider_model=model_identity,
            )
            route = self._tokenizer_router.route(model_identity)
            profile = self._request_profile(
                route,
                context_limit,
                model_identity=model_identity,
            )
            if profile.effective_input_budget < 1:
                return None
            return CompactionProviderBinding(
                provider_id=provider_id,
                model_identity=model_identity,
                context_limit=profile.effective_input_budget,
                context_limit_source=context_limit.source,
            )
        except Exception:  # noqa: BLE001 - defer with a stable unavailable code
            return None

    def _invalidate_current_provider_binding(
        self,
        event: AstrMessageEvent,
        request: Any,
    ) -> None:
        providers = self._providers
        if providers is None:
            return
        override = self.config.get("compaction_provider_id", "")
        if isinstance(override, str) and override.strip():
            return
        try:
            conversation = request.conversation
            session_key = extract_host_session_key(event, conversation)
        except Exception:  # noqa: BLE001 - request preparation reports identity faults
            return
        providers.remember_unavailable(session_key)

    async def _remember_current_provider(
        self,
        event: AstrMessageEvent,
        state: _RequestState,
    ) -> None:
        providers = self._providers
        prepared = state.prepared
        if providers is None or prepared is None:
            return
        override = self.config.get("compaction_provider_id", "")
        if isinstance(override, str) and override.strip():
            return
        resolver = getattr(self.context, "get_current_chat_provider_id", None)
        umo = getattr(event, "unified_msg_origin", None)
        if not callable(resolver) or not isinstance(umo, str) or not umo:
            providers.remember_unavailable(prepared.turn.session_key)
            self._record_fault(
                state,
                _generic_fault("PROVIDER_RESOLUTION_UNAVAILABLE", "REQUEST"),
            )
            return
        try:
            provider_id = await resolver(umo=umo)
            profile = prepared.budget_profile
            if profile.effective_input_budget < 1:
                providers.remember_unavailable(prepared.turn.session_key)
                return
            providers.remember(
                prepared.turn.session_key,
                CompactionProviderBinding(
                    provider_id=provider_id,
                    model_identity=profile.model_identity,
                    context_limit=profile.effective_input_budget,
                    context_limit_source=profile.context_limit_source,
                ),
            )
        except Exception:  # noqa: BLE001 - provider details stay private
            providers.remember_unavailable(prepared.turn.session_key)
            self._record_fault(
                state,
                _generic_fault("PROVIDER_RESOLUTION_FAILED", "REQUEST"),
            )

    @staticmethod
    def _restore_request_conversation(state: _RequestState) -> None:
        if state.original_conversation is None:
            return
        try:
            state.request.conversation = state.original_conversation
        finally:
            state.original_conversation = None

    def _mask_request_token_usage(self, state: _RequestState) -> None:
        conversation = getattr(state.request, "conversation", None)
        if conversation is None or state.original_conversation is not None:
            return
        masked = copy.copy(conversation)
        masked.token_usage = 0
        state.original_conversation = conversation
        state.request.conversation = masked

    @staticmethod
    async def _close_worker_safely(worker: CompactionWorker | None) -> None:
        if worker is None:
            return
        try:
            await worker.close()
        except Exception:  # noqa: BLE001 - never expose worker internals
            logger.error(
                "AstrContinuum worker close failed code=%s",
                "WORKER_CLOSE_FAILED",
            )

    async def initialize(self) -> None:
        """Authenticate storage and initialize runtime components exactly once."""

        async with self._initialize_lock:
            if self._initialized:
                return
            if not self._enabled:
                self._storage_status = self._initial_storage_status()
                self._initialized = True
                return

            worker: CompactionWorker | None = None
            key_source = self._configured_key_source_label()
            key_id = "NONE"
            try:
                data_dir = await asyncio.to_thread(StarTools.get_data_dir, _PLUGIN_NAME)
                keys = await _complete_thread_call(
                    resolve_key_material,
                    self.config,
                    data_dir,
                )
                key_source = self._key_source_label(keys.source)
                factory = SQLiteConnectionFactory(data_dir)
                activation = await _complete_thread_call(
                    activate_storage_security,
                    factory,
                    keys,
                )
                if activation.state is not StorageMaintenanceState.ACTIVE:
                    raise StorageSecurityError(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
                key_id = activation.key_id
                repository = SQLiteRepository(factory, codec=activation.codec)
                compatibility_counter = Utf8ByteTokenCounter()
                bridge = AstrBotHookBridge(
                    repository,
                    counter_provider=self._tokenizer_registry.counter_for,
                    context_engine_mode=self._context_engine_mode,
                )
                configured_provider = self.config.get("compaction_provider_id", "")
                provider_override_configured = (
                    isinstance(configured_provider, str) and bool(configured_provider.strip())
                )
                providers = SessionProviderRegistry(
                    provider_override=self._resolve_explicit_compaction_binding(),
                    provider_override_configured=provider_override_configured,
                )
                backend = AstrBotExtractiveCompilerBackend(
                    generator=self._generate_compaction,
                    providers=providers,
                    compatibility_counter=compatibility_counter,
                    tokenizer_router=self._tokenizer_router,
                    counter_provider=self._tokenizer_registry.counter_for,
                )
                try:
                    canonical_counter = await asyncio.to_thread(
                        self._tokenizer_registry.counter_for,
                        CANONICAL_O200K,
                    )
                except Exception:  # noqa: BLE001 - worker exposes only stable deferral codes
                    canonical_counter = None
                worker = CompactionWorker(
                    repository=repository,
                    backend=backend,
                    compatibility_counter=compatibility_counter,
                    canonical_counter=canonical_counter,
                    canonical_profile_id=CANONICAL_O200K.profile_id,
                    provider_bindings=providers,
                    worker_id=f"astrbot-{uuid.uuid4().hex}",
                    config=self._worker_config(),
                    fatal_storage_callback=self._on_worker_storage_failure,
                )
                await worker.start()
            except asyncio.CancelledError:
                await self._close_worker_safely(worker)
                raise
            except StorageSecurityError as error:
                await self._close_worker_safely(worker)
                self._bridge = None
                self._providers = None
                self._worker = None
                self._storage_status = self._locked_storage_status(
                    error.code.value,
                    key_source=key_source,
                    key_id=key_id,
                )
                self._initialized = True
                logger.error(
                    "AstrContinuum storage locked code=%s",
                    error.code.value,
                )
                return
            except Exception:  # noqa: BLE001 - startup details stay private
                await self._close_worker_safely(worker)
                self._bridge = None
                self._providers = None
                self._worker = None
                self._storage_status = self._locked_storage_status(
                    "STORAGE_STARTUP_FAILED",
                    key_source=key_source,
                    key_id=key_id,
                )
                self._initialized = True
                logger.error(
                    "AstrContinuum storage locked code=%s",
                    "STORAGE_STARTUP_FAILED",
                )
                return

            self._bridge = bridge
            self._providers = providers
            self._worker = worker
            self._storage_status = _StorageRuntimeStatus(
                protection=("LOCAL_KEY_DEGRADED" if keys.local_degraded else "ACTIVE"),
                key_source=key_source,
                key_id=activation.key_id,
                maintenance=activation.state.value,
                security_code="NONE",
            )
            self._initialized = True
            logger.info(
                "AstrContinuum initialized protection=%s key_source=%s key_id=%s",
                self._storage_status.protection,
                self._storage_status.key_source,
                self._storage_status.key_id,
            )

    async def _enter_storage_locked(self, error: StorageSecurityError) -> None:
        current_task = asyncio.current_task()
        async with self._initialize_lock:
            worker = self._worker
            self._bridge = None
            self._providers = None
            self._worker = None
            self._storage_status = self._locked_storage_status(error.code.value)
            self._initialized = True
            if worker is not None and worker.task is not current_task:
                await self._close_worker_safely(worker)
        logger.error(
            "AstrContinuum storage locked code=%s",
            error.code.value,
        )

    async def _on_worker_storage_failure(self, error: StorageSecurityError) -> None:
        await self._enter_storage_locked(error)

    async def terminate(self) -> None:
        """Release request composition state idempotently."""

        async with self._initialize_lock:
            worker = self._worker
            self._bridge = None
            self._providers = None
            self._worker = None
            self._initialized = False
            self._storage_status = self._initial_storage_status()
            await self._close_worker_safely(worker)

    def _record_fault(
        self,
        state: _RequestState,
        fault: AdapterFault,
    ) -> None:
        state.faults.append(fault)
        logger.warning(
            "AstrContinuum fail-open code=%s stage=%s missing_fields=%d capability=%s",
            fault.code,
            fault.stage,
            fault.missing_field_count,
            fault.capability_available,
        )

    def _store_state(self, event: AstrMessageEvent, state: _RequestState) -> bool:
        setter = getattr(event, "set_extra", None)
        if not callable(setter):
            logger.warning(
                "AstrContinuum fail-open code=%s stage=%s",
                "EVENT_EXTRA_UNAVAILABLE",
                "REQUEST",
            )
            return False
        try:
            setter(_REQUEST_STATE_KEY, state)
        except Exception:  # noqa: BLE001 - host details stay private
            logger.warning(
                "AstrContinuum fail-open code=%s stage=%s",
                "EVENT_EXTRA_WRITE_FAILED",
                "REQUEST",
            )
            return False
        return True

    def _state(self, event: AstrMessageEvent) -> _RequestState | None:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return None
        try:
            value = getter(_REQUEST_STATE_KEY, None)
        except Exception:  # noqa: BLE001 - host details stay private
            return None
        return value if isinstance(value, _RequestState) else None

    async def _ensure_ready(
        self,
        state: _RequestState,
    ) -> bool:
        try:
            await self.initialize()
        except Exception:  # noqa: BLE001 - initialization details stay private
            self._record_fault(
                state,
                _generic_fault("INITIALIZE_FAILED", "INITIALIZE"),
            )
            return False
        return self._enabled and self._bridge is not None

    @filter.on_llm_request(priority=2000)
    async def on_llm_request(self, event: AstrMessageEvent, req: Any) -> None:
        """Capture user input and prepare one immutable request view."""

        state = _RequestState(request=req)
        if not self._store_state(event, state):
            return
        if not await self._ensure_ready(state):
            return
        bridge = self._bridge
        if bridge is None:
            return
        self._invalidate_current_provider_binding(event, req)
        try:
            metadata = resolve_astrbot_request_metadata(self.context, event, req)
            context_limit = self._context_limit_resolver.resolve(
                self.config.get("model_context_limit", 0),
                provider_limit=metadata.provider_limit,
                request_model=metadata.request_model,
                provider_model=metadata.provider_model,
            )
            route = self._tokenizer_router.route(metadata.model_identity)
            profile = self._request_profile(
                route,
                context_limit,
                model_identity=metadata.model_identity,
            )
            state.prepared = await bridge.prepare_request(
                event,
                req,
                budget_profile=profile,
            )
            await self._remember_current_provider(event, state)
        except StorageSecurityError as error:
            await self._enter_storage_locked(error)
            self._record_fault(
                state,
                _generic_fault(error.code.value, "STORAGE"),
            )
        except AstrBotAdapterError as error:
            self._record_fault(state, error.fault)
        except Exception:  # noqa: BLE001 - persistence details stay private
            self._record_fault(
                state,
                _generic_fault("REQUEST_PREPARE_FAILED", "REQUEST"),
            )

    @filter.on_agent_begin(priority=2000)
    async def on_agent_begin_guard(
        self,
        event: AstrMessageEvent,
        run_context: Any,
    ) -> None:
        """Record the exact native identity graph before foreign projections."""

        state = self._state(event)
        if state is None or state.prepared is None or state.guard is not None:
            return
        messages = cast(list[object], getattr(run_context, "messages", None))
        try:
            system_objects, current_objects = select_projection_boundaries(messages)
            guard = guard_projection(
                state.request,
                messages,
                system_objects=system_objects,
                current_objects=current_objects,
            )
            if guard.request_identity != state.prepared.turn.request_identity:
                self._record_fault(
                    state,
                    _generic_fault("REQUEST_IDENTITY_MISMATCH", "GUARD"),
                )
                return
            state.guard = guard
        except AstrBotAdapterError as error:
            self._record_fault(state, error.fault)
        except ProjectionInvariantError as error:
            self._record_fault(state, _projection_fault(error))
        except Exception:  # noqa: BLE001 - host object details stay private
            self._record_fault(
                state,
                _generic_fault("GUARD_FAILED", "GUARD"),
            )

    @filter.on_agent_begin(priority=-100)
    async def on_agent_begin_project(
        self,
        event: AstrMessageEvent,
        run_context: Any,
    ) -> None:
        """Assemble once and project only AC-owned temporary objects."""

        state = self._state(event)
        bridge = self._bridge
        if (
            state is None
            or state.prepared is None
            or state.guard is None
            or state.projected is not None
            or bridge is None
        ):
            return
        messages = cast(list[object], getattr(run_context, "messages", None))
        if not isinstance(messages, list):
            self._record_fault(
                state,
                _generic_fault("MESSAGE_LIST_INVALID", "PROJECT"),
            )
            return
        guard = state.guard
        retained = tuple(
            item for item in messages if not _contains_identity(guard.native_history_objects, item)
        )
        preserved = (*guard.system_objects, *guard.current_objects)
        opaque_objects = tuple(item for item in retained if not _contains_identity(preserved, item))
        try:
            host_view = HostBudgetView(
                all_host_texts=tuple(text for message in messages for text in _host_texts(message)),
                opaque_texts=tuple(
                    text for message in opaque_objects for text in _host_texts(message)
                ),
                fixed_required_texts=tuple(
                    text for message in guard.system_objects for text in _host_texts(message)
                ),
            )
            outcome = await bridge.evaluate_prepared(
                state.prepared,
                host_budget_view=host_view,
            )
            state.outcome = outcome
            state.pressure = outcome.pressure
            if not state.pressure.should_project:
                return
            if not outcome.mutation_allowed or outcome.assembly is None:
                self._record_fault(
                    state,
                    _generic_fault(outcome.stable_code, "BUDGET"),
                )
                return
            current_user_message = guard.current_objects[0]
            factory = projection_factory_from_user_message(current_user_message)
            built = build_projection_objects(
                outcome.assembly.projected_text,
                factory,
            )
            if built.fault is not None:
                self._record_fault(state, built.fault)
                return
            self._mask_request_token_usage(state)
            projection_objects = built.objects
            state.projected = project(messages, guard, projection_objects)
        except AstrBotAdapterError as error:
            self._restore_request_conversation(state)
            self._record_fault(state, error.fault)
        except TokenizerError as error:
            self._restore_request_conversation(state)
            self._record_fault(
                state,
                _generic_fault(error.code.value, "BUDGET"),
            )
        except BudgetInvariantError as error:
            self._restore_request_conversation(state)
            self._record_fault(
                state,
                _generic_fault(error.code, "BUDGET"),
            )
        except ProjectionInvariantError as error:
            self._record_fault(state, _projection_fault(error))
            self._restore_request_conversation(state)
        except Exception:  # noqa: BLE001 - optional enhancement fails open
            self._restore_request_conversation(state)
            self._record_fault(
                state,
                _generic_fault(
                    "PROJECTION_FAILED",
                    "PROJECT",
                ),
            )

    @filter.on_agent_done(priority=2000)
    async def on_agent_done_restore(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
    ) -> None:
        """Restore exact pre-AC objects plus the appended provider Delta."""

        del response
        state = self._state(event)
        if state is None or state.projected is None or state.restored is not None:
            return
        messages = cast(list[object], getattr(run_context, "messages", None))
        try:
            state.restored = restore(messages, state.projected)
        except ProjectionInvariantError as error:
            self._record_fault(state, _projection_fault(error))
        except Exception:  # noqa: BLE001 - host object details stay private
            self._record_fault(
                state,
                _generic_fault("RESTORE_FAILED", "RESTORE"),
            )
        finally:
            self._restore_request_conversation(state)

    @filter.on_agent_done(priority=900)
    async def on_agent_done_finalize(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
    ) -> None:
        """Verify native persistence, capture assistant, and raise durable intent."""

        state = self._state(event)
        bridge = self._bridge
        if state is None or state.prepared is None or bridge is None:
            return
        messages = cast(list[object], getattr(run_context, "messages", None))
        if state.projected is not None:
            if state.restored is None:
                self._record_fault(
                    state,
                    _generic_fault("RESTORE_NOT_COMPLETED", "VERIFY"),
                )
            elif not verify_native(messages, state.restored):
                self._record_fault(
                    state,
                    _generic_fault("NATIVE_VERIFY_FAILED", "VERIFY"),
                )
        content = getattr(response, "completion_text", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            self._record_fault(
                state,
                _generic_fault("ASSISTANT_CONTENT_INVALID", "ASSISTANT_CAPTURE"),
            )
            content = ""
        if state.assistant_event is None:
            try:
                state.assistant_event = await bridge.capture_assistant(
                    state.prepared,
                    content,
                )
            except StorageSecurityError as error:
                await self._enter_storage_locked(error)
                self._record_fault(
                    state,
                    _generic_fault(error.code.value, "STORAGE"),
                )
                return
            except AstrBotAdapterError as error:
                self._record_fault(state, error.fault)
                return
            except Exception:  # noqa: BLE001 - persistence details stay private
                self._record_fault(
                    state,
                    _generic_fault("ASSISTANT_CAPTURE_FAILED", "ASSISTANT_CAPTURE"),
                )
                return
        if state.pressure is not None and state.pressure.should_compact and not state.intent_raised:
            try:
                job = await bridge.raise_compaction_intent(
                    state.prepared,
                    target_high_water_mark=state.assistant_event.sequence,
                )
                state.intent_raised = True
                if job is not None and self._worker is not None:
                    self._worker.wake()
            except StorageSecurityError as error:
                await self._enter_storage_locked(error)
                self._record_fault(
                    state,
                    _generic_fault(error.code.value, "STORAGE"),
                )
            except AstrBotAdapterError as error:
                self._record_fault(state, error.fault)
            except Exception:  # noqa: BLE001 - scheduling details stay private
                self._record_fault(
                    state,
                    _generic_fault("COMPACTION_INTENT_FAILED", "COMPACTION_INTENT"),
                )

    @filter.on_using_llm_tool()
    async def on_using_llm_tool(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
    ) -> None:
        """Write only the authoritative tool-call event.

        Args:
            event (AstrMessageEvent): Current AstrBot message event.
            tool (object): Tool descriptor supplied by AstrBot.
            tool_args (object): Tool arguments supplied by the agent runner.
        """

        state = self._state(event)
        bridge = self._bridge
        if state is None or state.prepared is None or bridge is None:
            return
        ordinal = state.next_tool_ordinal
        state.next_tool_ordinal += 1
        state.pending_tool_ordinals.append(ordinal)
        try:
            await bridge.capture_tool_call(
                state.prepared,
                tool,
                tool_args,
                ordinal=ordinal,
            )
        except StorageSecurityError as error:
            await self._enter_storage_locked(error)
            self._record_fault(
                state,
                _generic_fault(error.code.value, "STORAGE"),
            )
        except AstrBotAdapterError as error:
            self._record_fault(state, error.fault)
        except Exception:  # noqa: BLE001 - persistence details stay private
            self._record_fault(
                state,
                _generic_fault("TOOL_CALL_CAPTURE_FAILED", "TOOL_CALL"),
            )

    @filter.on_llm_tool_respond()
    async def on_llm_tool_respond(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> None:
        """Write only the authoritative tool-result event.

        Args:
            event (AstrMessageEvent): Current AstrBot message event.
            tool (object): Tool descriptor supplied by AstrBot.
            tool_args (object): Tool arguments supplied by the agent runner.
            tool_result (object): Authoritative result returned by the tool.
        """

        state = self._state(event)
        bridge = self._bridge
        if state is None or state.prepared is None or bridge is None:
            return
        if state.pending_tool_ordinals:
            ordinal = state.pending_tool_ordinals.pop(0)
        else:
            ordinal = state.next_tool_ordinal
            state.next_tool_ordinal += 1
        try:
            await bridge.capture_tool_result(
                state.prepared,
                tool,
                tool_args,
                tool_result,
                ordinal=ordinal,
            )
        except StorageSecurityError as error:
            await self._enter_storage_locked(error)
            self._record_fault(
                state,
                _generic_fault(error.code.value, "STORAGE"),
            )
        except AstrBotAdapterError as error:
            self._record_fault(state, error.fault)
        except Exception:  # noqa: BLE001 - persistence details stay private
            self._record_fault(
                state,
                _generic_fault("TOOL_RESULT_CAPTURE_FAILED", "TOOL_RESULT"),
            )

    @filter.on_llm_response()
    async def on_llm_response(
        self,
        event: AstrMessageEvent,
        response: Any,
    ) -> None:
        """Observe completion without writing Journal rows."""

        del response
        state = self._state(event)
        if state is not None:
            self._restore_request_conversation(state)

    @staticmethod
    def _security_status_lines(status: _StorageRuntimeStatus) -> tuple[str, ...]:
        return (
            f"数据保护：{status.protection}",
            f"加密格式：{_ENCRYPTION_FORMAT}",
            f"密钥来源：{status.key_source}",
            f"活动密钥标识：{status.key_id}",
            f"存储维护：{status.maintenance}",
            f"安全代码：{status.security_code}",
        )

    @staticmethod
    def _safe_operational_label(value: object, *, fallback: str = "INVALID") -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            return fallback
        if not value.isascii() or any(
            not (character.isalnum() or character in "_-") for character in value
        ):
            return fallback
        return value

    @classmethod
    def _enum_operational_label(
        cls,
        value: object,
        *,
        fallback: str = "INVALID",
    ) -> str:
        return cls._safe_operational_label(
            getattr(value, "value", value),
            fallback=fallback,
        )

    @staticmethod
    def _bounded_integer_label(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "INVALID"
        if value > _TRACE_INTEGER_LIMIT:
            return f"{_TRACE_INTEGER_LIMIT}+"
        return str(value)

    @staticmethod
    def _bounded_reduction_ratio(
        candidate_count: object,
        reduced_count: object,
    ) -> str:
        if (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count < 0
            or isinstance(reduced_count, bool)
            or not isinstance(reduced_count, int)
            or reduced_count < 0
        ):
            return "INVALID"
        if candidate_count == 0:
            return "0.00%" if reduced_count == 0 else "INVALID"
        bounded_reduced = min(candidate_count, reduced_count)
        hundredths = bounded_reduced * 10_000 // candidate_count
        return f"{hundredths // 100}.{hundredths % 100:02d}%"

    @staticmethod
    def _coverage_label(value: object) -> str:
        if value is True:
            return "PASS"
        if value is False:
            return "FAIL"
        return "UNKNOWN"

    def _latest_engine_state(self, trace: LiveContextTrace | None) -> str:
        if trace is None:
            return "NONE"
        mode = self._enum_operational_label(getattr(trace, "mode", None))
        outcome = self._enum_operational_label(getattr(trace, "outcome", None))
        if mode != self._context_engine_mode.value:
            return "UNVERIFIED"
        if mode == ContextEngineMode.OFF.value or outcome == "OFF":
            return "NONE"
        if outcome == "DEGRADED_RAW":
            return "DEGRADED_RAW"
        if mode == ContextEngineMode.SHADOW.value or outcome == "SHADOW":
            return "UNVERIFIED"
        if outcome != "ACTIVE":
            return "UNVERIFIED"
        if (
            getattr(trace, "required_passed", None) is not True
            or getattr(trace, "provenance_passed", None) is not True
        ):
            return "UNVERIFIED"
        recovery_count = getattr(trace, "adaptive_retry_count", None)
        if isinstance(recovery_count, bool) or not isinstance(recovery_count, int):
            return "UNVERIFIED"
        return "REFINED" if recovery_count > 0 else "VERIFIED"

    @staticmethod
    def _latest_context_trace(bridge: AstrBotHookBridge | None) -> LiveContextTrace | None:
        if bridge is None:
            return None
        try:
            return bridge.last_context_trace
        except Exception:  # noqa: BLE001 - observability is content-free and fail-open
            return None

    def _engine_status_lines(
        self,
        trace: LiveContextTrace | None,
    ) -> tuple[str, str]:
        return (
            f"上下文引擎：{self._context_engine_mode.value}",
            f"最近引擎状态：{self._latest_engine_state(trace)}",
        )

    def _normalize_context_trace(
        self,
        trace: LiveContextTrace | None,
    ) -> _ContextEngineInspection:
        if trace is None:
            return _ContextEngineInspection(
                mode=self._context_engine_mode.value,
                state="NONE",
                stable_code="CONTEXT_TRACE_UNAVAILABLE",
                candidate_count="UNAVAILABLE",
                selected_count="UNAVAILABLE",
                relation_count="UNAVAILABLE",
                constraint_count="UNAVAILABLE",
                retained_count="UNAVAILABLE",
                reduced_count="UNAVAILABLE",
                selected_budget_units="UNAVAILABLE",
                reduction_ratio="UNAVAILABLE",
                residual_band="UNAVAILABLE",
                recovery_count="UNAVAILABLE",
                required_coverage="UNKNOWN",
                provenance_coverage="UNKNOWN",
            )
        error_code = getattr(trace, "error_code", None)
        stable_code = "NONE" if error_code is None else self._safe_operational_label(error_code)
        return _ContextEngineInspection(
            mode=self._context_engine_mode.value,
            state=self._latest_engine_state(trace),
            stable_code=stable_code,
            candidate_count=self._bounded_integer_label(getattr(trace, "candidate_count", None)),
            selected_count=self._bounded_integer_label(getattr(trace, "selected_count", None)),
            relation_count=self._bounded_integer_label(getattr(trace, "relation_count", None)),
            constraint_count=self._bounded_integer_label(getattr(trace, "constraint_count", None)),
            retained_count=self._bounded_integer_label(getattr(trace, "retained_count", None)),
            reduced_count=self._bounded_integer_label(getattr(trace, "reduced_count", None)),
            selected_budget_units=self._bounded_integer_label(
                getattr(trace, "selected_budget_units", None)
            ),
            reduction_ratio=self._bounded_reduction_ratio(
                getattr(trace, "candidate_count", None),
                getattr(trace, "reduced_count", None),
            ),
            residual_band=self._enum_operational_label(getattr(trace, "residual_band", None)),
            recovery_count=self._bounded_integer_label(
                getattr(trace, "adaptive_retry_count", None)
            ),
            required_coverage=self._coverage_label(getattr(trace, "required_passed", None)),
            provenance_coverage=self._coverage_label(getattr(trace, "provenance_passed", None)),
        )

    @staticmethod
    def _snapshot_suffix(value: str) -> str:
        suffix = value[-12:]
        if len(suffix) == 12 and all(character in "0123456789abcdef" for character in suffix):
            return suffix
        return "REDACTED"

    @classmethod
    def _read_session_inspection(
        cls,
        bridge: AstrBotHookBridge,
        session_key: SessionKey,
    ) -> _SessionInspection:
        view = bridge.repository.read_request_view(session_key)
        with bridge.repository.factory.connection(read_only=True) as connection:
            event_rows = connection.execute(
                """
                SELECT event_type, count(*)
                FROM journal_events
                WHERE session_key_hash = ?
                GROUP BY event_type
                """,
                (session_key.session_key_hash,),
            ).fetchall()
            pending_row = connection.execute(
                """
                SELECT state, error_code
                FROM compaction_jobs
                WHERE session_key_hash = ?
                  AND state IN (
                      'PENDING',
                      'LEASED',
                      'COMPILING',
                      'AUDITING',
                      'READY_TO_COMMIT',
                      'RETRY_WAIT'
                  )
                ORDER BY updated_at DESC, job_id DESC
                LIMIT 1
                """,
                (session_key.session_key_hash,),
            ).fetchone()

        event_count_map = {cls._safe_operational_label(row[0]): int(row[1]) for row in event_rows}
        event_counts = tuple(
            (event_type, event_count_map.get(event_type, 0)) for event_type in _EVENT_TYPES
        )
        capsule_counter = Counter(
            cls._safe_operational_label(membership.slot) for membership in view.memberships
        )
        capsule_slots = tuple(sorted(capsule_counter.items()))
        snapshot = view.snapshot
        snapshot_suffix = (
            cls._snapshot_suffix(snapshot.snapshot_id) if snapshot is not None else "NONE"
        )
        covered_range = f"1-{view.covered_event_end}" if snapshot is not None else "EMPTY"
        delta_range = (
            f"{view.delta[0].sequence}-{view.delta[-1].sequence}" if view.delta else "EMPTY"
        )
        pending_job_state = "NONE"
        retry_code = "NONE"
        if pending_row is not None:
            pending_job_state = cls._safe_operational_label(pending_row[0])
            if pending_row[1] is not None:
                retry_code = cls._safe_operational_label(pending_row[1])
        return _SessionInspection(
            snapshot_suffix=snapshot_suffix,
            pointer_version=view.pointer_version,
            covered_range=covered_range,
            high_water_mark=view.high_water_mark,
            delta_range=delta_range,
            event_counts=event_counts,
            capsule_slots=capsule_slots,
            pending_job_state=pending_job_state,
            retry_code=retry_code,
        )

    def _read_session_observability(
        self,
        bridge: AstrBotHookBridge,
        session_key: SessionKey,
    ) -> tuple[_SessionInspection, _ContextEngineInspection]:
        inspection = self._read_session_inspection(bridge, session_key)
        trace = bridge.inspect_context_trace(session_key)
        return inspection, self._normalize_context_trace(trace)

    async def _current_session_key(
        self,
        event: AstrMessageEvent,
    ) -> tuple[SessionKey | None, str]:
        umo = getattr(event, "unified_msg_origin", None)
        manager = getattr(self.context, "conversation_manager", None)
        get_current = getattr(manager, "get_curr_conversation_id", None)
        get_conversation = getattr(manager, "get_conversation", None)
        if (
            not isinstance(umo, str)
            or not umo.strip()
            or not callable(get_current)
            or not callable(get_conversation)
        ):
            return None, "CURRENT_SESSION_API_UNAVAILABLE"
        try:
            cid_before = await get_current(umo)
            if not isinstance(cid_before, str) or not cid_before.strip():
                return None, "NO_CURRENT_CONVERSATION"
            conversation = await get_conversation(umo, cid_before)
            if conversation is None:
                return None, "CURRENT_CONVERSATION_UNAVAILABLE"
            if getattr(conversation, "cid", None) != cid_before:
                return None, "CURRENT_CONVERSATION_CHANGED"
            cid_after = await get_current(umo)
            if cid_after != cid_before:
                return None, "CURRENT_CONVERSATION_CHANGED"
            return extract_host_session_key(event, conversation), "NONE"
        except AstrBotAdapterError as error:
            return None, error.code
        except Exception:  # noqa: BLE001 - host conversation details stay private
            logger.error(
                "AstrContinuum current-session lookup failed code=%s",
                "CURRENT_SESSION_LOOKUP_FAILED",
            )
            return None, "CURRENT_SESSION_LOOKUP_FAILED"

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("context_status")
    async def context_status(self, event: AstrMessageEvent):
        """Show content-free runtime and encrypted-storage health."""

        if not self._enabled:
            engine_lines = self._engine_status_lines(None)
            yield event.plain_result(
                "AstrContinuum：已停用\n" + "\n".join(engine_lines) + "\n"
                "后台归约：已停止\n"
                "提示：插件不会改写 AstrBot 保存的对话历史。"
            )
            return

        async with self._initialize_lock:
            status = self._storage_status
            bridge = self._bridge
        security_lines = self._security_status_lines(status)
        engine_lines = self._engine_status_lines(self._latest_context_trace(bridge))
        if status.protection == "LOCKED" or bridge is None:
            yield event.plain_result(
                "AstrContinuum：已锁定\n"
                + "\n".join((*security_lines,))
                + "\n"
                + "\n".join(engine_lines)
                + "\n"
                "后台归约：未启动\n" + self._storage_lock_hint(status)
            )
            return

        def read_counts() -> tuple[int, int, int]:
            with bridge.repository.factory.connection(read_only=True) as connection:
                event_count = int(
                    connection.execute("SELECT count(*) FROM journal_events").fetchone()[0]
                )
                checkpoint_count = int(
                    connection.execute("SELECT count(*) FROM snapshots").fetchone()[0]
                )
                pending_count = int(
                    connection.execute(
                        """
                        SELECT count(*)
                        FROM compaction_jobs
                        WHERE state IN (
                            'PENDING',
                            'LEASED',
                            'COMPILING',
                            'AUDITING',
                            'READY_TO_COMMIT',
                            'RETRY_WAIT'
                        )
                        """
                    ).fetchone()[0]
                )
                return event_count, checkpoint_count, pending_count

        query_failed = False
        try:
            event_count, checkpoint_count, pending_count = await asyncio.to_thread(read_counts)
        except Exception:  # noqa: BLE001 - never expose storage details to chat
            logger.error(
                "AstrContinuum status query failed code=%s",
                "STATUS_QUERY_FAILED",
            )
            query_failed = True

        async with self._initialize_lock:
            current_status = self._storage_status
            current_bridge = self._bridge
            current_worker = self._worker
        if current_bridge is not bridge or current_status != status:
            current_security_lines = self._security_status_lines(current_status)
            current_engine_lines = self._engine_status_lines(
                self._latest_context_trace(current_bridge)
            )
            if current_status.protection == "LOCKED" or current_bridge is None:
                yield event.plain_result(
                    "AstrContinuum：已锁定\n"
                    + "\n".join((*current_security_lines,))
                    + "\n"
                    + "\n".join(current_engine_lines)
                    + "\n后台归约：未启动\n"
                    + self._storage_lock_hint(current_status)
                )
            else:
                current_worker_task = current_worker.task if current_worker is not None else None
                current_worker_status = (
                    "运行中"
                    if current_worker_task is not None and not current_worker_task.done()
                    else "已停止"
                )
                yield event.plain_result(
                    "AstrContinuum：状态已变化\n"
                    + "\n".join((*current_security_lines,))
                    + "\n"
                    + "\n".join(current_engine_lines)
                    + f"\n后台归约：{current_worker_status}\n统计信息：请重试"
                )
            return

        worker_task = current_worker.task if current_worker is not None else None
        worker_status = "运行中" if worker_task is not None and not worker_task.done() else "已停止"
        security_lines = self._security_status_lines(current_status)
        engine_lines = self._engine_status_lines(self._latest_context_trace(current_bridge))
        if query_failed:
            yield event.plain_result(
                "AstrContinuum：运行中\n"
                + "\n".join((*security_lines,))
                + "\n"
                + "\n".join(engine_lines)
                + f"\n后台归约：{worker_status}\n统计信息：暂时无法读取"
            )
            return

        yield event.plain_result(
            "AstrContinuum：运行中\n"
            + "\n".join((*security_lines,))
            + "\n"
            + "\n".join(engine_lines)
            + "\n"
            f"后台归约：{worker_status}\n"
            f"已记录事件：{event_count}\n"
            f"已发布 Checkpoint：{checkpoint_count}\n"
            f"待处理任务：{pending_count}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("context_inspect")
    async def context_inspect(self, event: AstrMessageEvent):
        """Inspect only content-free provenance for the current AstrBot conversation."""

        if not self._enabled:
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n检查状态：不可用\n检查代码：PLUGIN_DISABLED"
            )
            return

        async with self._initialize_lock:
            status = self._storage_status
            bridge = self._bridge
        if status.protection == "LOCKED" or bridge is None:
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                f"检查代码：{status.security_code}\n"
                f"数据保护：{status.protection}\n" + self._storage_lock_hint(status)
            )
            return

        session_key, lookup_code = await self._current_session_key(event)
        if session_key is None:
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                f"检查代码：{lookup_code}\n"
                f"数据保护：{status.protection}"
            )
            return

        try:
            inspection, engine_inspection = await asyncio.to_thread(
                self._read_session_observability,
                bridge,
                session_key,
            )
        except StorageSecurityError as error:
            await self._enter_storage_locked(error)
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                f"检查代码：{error.code.value}\n"
                "数据保护：LOCKED"
            )
            return
        except Exception:  # noqa: BLE001 - never expose storage details to chat
            logger.error(
                "AstrContinuum inspection failed code=%s",
                "INSPECTION_QUERY_FAILED",
            )
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                "检查代码：INSPECTION_QUERY_FAILED\n"
                f"数据保护：{status.protection}"
            )
            return

        confirmed_key, confirmation_code = await self._current_session_key(event)
        if confirmed_key is None:
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                f"检查代码：{confirmation_code}\n"
                f"数据保护：{status.protection}"
            )
            return
        if confirmed_key != session_key:
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                "检查代码：CURRENT_CONVERSATION_CHANGED\n"
                f"数据保护：{status.protection}"
            )
            return
        async with self._initialize_lock:
            current_status = self._storage_status
            current_bridge = self._bridge
        if current_bridge is not bridge or current_status.protection == "LOCKED":
            yield event.plain_result(
                "AstrContinuum 当前会话检查\n"
                "检查状态：不可用\n"
                f"检查代码：{current_status.security_code}\n"
                f"数据保护：{current_status.protection}"
            )
            return
        status = current_status

        event_counts = ", ".join(
            f"{event_type}={count}" for event_type, count in inspection.event_counts
        )
        capsule_slots = (
            ", ".join(f"{slot}={count}" for slot, count in inspection.capsule_slots)
            if inspection.capsule_slots
            else "NONE"
        )
        yield event.plain_result(
            "AstrContinuum 当前会话检查\n"
            "检查状态：可用\n"
            f"数据保护：{status.protection}\n"
            f"安全代码：{status.security_code}\n"
            f"活动 Checkpoint：{inspection.snapshot_suffix}\n"
            f"指针版本：{inspection.pointer_version}\n"
            f"覆盖范围：{inspection.covered_range}\n"
            f"Journal 高水位：{inspection.high_water_mark}\n"
            f"Delta 范围：{inspection.delta_range}\n"
            f"事件计数：{event_counts}\n"
            f"Capsule 槽位：{capsule_slots}\n"
            f"待处理任务：{inspection.pending_job_state}\n"
            f"重试代码：{inspection.retry_code}\n"
            f"上下文引擎：{engine_inspection.mode}\n"
            f"最近引擎状态：{engine_inspection.state}\n"
            f"候选块：{engine_inspection.candidate_count}\n"
            f"选中块：{engine_inspection.selected_count}\n"
            f"关系数：{engine_inspection.relation_count}\n"
            f"约束数：{engine_inspection.constraint_count}\n"
            f"保留块：{engine_inspection.retained_count}\n"
            f"归约块：{engine_inspection.reduced_count}\n"
            f"选择预算单位：{engine_inspection.selected_budget_units}\n"
            f"归约比例：{engine_inspection.reduction_ratio}\n"
            f"残差带：{engine_inspection.residual_band}\n"
            f"恢复次数：{engine_inspection.recovery_count}\n"
            f"必选覆盖：{engine_inspection.required_coverage}\n"
            f"来源覆盖：{engine_inspection.provenance_coverage}\n"
            f"稳定代码：{engine_inspection.stable_code}"
        )
