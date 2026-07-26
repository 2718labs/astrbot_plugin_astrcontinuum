"""AstrContinuum AstrBot plugin composition root."""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass, field
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
        AstrBotExtractiveCompilerBackend,
        CompactionWorker,
        CompactionWorkerConfig,
        SessionProviderRegistry,
    )
    from astrcontinuum.domain import EventEnvelope
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
        AstrBotExtractiveCompilerBackend,
        CompactionWorker,
        CompactionWorkerConfig,
        SessionProviderRegistry,
    )
    from .astrcontinuum.domain import EventEnvelope
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
        SQLiteConnectionFactory,
        SQLiteMigrator,
        SQLiteRepository,
    )

_PLUGIN_NAME = "astrbot_plugin_astrcontinuum"
_REQUEST_STATE_KEY = "astrcontinuum.v1.request-state"


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
    "2718labs",
    "Non-blocking infinite context runtime for AstrBot",
    "0.1.0",
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
            bridge = AstrBotHookBridge(
                repository,
                budget_config=self._budget_config(),
                counter=self._counter,
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
            worker = CompactionWorker(
                repository=repository,
                backend=backend,
                counter=self._counter,
                worker_id=f"astrbot-{uuid.uuid4().hex}",
                config=self._worker_config(),
            )
            await worker.start()
            self._bridge = bridge
            self._providers = providers
            self._worker = worker
            self._capability = probe_projection_capability()
            self._initialized = True
            logger.info("AstrContinuum initialized")

    async def terminate(self) -> None:
        """Release request composition state idempotently."""

        async with self._initialize_lock:
            worker = self._worker
            self._worker = None
            if worker is not None:
                await worker.close()
            self._bridge = None
            self._capability = None
            self._providers = None
            self._initialized = False

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
            assembly = bridge.assemble_prepared(
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

        del response
        state = self._state(event)
        if state is not None:
            self._restore_request_conversation(state)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("context_status")
    async def context_status(self, event: AstrMessageEvent):
        if not self._enabled:
            yield event.plain_result(
                "AstrContinuum：已停用\n"
                "后台归约：已停止\n"
                "提示：插件不会改写 AstrBot 保存的对话历史。"
            )
            return

        bridge = self._bridge
        if bridge is None:
            yield event.plain_result(
                "AstrContinuum：尚未就绪\n"
                "后台归约：未启动\n"
                "提示：请检查 AstrBot 日志中的 AstrContinuum 初始化记录。"
            )
            return

        worker = self._worker
        worker_task = worker.task if worker is not None else None
        worker_status = "运行中" if worker_task is not None and not worker_task.done() else "已停止"

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

        try:
            event_count, checkpoint_count, pending_count = await asyncio.to_thread(read_counts)
        except Exception:  # noqa: BLE001 - never expose storage details to chat
            logger.error(
                "AstrContinuum status query failed code=%s",
                "STATUS_QUERY_FAILED",
            )
            yield event.plain_result(
                f"AstrContinuum：运行中\n后台归约：{worker_status}\n统计信息：暂时无法读取"
            )
            return

        yield event.plain_result(
            "AstrContinuum：运行中\n"
            f"后台归约：{worker_status}\n"
            f"已记录事件：{event_count}\n"
            f"已发布 Checkpoint：{checkpoint_count}\n"
            f"待处理任务：{pending_count}"
        )
