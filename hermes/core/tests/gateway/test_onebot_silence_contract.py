"""Offline async regressions for the OneBot output and exit-state contracts."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SendResult
from gateway.platforms.base import ProcessingOutcome
from gateway.run import _normalize_gateway_response
from gateway.session import SessionSource, build_session_key
from plugins.platforms.onebot.adapter import (
    GroupTurnCompletion,
    OneBotAdapter,
)
from plugins.platforms.onebot.group_executor import AgentOutcome, GroupExecutor
from plugins.platforms.onebot.group_state import BufferedMessage, GroupStateRegistry
from plugins.platforms.onebot.semantic_judge import (
    _build_pre_reply_judge_prompt,
    _sanitize_recorder_state,
    _validate_judge_v2,
)
from plugins.platforms.onebot.trigger_coordinator import TriggerCoordinator, TriggerRequest


@pytest.fixture
def anyio_backend():
    """Run asyncio-native OneBot contract tests on the production backend."""
    return "asyncio"


def _onebot_adapter():
    return OneBotAdapter(PlatformConfig(enabled=True, extra={}))


def _event(group_id="42", text="hello"):
    source = SessionSource(
        platform=Platform("onebot"),
        chat_id=f"group:{group_id}",
        user_id="100",
        user_name="member",
        chat_type="group",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"group_id": group_id, "user_id": "100", "message": text},
        message_id="m-1",
    )


@pytest.mark.anyio
async def test_marker_normalization_sends_only_body_and_exposes_clean_completion():
    adapter = _onebot_adapter()
    sent = []

    async def fake_impl(chat_id, content, reply_to=None, metadata=None):
        sent.append(content)
        return SendResult(success=True, message_id="out-1")

    adapter._send_message_impl = fake_impl
    future = asyncio.get_running_loop().create_future()
    adapter._group_send_results["42"] = future

    result = await adapter.send("group:42", "  你好 [sIlEnT]  [quiet]  ")

    assert result.success is True
    assert sent == ["你好"]
    completion = await future
    assert isinstance(completion, GroupTurnCompletion)
    assert completion.normalized_text == "你好"
    assert completion.delivery_text == "你好"
    assert completion.marker_names == ("SILENT", "QUIET")
    assert "[SILENT]" not in completion.normalized_text
    assert "[QUIET]" not in completion.normalized_text


@pytest.mark.anyio
async def test_pure_marker_in_executor_context_does_not_quiet_before_retry():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    sent = AsyncMock(return_value=SendResult(success=True, message_id="out-1"))
    adapter._send_message_impl = sent
    future = asyncio.get_running_loop().create_future()
    adapter._register_group_turn("42", "turn-1", future)

    result = await adapter.send("group:42", " \n [QUIET] \t")

    assert result.success is True
    sent.assert_not_awaited()
    assert state.attentive.active is True
    assert not future.done()
    adapter._unregister_group_turn("42", "turn-1")


@pytest.mark.anyio
async def test_empty_final_response_hook_completes_without_send():
    adapter = _onebot_adapter()
    event = _event()
    future = asyncio.get_running_loop().create_future()
    adapter._register_group_turn("42", "turn-empty", future)
    event._onebot_group_turn_nonce = "turn-empty"

    await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)

    completion = await future
    assert isinstance(completion, GroupTurnCompletion)
    assert completion.completed is True
    assert completion.delivery_text == ""
    adapter._unregister_group_turn("42", "turn-empty")


@pytest.mark.anyio
async def test_pure_marker_final_completion_is_nonce_scoped_and_deferred():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    event = _event(text="hello")
    future = asyncio.get_running_loop().create_future()
    adapter._register_group_turn("42", "turn-marker", future)
    event._onebot_group_turn_nonce = "turn-marker"

    result = await adapter._send_final_with_delivery_ledger(
        event=event,
        session_key="onebot:group:42",
        content=" [qUiEt] ",
        reply_to=None,
        metadata=None,
    )

    assert result.success is True
    assert state.attentive.active is True
    completion = await future
    assert completion.completed is True
    assert completion.delivery_text == ""
    adapter._unregister_group_turn("42", "turn-marker")


@pytest.mark.anyio
async def test_failed_mixed_marker_delivery_does_not_quiet_group():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()

    async def failed_impl(*_args, **_kwargs):
        return SendResult(success=False, error="offline", retryable=True)

    adapter._send_message_impl = failed_impl
    result = await adapter.send("group:42", "告别 [QUIET]")

    assert result.success is False
    assert state.attentive.active is True


def test_marker_parser_handles_code_and_reference_boundaries():
    body, markers = OneBotAdapter._normalize_control_markers(
        "引用 [QUIET] 以及代码：`[silent]`\n正文"
    )
    assert markers == ("QUIET", "SILENT")
    assert "[QUIET]" not in body
    assert "[silent]" not in body
    assert "正文" in body


@pytest.mark.anyio
async def test_group_prompt_only_offers_quiet_marker_for_exit_mode():
    adapter = _onebot_adapter()
    adapter._recall_context = lambda *_args, **_kwargs: ""
    executor = GroupExecutor(adapter)
    state = adapter._group_states.get("42")
    raw = {
        "group_id": "42",
        "user_id": "100",
        "message": "hello",
        "sender": {"nickname": "member"},
    }

    normal = await executor._build_channel_prompt(
        "42", TriggerRequest("42", 1, "mention", "direct @", raw_msg=raw),
        (), state.snapshot_meta(), "member",
    )
    exit_prompt = await executor._build_channel_prompt(
        "42", TriggerRequest("42", 1, "exit", "leave", raw_msg=raw),
        (), state.snapshot_meta(), "member",
    )

    assert "沉默不是可选项" in normal
    assert "[SILENT]" not in normal
    assert "[QUIET]" not in normal
    assert "[QUIET]" in exit_prompt
    assert "不能只有标记没有话" in exit_prompt


def test_suppressed_agent_retry_does_not_persist_synthetic_turn():
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent._suppress_session_persistence = True
    agent._session_messages = []
    agent.save_trajectories = True
    messages = [
        {"role": "user", "content": "[internal output contract retry]"},
        {"role": "assistant", "content": "visible"},
    ]

    agent._persist_session(messages, conversation_history=[])
    agent._save_session_log(messages)
    agent._save_trajectory(messages, "retry", True)

    assert agent._session_messages == messages


def test_contract_retry_empty_sentinel_stays_invisible_but_normal_path_keeps_notice():
    assert _normalize_gateway_response("(empty)", contract_retry=True) == ""
    assert _normalize_gateway_response("(empty)", contract_required=True) == ""
    assert "returned no response" in _normalize_gateway_response(
        "(empty)", contract_retry=False
    )


@pytest.mark.anyio
async def test_group_executor_retries_empty_completion_once_after_old_guard_releases():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    calls = []
    owner_tasks = []

    async def fake_handle(event):
        calls.append(event)
        nonce = event._onebot_group_turn_nonce
        future = adapter._group_send_results["42"][nonce]
        if len(calls) == 1:
            session_key = build_session_key(event.source)
            guard = asyncio.Event()
            adapter._active_sessions[session_key] = guard

            async def old_owner():
                future.set_result(
                    GroupTurnCompletion(completed=True, normalized_text="")
                )
                await asyncio.sleep(0.02)
                adapter._session_tasks.pop(session_key, None)
                adapter._active_sessions.pop(session_key, None)

            owner = asyncio.create_task(old_owner())
            owner_tasks.append(owner)
            adapter._session_tasks[session_key] = owner
        else:
            future.set_result(
                GroupTurnCompletion(
                    completed=True,
                    normalized_text="visible reply",
                    delivery_text="visible reply",
                    delivery_succeeded=True,
                )
            )

    adapter.handle_message = fake_handle
    executor = GroupExecutor(adapter)
    executor._record_episode_state = AsyncMock()
    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
            "time": 1,
        },
        is_mention=True,
    )

    await executor._run_turn(request)
    await asyncio.gather(*owner_tasks)

    assert len(calls) == 2
    assert getattr(calls[1], "_onebot_contract_retry", False) is True
    assert getattr(calls[0], "_onebot_contract_required", False) is True
    assert calls[1].text == calls[0].text
    assert calls[1].raw_message is calls[0].raw_message
    assert state.reply_count == 1
    assert state.last_reply[1] == "visible reply"
    executor._record_episode_state.assert_awaited_once()
    recorded = executor._record_episode_state.await_args.args[2]
    assert recorded.reply_text == "visible reply"
    assert "[QUIET]" not in recorded.reply_text


@pytest.mark.anyio
async def test_real_base_adapter_empty_handler_retries_without_network_or_duplicate_user():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    responses = [None, "visible reply"]
    calls = []

    async def fake_handler(event):
        calls.append(event)
        return responses.pop(0)

    async def fake_impl(chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="out-1")

    async def quiet_typing(*_args, **_kwargs):
        await asyncio.sleep(3600)

    adapter.set_message_handler(fake_handler)
    adapter._send_message_impl = fake_impl
    adapter._keep_typing = quiet_typing
    adapter.stop_typing = AsyncMock()

    executor = GroupExecutor(adapter)
    executor._record_episode_state = AsyncMock()
    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
        },
        is_mention=True,
    )

    await executor._run_turn(request)

    assert len(calls) == 2
    assert getattr(calls[1], "_onebot_contract_retry", False) is True
    assert state.reply_count == 1
    assert state.last_reply[1] == "visible reply"
    executor._record_episode_state.assert_awaited_once()


@pytest.mark.anyio
async def test_real_pending_user_followup_wins_over_contract_retry():
    """A real Base guard/pending handoff must never be replaced by feedback."""
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    calls = []
    responses = [None, "follow-up response"]

    async def fake_handler(event):
        calls.append(event)
        if len(calls) == 1:
            await adapter.handle_message(
                _event(group_id="42", text="follow-up")
            )
        return responses.pop(0)

    async def fake_impl(chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="out-follow-up")

    async def quiet_typing(*_args, **_kwargs):
        await asyncio.sleep(3600)

    adapter.set_message_handler(fake_handler)
    adapter._send_message_impl = fake_impl
    adapter._keep_typing = quiet_typing
    adapter.stop_typing = AsyncMock()
    executor = GroupExecutor(adapter)
    executor._record_episode_state = AsyncMock()

    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
        },
        is_mention=True,
    )

    await executor._run_turn(request)
    for _ in range(200):
        if not adapter._active_sessions:
            break
        await asyncio.sleep(0.01)
    await adapter.cancel_background_tasks()

    assert len(calls) == 2
    assert getattr(calls[1], "_onebot_contract_retry", False) is False
    assert calls[1].text == "follow-up"
    assert state.reply_count == 0
    executor._record_episode_state.assert_not_awaited()


@pytest.mark.anyio
async def test_pending_followup_marks_empty_completion_interrupted_before_retry():
    """A pending real user message must prevent a synthetic contract retry."""
    adapter = _onebot_adapter()
    event = _event(group_id="42")
    future = asyncio.get_running_loop().create_future()
    adapter._register_group_turn("42", "turn-1", future)
    event._onebot_group_turn_nonce = "turn-1"

    session_key = build_session_key(event.source)
    adapter._pending_messages[session_key] = _event(group_id="42", text="follow-up")
    await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)

    completion = await future
    assert completion.completed is True
    assert completion.interrupted is True
    adapter._unregister_group_turn("42", "turn-1")


@pytest.mark.anyio
async def test_newer_group_message_suppresses_contract_retry():
    """A newer group watermark must prevent a synthetic retry."""
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    calls = []

    async def fake_run(event):
        calls.append(event)
        state.append_message(
            BufferedMessage(mid="m-2", ts=2.0, uid="101", name="follow-up", text="next")
        )
        return AgentOutcome(kind="silent", completed=True)

    adapter._update_rolling_summary = AsyncMock()
    executor = GroupExecutor(adapter)
    executor._run_agent_locked = fake_run
    executor._record_episode_state = AsyncMock()
    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
            "time": 1,
        },
        is_mention=True,
    )

    await executor._run_turn(request)

    assert len(calls) == 1
    executor._record_episode_state.assert_not_awaited()


@pytest.mark.anyio
async def test_real_base_marker_body_reaches_send_outcome_recorder_and_buffer():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    calls = []

    async def fake_handler(event):
        calls.append(event)
        return "visible body [sIlEnT]"

    adapter.set_message_handler(fake_handler)
    adapter._ws = object()
    adapter._http_client = object()
    adapter._send_text_with_retry = AsyncMock(
        return_value=SendResult(success=True, message_id="out-1")
    )
    adapter._keep_typing = AsyncMock()
    adapter.stop_typing = AsyncMock()
    executor = GroupExecutor(adapter)
    executor._record_episode_state = AsyncMock()

    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
        },
        is_mention=True,
    )

    await executor._run_turn(request)
    await adapter.cancel_background_tasks()

    assert len(calls) == 1
    assert adapter._send_text_with_retry.await_args.args[1] == "visible body"
    assert state.reply_count == 1
    buffered = [m.text for m in state.get_recent() if m.is_bot]
    assert buffered[-1] == "visible body"
    recorded = executor._record_episode_state.await_args.args[2]
    assert recorded.reply_text == "visible body"
    assert "[SILENT]" not in recorded.reply_text


@pytest.mark.anyio
async def test_group_executor_gives_up_after_one_empty_retry_without_reply_record():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    calls = []

    async def fake_handle(event):
        calls.append(event)
        nonce = event._onebot_group_turn_nonce
        adapter._group_send_results["42"][nonce].set_result(
            GroupTurnCompletion(completed=True, normalized_text="")
        )

    adapter.handle_message = fake_handle
    executor = GroupExecutor(adapter)
    executor._record_episode_state = AsyncMock()
    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
        },
        is_mention=True,
    )

    await executor._run_turn(request)

    assert len(calls) == 2
    assert state.reply_count == 0
    assert state.attentive.silent_count == 1
    executor._record_episode_state.assert_not_awaited()


@pytest.mark.anyio
async def test_delivery_failure_does_not_trigger_model_contract_retry():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    state.enter_attentive()
    state.append_message(
        BufferedMessage(mid="m-1", ts=1.0, uid="100", name="member", text="hello")
    )
    executor = GroupExecutor(adapter)
    calls = []

    async def failed_delivery(_event):
        calls.append(True)
        return AgentOutcome(
            kind="silent",
            completed=True,
            delivery_attempted=True,
            delivery_succeeded=False,
        )

    executor._run_agent_locked = failed_delivery
    executor._record_episode_state = AsyncMock()
    request = TriggerRequest(
        group_id="42",
        origin_seq=1,
        mode="mention",
        decision_reason="direct mention",
        raw_msg={
            "group_id": "42",
            "user_id": "100",
            "message_id": "m-1",
            "message": "hello",
            "sender": {"nickname": "member"},
        },
        is_mention=True,
    )

    await executor._run_turn(request)

    assert len(calls) == 1
    executor._record_episode_state.assert_not_awaited()


def test_recorder_downgrades_soft_close_and_accepts_explicit_departure():
    prior = {"episode_phase": "mid", "exiting_streak": 0, "turn_count": 1}

    soft = _sanitize_recorder_state(
        {"episode_phase": "exiting", "continuity": "same_episode"},
        prior,
        bot_reply="你们聊，我先看看。",
    )
    explicit = _sanitize_recorder_state(
        {"episode_phase": "exiting", "continuity": "same_episode"},
        prior,
        bot_reply="那我先走了，拜拜。",
    )

    assert soft["episode_phase"] == "winding_down"
    assert soft["exiting_streak"] == 0
    assert explicit["episode_phase"] == "exiting"
    assert explicit["exiting_streak"] == 1


def test_judge_exit_gate_requires_two_consecutive_observations():
    raw = {
        "should_reply": False,
        "should_end": False,
        "should_exit": True,
        "exit_farewell": False,
        "episode_phase": "exiting",
    }
    first = _validate_judge_v2(dict(raw), episode_state={"exiting_streak": 0})
    second = _validate_judge_v2(dict(raw), episode_state={"exiting_streak": 1})
    reset = _validate_judge_v2(
        {**raw, "episode_phase": "mid"},
        episode_state={"exiting_streak": 2},
    )

    assert first["exiting_streak"] == 1
    assert first["should_exit"] is False
    assert second["exiting_streak"] == 2
    assert second["should_exit"] is True
    assert reset["exiting_streak"] == 0
    assert reset["should_exit"] is False


def test_judge_prompt_exposes_exiting_streak_to_the_model():
    prompt = _build_pre_reply_judge_prompt(
        "test",
        "对话态",
        [],
        {"text": "继续", "name": "member"},
        episode_state={"turn_count": 2, "episode_phase": "exiting", "exiting_streak": 1},
        bot_name="Soyo",
    )

    assert "exiting 连续轮数: 1" in prompt


def test_coordinator_exit_gate_is_deterministic_for_unvalidated_results():
    adapter = _onebot_adapter()
    state = adapter._group_states.get("42")
    raw = {
        "should_exit": True,
        "exit_farewell": True,
        "episode_phase": "exiting",
    }

    first = TriggerCoordinator._enforce_exit_gate(
        state, {}, dict(raw)
    )
    state.episode_state.episode_phase = first["episode_phase"]
    state.episode_state.exiting_streak = first["exiting_streak"]
    second = TriggerCoordinator._enforce_exit_gate(
        state, {}, dict(raw)
    )
    directed = TriggerCoordinator._enforce_exit_gate(
        state, {"_reply_to_bot": True}, dict(raw)
    )

    assert first["exiting_streak"] == 1
    assert first["should_exit"] is False
    assert second["exiting_streak"] == 2
    assert second["should_exit"] is True
    assert directed["episode_phase"] == "mid"
    assert directed["exiting_streak"] == 0
    assert directed["should_exit"] is False


def test_direct_at_name_and_reply_reset_exiting_state(monkeypatch):
    adapter = _onebot_adapter()
    coordinator = TriggerCoordinator(adapter)
    state = adapter._group_states.get("42")
    state.episode_state.episode_phase = "exiting"
    state.episode_state.exiting_streak = 2

    coordinator._reset_exiting_state(state, "42", "direct @")
    assert state.episode_state.episode_phase == "mid"
    assert state.episode_state.exiting_streak == 0


@pytest.mark.anyio
async def test_trigger_ingest_resets_direct_at_alias_and_reply_to_bot(monkeypatch):
    adapter = _onebot_adapter()
    coordinator = TriggerCoordinator(adapter)
    coordinator._submit_request = MagicMock()
    coordinator._schedule_judge = MagicMock()
    state = adapter._group_states.get("42")
    monkeypatch.setattr(
        "plugins.platforms.onebot.semantic_judge._bot_aliases",
        lambda: ["Soyo"],
    )

    state.episode_state.episode_phase = "exiting"
    state.episode_state.exiting_streak = 2
    await coordinator.on_ingested(
        "42", 1, {"_is_mentioned": True}, "member", "@Soyo hello"
    )
    assert state.episode_state.episode_phase == "mid"
    assert state.episode_state.exiting_streak == 0

    state.episode_state.episode_phase = "exiting"
    state.episode_state.exiting_streak = 2
    await coordinator.on_ingested("42", 2, {}, "member", "Soyo hello")
    assert state.episode_state.episode_phase == "mid"
    assert state.episode_state.exiting_streak == 0

    state.episode_state.episode_phase = "exiting"
    state.episode_state.exiting_streak = 2
    adapter._self_id = 999
    monkeypatch.setattr(adapter, "_get_reply_message_id", lambda _msg: 1)
    monkeypatch.setattr(
        coordinator, "_resolve_reply", lambda _msg: ("Soyo", "999")
    )
    await coordinator.on_ingested(
        "42", 3, {"message": [{"type": "reply", "data": {"id": "1"}}]},
        "member", "reply",
    )
    assert state.episode_state.episode_phase == "mid"
    assert state.episode_state.exiting_streak == 0

    state.episode_state.episode_phase = "exiting"
    state.episode_state.exiting_streak = 2
    monkeypatch.setattr(
        "plugins.platforms.onebot.semantic_judge._bot_aliases",
        lambda: ["Soyo"],
    )
    # The name branch uses the same reset helper; exercise it directly with
    # the production branch's inputs so no network or database is touched.
    coordinator._reset_exiting_state(state, "42", "bot alias")
    assert state.episode_state.episode_phase == "mid"
    assert state.episode_state.exiting_streak == 0

    state.episode_state.episode_phase = "exiting"
    state.episode_state.exiting_streak = 2
    monkeypatch.setattr(coordinator, "_resolve_reply", lambda _msg: ("Soyo", "999"))
    adapter._self_id = 999
    assert coordinator._reply_targets_bot({"message": [{"type": "reply", "data": {"id": "1"}}]}) is True
    coordinator._reset_exiting_state(state, "42", "reply-to-bot")
    assert state.episode_state.episode_phase == "mid"
    assert state.episode_state.exiting_streak == 0
