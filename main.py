"""AstrContinuum AstrBot plugin composition root."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

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
        probe_projection_capability,
        select_projection_boundaries,
    )
    from astrcontinuum.compaction import (
        CompactionWorker,
        CompactionWorkerConfig,
        CompilerBackend,
        SegmenterConfig,
        SemanticAuditBackend,
    )
    from astrcontinuum.domain import EventEnvelope
    from astrcontinuum.runtime import (
        BudgetConfig,
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
    from astrcontinuum.scheduler import CoalescingCompactionScheduler
    from astrcontinuum.storage import (
        SQLiteConnectionFactory,
        SQLiteMigrator,
        SQLiteRepository,
    )
else:
    from .astrcontinuum.adapters import (
        AdapterFault,
        AstrBotAdapterError,
        AstrBotHookBridge,
        PreparedRequest,
        ProjectionCapability,
        build_projection_objects,
        estimate_opaque_token_cost,
        probe_projection_capability,
        select_projection_boundaries,
    )
    from .astrcontinuum.compaction import (
        CompactionWorker,
        CompactionWorkerConfig,
        CompilerBackend,
        SegmenterConfig,
        SemanticAuditBackend,
    )
    from .astrcontinuum.domain import EventEnvelope
    from .astrcontinuum.runtime import (
        BudgetConfig,
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
    from .astrcontinuum.scheduler import CoalescingCompactionScheduler
    from .astrcontinuum.storage import (
        SQLiteConnectionFactory,
        SQLiteMigrator,
        SQLiteRepository,
    )

_PLUGIN_NAME = "astrbot_plugin_astrcontinuum"
_REQUEST_STATE_KEY = "astrcontinuum.v1.request-state"


@dataclass(slots=True, repr=False)
class _RequestState:
    request: object = field(repr=False)
    prepared: PreparedRequest | None = field(default=None, repr=False)
    guard: ProjectionGuard | None = field(default=None, repr=False)
    projected: ProjectedView | None = field(default=None, repr=False)
    restored: RestoredView | None = field(default=None, repr=False)
    assistant_event: EventEnvelope | None = field(default=None, repr=False)
    intent_raised: bool = False
    next_tool_ordinal: int = 0
    pending_tool_ordinals: list[int] = field(default_factory=list, repr=False)
    faults: list[AdapterFault] = field(default_factory=list, repr=False)


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
    "0.1.0-alpha",
)
class AstrContinuumPlugin(Star):
    """One standards-compliant Star with reversible provider projection."""

    def __init__(
        self,
        context: Context,
        config: dict[str, Any] | None = None,
        *,
        compiler_backend: CompilerBackend | None = None,
        semantic_audit_backend: SemanticAuditBackend | None = None,
    ) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self._enabled = bool(self.config.get("enabled", True))
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._bridge: AstrBotHookBridge | None = None
        self._capability: ProjectionCapability | None = None
        self._counter = Utf8ByteTokenCounter()
        self._compiler_backend = compiler_backend
        self._semantic_audit_backend = semantic_audit_backend
        self._worker: CompactionWorker | None = None
        self._scheduler: CoalescingCompactionScheduler | None = None

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

    async def initialize(self) -> None:
        """Initialize durable storage exactly once per active lifecycle."""

        async with self._initialize_lock:
            if self._initialized:
                return
            if not self._enabled:
                self._initialized = True
                return
            data_dir = await asyncio.to_thread(StarTools.get_data_dir, _PLUGIN_NAME)
            factory = SQLiteConnectionFactory(data_dir)
            await asyncio.to_thread(SQLiteMigrator(factory).migrate)
            repository = SQLiteRepository(factory)
            self._bridge = AstrBotHookBridge(
                repository,
                budget_config=self._budget_config(),
                counter=self._counter,
            )
            self._capability = probe_projection_capability()
            self._initialized = True
            if self._compiler_backend is not None:
                scheduler: CoalescingCompactionScheduler | None = None
                try:
                    worker = CompactionWorker(
                        repository=repository,
                        compiler_backend=self._compiler_backend,
                        counter=self._counter,
                        config=CompactionWorkerConfig(
                            worker_id="astrcontinuum-main",
                            lease_duration=timedelta(seconds=30),
                            token_ceiling=130_000,
                            segmenter_config=SegmenterConfig(),
                            strict_audit=self._semantic_audit_backend is not None,
                            max_attempts=3,
                            retry_delay=timedelta(seconds=5),
                        ),
                        audit_backend=self._semantic_audit_backend,
                    )
                    async def wake_compaction_worker(session_key_hash: str) -> None:
                        await self._wake_compaction_worker(worker, session_key_hash)

                    scheduler = CoalescingCompactionScheduler(wake_compaction_worker)
                    await scheduler.start()
                    self._worker = worker
                    self._scheduler = scheduler
                except asyncio.CancelledError:
                    self._worker = None
                    self._scheduler = None
                    if scheduler is not None:
                        await scheduler.close()
                    raise
                except Exception:  # noqa: BLE001 - scheduler setup is fail-open.
                    self._worker = None
                    self._scheduler = None
                    if scheduler is not None:
                        try:
                            await scheduler.close()
                        except Exception:  # noqa: BLE001 - cleanup is best effort.
                            logger.warning(
                                "AstrContinuum fail-open code=%s stage=%s",
                                "COMPACTION_SCHEDULER_CLOSE_FAILED",
                                "COMPACTION_SCHEDULER",
                            )
                    logger.warning(
                        "AstrContinuum fail-open code=%s stage=%s",
                        "COMPACTION_SCHEDULER_START_FAILED",
                        "COMPACTION_SCHEDULER",
                    )
            logger.info("AstrContinuum initialized")

    async def terminate(self) -> None:
        """Release request composition state idempotently."""

        scheduler: CoalescingCompactionScheduler | None = None
        async with self._initialize_lock:
            scheduler = self._scheduler
            self._scheduler = None
            self._worker = None
            self._bridge = None
            self._capability = None
            self._initialized = False
        if scheduler is not None:
            try:
                await scheduler.close()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - termination remains fail-open.
                logger.warning(
                    "AstrContinuum fail-open code=%s stage=%s",
                    "COMPACTION_SCHEDULER_CLOSE_FAILED",
                    "COMPACTION_SCHEDULER",
                )

    async def _wake_compaction_worker(
        self,
        worker: CompactionWorker,
        _session_key_hash: str,
    ) -> None:
        try:
            await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - callback errors must not kill the scheduler.
            logger.warning(
                "AstrContinuum fail-open code=%s stage=%s",
                "COMPACTION_WORKER_RUN_FAILED",
                "COMPACTION_WORKER",
            )

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
            opaque_cost = estimate_opaque_token_cost(opaque_objects, self._counter)
            fixed_cost = estimate_opaque_token_cost(guard.system_objects, self._counter)
            assembly = bridge.assemble_prepared(
                state.prepared,
                opaque_token_cost=opaque_cost,
                fixed_required_cost=fixed_cost,
            )
            if not assembly.projected_text:
                return
            built = build_projection_objects(
                assembly.projected_text,
                capability,
            )
            if built.fault is not None:
                self._record_fault(state, built.fault)
                return
            if not built.objects:
                return
            state.projected = project(messages, guard, built.objects)
        except AstrBotAdapterError as error:
            self._record_fault(state, error.fault)
        except ProjectionInvariantError as error:
            self._record_fault(state, _projection_fault(error))
        except Exception:  # noqa: BLE001 - optional enhancement fails open
            self._record_fault(
                state,
                _generic_fault(
                    "PROJECTION_FAILED",
                    "PROJECT",
                    capability_available=capability.available,
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
            except AstrBotAdapterError as error:
                self._record_fault(state, error.fault)
                return
            except Exception:  # noqa: BLE001 - persistence details stay private
                self._record_fault(
                    state,
                    _generic_fault("ASSISTANT_CAPTURE_FAILED", "ASSISTANT_CAPTURE"),
                )
                return
        if not state.intent_raised:
            try:
                job = await bridge.raise_compaction_intent(
                    state.prepared,
                    target_high_water_mark=state.assistant_event.sequence,
                )
                state.intent_raised = True
                scheduler = self._scheduler
                if job is not None and scheduler is not None:
                    try:
                        await scheduler.notify(job.session_key.session_key_hash)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - durable intent must not roll back.
                        self._record_fault(
                            state,
                            _generic_fault(
                                "COMPACTION_SCHEDULER_NOTIFY_FAILED",
                                "COMPACTION_SCHEDULER",
                            ),
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

        del event, response

    @filter.command("context_status")
    async def context_status(self, event: AstrMessageEvent):
        if self._bridge is None:
            status = "not ready"
        elif self._worker is None or self._scheduler is None:
            status = "ready; background compaction unavailable"
        else:
            status = "ready; background compaction active"
        yield event.plain_result(f"AstrContinuum is {status}.")
