"""Optional v26 message-sidecar writer/loader contract tests."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock, patch

from hermes_state import SessionDB
from run_agent import AIAgent


_SIDECAR_COLUMNS = (
    "messages.api_content",
    "messages.display_kind",
    "messages.display_metadata",
)


def _sidecar_db(tmp_path):
    target = tmp_path / "sidecar.db"
    seed = SessionDB(target)
    seed.create_session("sidecar-session", source="onebot")
    seed.close()

    backup = tmp_path / "sidecar-backup.db"
    shutil.copyfile(target, backup)
    before = target.read_bytes()
    report = SessionDB.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=__import__("hashlib").sha256(before).hexdigest(),
        columns=_SIDECAR_COLUMNS,
    )
    assert report.status == "committed"
    return target


def test_v11_sidecar_arguments_are_a_noop(tmp_path):
    path = tmp_path / "v11.db"
    db = SessionDB(path)
    try:
        db.create_session("v11-session", source="onebot")
        db.append_message(
            "v11-session",
            role="user",
            content="clean",
            timestamp=42.0,
            api_content="provider-only",
            display_kind="user",
            display_metadata={"visible": False},
        )
        row = db.get_messages_as_conversation("v11-session")[0]
        assert row == {"role": "user", "content": "clean"}
    finally:
        db.close()


def test_v26_sidecar_writer_and_loader_round_trip(tmp_path):
    db = SessionDB(_sidecar_db(tmp_path))
    try:
        db.append_message(
            "sidecar-session",
            role="user",
            content="clean transcript",
            timestamp=123.5,
            api_content="wire content",
            display_kind="user_message",
            display_metadata={"message_id": "m1", "visible": True},
        )
        row = db.get_messages_as_conversation("sidecar-session")[0]
        assert row["content"] == "clean transcript"
        assert row["timestamp"] == 123.5
        assert row["api_content"] == "wire content"
        assert row["display_kind"] == "user_message"
        assert row["display_metadata"] == {"message_id": "m1", "visible": True}

        raw = db.get_messages("sidecar-session")[0]
        assert raw["timestamp"] == 123.5
        assert raw["api_content"] == "wire content"
    finally:
        db.close()


def test_v26_sidecar_fields_are_bounded_and_role_scoped(tmp_path):
    db = SessionDB(_sidecar_db(tmp_path))
    try:
        db.append_message(
            "sidecar-session",
            role="tool",
            content="tool result",
            api_content="must not persist on tool rows",
            display_kind="x" * 500,
            display_metadata=["not", "an", "object"],
        )
        db.append_message(
            "sidecar-session",
            role="assistant",
            content="answer",
            api_content="a" * 600_000,
            display_kind="assistant",
            display_metadata='{"ok": true}',
        )
        rows = db.get_messages_as_conversation("sidecar-session")
        tool_row, assistant_row = rows
        assert "api_content" not in tool_row
        assert tool_row["display_kind"] == "x" * 120
        assert "display_metadata" not in tool_row
        assert len(assistant_row["api_content"]) == 500_000
        assert assistant_row["display_metadata"] == {"ok": True}
    finally:
        db.close()


def test_v26_replace_messages_preserves_sidecars(tmp_path):
    db = SessionDB(_sidecar_db(tmp_path))
    try:
        db.replace_messages(
            "sidecar-session",
            [
                {
                    "role": "user",
                    "content": "rewritten",
                    "timestamp": 10.0,
                    "api_content": "rewritten wire",
                    "display_kind": "rewritten_user",
                    "display_metadata": {"source": "test"},
                },
                {"role": "assistant", "content": "reply"},
            ],
        )
        rows = db.get_messages_as_conversation("sidecar-session")
        assert rows[0]["timestamp"] == 10.0
        assert rows[0]["api_content"] == "rewritten wire"
        assert rows[0]["display_kind"] == "rewritten_user"
        assert rows[0]["display_metadata"] == {"source": "test"}
    finally:
        db.close()


def test_v26_agent_flush_passes_sidecars_only_when_columns_exist(tmp_path):
    db = SessionDB(_sidecar_db(tmp_path))
    try:
        holder = type("AgentHolder", (), {})()
        holder._session_db = db
        holder.session_id = "sidecar-session"
        holder._session_db_created = True
        holder._last_flushed_db_idx = 0
        holder._apply_persist_user_message_override = lambda _messages: None
        holder._session_message_metadata_columns = lambda: set(
            db.message_metadata_columns()
        )
        messages = [
            {
                "role": "user",
                "content": "clean",
                "timestamp": 123.5,
                "api_content": "wire",
                "display_kind": "user",
                "display_metadata": {"source": "flush"},
            }
        ]

        AIAgent._flush_messages_to_session_db(holder, messages, [])

        row = db.get_messages_as_conversation("sidecar-session")[0]
        assert row["timestamp"] == 123.5
        assert row["api_content"] == "wire"
        assert row["display_kind"] == "user"
        assert row["display_metadata"] == {"source": "flush"}
    finally:
        db.close()


def test_gated_agent_stamps_exact_api_sidecar_without_leaking_metadata(tmp_path, monkeypatch):
    db = SessionDB(_sidecar_db(tmp_path))
    try:
        with (
            patch("run_agent.get_tool_definitions", return_value=[]),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
        ):
            agent = AIAgent(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                session_db=db,
            )
        agent.session_id = "sidecar-session"
        agent.client = MagicMock()
        agent.client.chat.completions.create.return_value = type(
            "Response", (), {
                "choices": [type(
                    "Choice", (), {
                        "message": type(
                            "Message", (), {
                                "content": "answer",
                                "tool_calls": None,
                                "reasoning": None,
                                "reasoning_content": None,
                                "reasoning_details": None,
                            }
                        )(),
                        "finish_reason": "stop",
                    }
                )()],
                "model": "test/model",
                "usage": None,
            }
        )()
        agent._cached_system_prompt = "stable system"
        agent.compression_enabled = False

        class Memory:
            def on_turn_start(self, *_args, **_kwargs):
                return None

            def prefetch_all(self, *_args, **_kwargs):
                return "remembered"

            def describe_recall(self):
                return ""

        agent._memory_manager = Memory()
        monkeypatch.setattr(
            "agent.memory_manager.build_memory_context_block",
            lambda value: f"<memory>{value}</memory>",
        )

        result = agent.run_conversation("Tell me about the remembered topic")

        sent = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_payload = next(
            row for row in sent if row.get("role") == "user"
        )
        assert user_payload["content"] == (
            "Tell me about the remembered topic\n\n<memory>remembered</memory>"
        )
        assert "api_content" not in user_payload
        assert "timestamp" not in user_payload
        assert "display_kind" not in user_payload
        assert "display_metadata" not in user_payload

        rows = db.get_messages_as_conversation("sidecar-session")
        user_row = next(row for row in rows if row.get("role") == "user")
        assert user_row["content"] == "Tell me about the remembered topic"
        assert user_row["api_content"] == (
            "Tell me about the remembered topic\n\n<memory>remembered</memory>"
        )
        assert isinstance(user_row.get("timestamp"), float)
        assert result["final_response"] == "answer"
    finally:
        db.close()


def test_gated_sidecar_lineage_replay_keeps_timestamp_and_sidecar_alignment(tmp_path):
    db = SessionDB(_sidecar_db(tmp_path))
    try:
        db.create_session(
            "sidecar-child",
            source="onebot",
            parent_session_id="sidecar-session",
        )
        db.append_message(
            "sidecar-session",
            role="user",
            content="parent clean",
            timestamp=100.0,
            api_content="parent wire",
            display_kind="parent",
            display_metadata={"generation": 0},
        )
        db.append_message(
            "sidecar-child",
            role="user",
            content="child clean",
            timestamp=200.0,
            api_content="child wire",
            display_kind="child",
            display_metadata={"generation": 1},
        )

        rows = db.get_messages_as_conversation(
            "sidecar-child", include_ancestors=True
        )
        assert [row["content"] for row in rows] == [
            "parent clean",
            "child clean",
        ]
        assert [row["timestamp"] for row in rows] == [100.0, 200.0]
        assert [row["api_content"] for row in rows] == [
            "parent wire",
            "child wire",
        ]
        assert [row["display_kind"] for row in rows] == ["parent", "child"]

        db.replace_messages(
            "sidecar-child",
            [
                {
                    "role": "user",
                    "content": "child rewritten",
                    "timestamp": 300.0,
                    "api_content": "child rewritten wire",
                    "display_kind": "child-rewritten",
                    "display_metadata": {"generation": 2},
                }
            ],
        )
        rows = db.get_messages_as_conversation(
            "sidecar-child", include_ancestors=True
        )
        assert rows[-1]["content"] == "child rewritten"
        assert rows[-1]["timestamp"] == 300.0
        assert rows[-1]["api_content"] == "child rewritten wire"
    finally:
        db.close()
