"""AstrContinuum AstrBot plugin composition root."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections import Counter
from collections.abc import Callable
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
        ProjectionCapability,
        build_projection_objects,
        estimate_opaque_token_cost,
        extract_host_session_key,
        probe_projection_capability,
        select_projection_boundaries,
    )
    from astrcontinuum.compaction import (
        AstrBotExtractiveCompilerBackend,
        CompactionWorker,
        CompactionWorkerConfig,
        SessionProviderRegistry,
    )
    from astrcontinuum.context_graph import ContextEngineMode, LiveContextTrace
    from astrcontinuum.domain import EventEnvelope, SessionKey
    from astrcontinuum.runtime import (
        BudgetConfig,
        PressureConfig,
        PressureDecision,
        ProjectedView,
        ProjectionGuard,
        ProjectionInvariantError,
        RestoredView,
        Utf8ByteTokenCounter,
        assess_pressure,
        guard_projection,
        project,
        restore,
        verify_native,
    )
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
else:
    from .astrcontinuum.adapters import (
        AdapterFault,
        AstrBotAdapterError,
        AstrBotHookBridge,
        PreparedRequest,
        ProjectionCapability,
        build_projection_objects,
        estimate_opaque_token_cost,
        extract_host_session_key,
        probe_projection_capability,
        select_projection_boundaries,
    )
    from .astrcontinuum.compaction import (
        AstrBotExtractiveCompilerBackend,
        CompactionWorker,
        CompactionWorkerConfig,
        SessionProviderRegistry,
    )
    from .astrcontinuum.context_graph import ContextEngineMode, LiveContextTrace
    from .astrcontinuum.domain import EventEnvelope, SessionKey
    from .astrcontinuum.runtime import (
        BudgetConfig,
        PressureConfig,
        PressureDecision,
        ProjectedView,
        ProjectionGuard,
        ProjectionInvariantError,
        RestoredView,
        Utf8ByteTokenCounter,
        assess_pressure,
        guard_projection,
        project,
        restore,
        verify_native,
    )
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
        self._capability: ProjectionCapability | None = None
        self._providers: SessionProviderRegistry | None = None
        self._worker: CompactionWorker | None = None
        self._counter = Utf8ByteTokenCounter()
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

    def _budget_config(self) -> BudgetConfig:
        defaults = BudgetConfig()
        try:
            return BudgetConfig(
                target_input_budget=self.config.get(
                    "target_input_budget",
                    defaults.target_input_budget,
                ),
                hard_input_ceiling=self.config.get(
                    "hard_input_ceiling",
                    defaults.hard_input_ceiling,
                ),
                model_context_limit=self.config.get(
                    "model_context_limit",
                    defaults.model_context_limit,
                ),
                reserved_output_and_tools=defaults.reserved_output_and_tools,
                safety_margin=defaults.safety_margin,
            )
        except (TypeError, ValueError):
            logger.warning(
                "AstrContinuum configuration fallback code=%s",
                "BUDGET_CONFIG_INVALID",
            )
            return defaults

    def _pressure_config(self) -> PressureConfig:
        budget = self._budget_config()
        try:
            return PressureConfig(
                context_limit=budget.model_context_limit,
                reserved_output_and_tools=budget.reserved_output_and_tools,
                compact_ratio=self.config.get("compaction_start_ratio", 0.75),
                project_ratio=self.config.get("provider_view_switch_ratio", 0.80),
            )
        except (TypeError, ValueError):
            logger.warning(
                "AstrContinuum configuration fallback code=%s",
                "PRESSURE_CONFIG_INVALID",
            )
            return PressureConfig(
                context_limit=budget.model_context_limit,
                reserved_output_and_tools=budget.reserved_output_and_tools,
            )

    def _worker_config(self) -> CompactionWorkerConfig:
        budget = self._budget_config()
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
                    budget.target_input_budget,
                    budget.hard_input_ceiling,
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
                    budget.target_input_budget,
                    budget.hard_input_ceiling,
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
            self._record_fault(
                state,
                _generic_fault("PROVIDER_RESOLUTION_UNAVAILABLE", "REQUEST"),
            )
            return
        try:
            provider_id = await resolver(umo=umo)
            providers.remember(prepared.turn.session_key, provider_id)
        except Exception:  # noqa: BLE001 - provider details stay private
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

    def _project_empty(
        self,
        state: _RequestState,
        messages: list[object],
        guard: ProjectionGuard,
    ) -> None:
        try:
            state.projected = project(messages, guard, ())
        except ProjectionInvariantError as error:
            self._restore_request_conversation(state)
            self._record_fault(state, _projection_fault(error))
        except Exception:  # noqa: BLE001 - host object details stay private
            self._restore_request_conversation(state)
            self._record_fault(
                state,
                _generic_fault("EMPTY_PROJECTION_FAILED", "PROJECT"),
            )

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
                bridge = AstrBotHookBridge(
                    repository,
                    budget_config=self._budget_config(),
                    counter=self._counter,
                    context_engine_mode=self._context_engine_mode,
                )
                provider_override = self.config.get("compaction_provider_id", "")
                providers = SessionProviderRegistry(
                    provider_override=(
                        provider_override if isinstance(provider_override, str) else None
                    ),
                )
                backend = AstrBotExtractiveCompilerBackend(
                    generator=self._generate_compaction,
                    providers=providers,
                    counter=self._counter,
                )
                capability = probe_projection_capability()
                worker = CompactionWorker(
                    repository=repository,
                    backend=backend,
                    counter=self._counter,
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
                self._capability = None
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
                self._capability = None
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
            self._capability = capability
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
            self._capability = None
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
            self._capability = None
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
        try:
            state.prepared = await bridge.prepare_request(event, req)
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
        capability = self._capability
        if (
            state is None
            or state.prepared is None
            or state.guard is None
            or state.projected is not None
            or bridge is None
            or capability is None
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
            estimated_input_usage = estimate_opaque_token_cost(tuple(messages), self._counter)
            current_input_cost = self._counter.count_text(state.prepared.current_input)
            state.pressure = assess_pressure(
                trusted_token_usage=state.prepared.trusted_token_usage,
                estimated_input_usage=estimated_input_usage,
                current_input_cost=current_input_cost,
                config=self._pressure_config(),
            )
            if not state.pressure.should_project:
                return
            self._mask_request_token_usage(state)
            opaque_cost = estimate_opaque_token_cost(opaque_objects, self._counter)
            fixed_cost = estimate_opaque_token_cost(guard.system_objects, self._counter)
            assembly = await bridge.assemble_prepared(
                state.prepared,
                opaque_token_cost=opaque_cost,
                fixed_required_cost=fixed_cost,
            )
            projection_objects: tuple[object, ...] = ()
            if assembly.projected_text:
                built = build_projection_objects(
                    assembly.projected_text,
                    capability,
                )
                if built.fault is not None:
                    self._record_fault(state, built.fault)
                else:
                    projection_objects = built.objects
            state.projected = project(messages, guard, projection_objects)
        except AstrBotAdapterError as error:
            self._record_fault(state, error.fault)
            if state.pressure is not None and state.pressure.should_project:
                self._project_empty(state, messages, guard)
        except ProjectionInvariantError as error:
            self._record_fault(state, _projection_fault(error))
            self._restore_request_conversation(state)
        except Exception:  # noqa: BLE001 - optional enhancement fails open
            self._record_fault(
                state,
                _generic_fault(
                    "PROJECTION_FAILED",
                    "PROJECT",
                    capability_available=capability.available,
                ),
            )
            if state.pressure is not None and state.pressure.should_project:
                self._project_empty(state, messages, guard)

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
