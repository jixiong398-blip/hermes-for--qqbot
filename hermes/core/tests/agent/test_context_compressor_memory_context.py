"""Tests for passing provider checkpoint context into the local compressor."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.context_compressor import ContextCompressor


def _make_compressor() -> ContextCompressor:
    with patch("agent.context_compressor.get_model_context_length", return_value=80_000):
        return ContextCompressor(
            model="test-model",
            threshold_percent=0.5,
            protect_first_n=3,
            protect_last_n=3,
            quiet_mode=True,
        )


def test_provider_checkpoint_context_is_fenced_in_summary_prompt():
    compressor = _make_compressor()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
    )

    with patch("agent.context_compressor.call_llm", return_value=response) as call_llm:
        result = compressor._generate_summary(
            [{"role": "user", "content": "old decision"}],
            memory_context="provider checkpoint evidence",
        )

    assert result
    prompt = call_llm.call_args.kwargs["messages"][0]["content"]
    assert "MEMORY CHECKPOINT EVIDENCE (REFERENCE ONLY)" in prompt
    assert "provider checkpoint evidence" in prompt
    assert "BEGIN MEMORY CHECKPOINT" in prompt
    assert "END MEMORY CHECKPOINT" in prompt


def test_provider_checkpoint_context_is_bounded_before_summary_call():
    compressor = _make_compressor()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
    )

    with patch("agent.context_compressor.call_llm", return_value=response) as call_llm:
        compressor._generate_summary(
            [{"role": "user", "content": "old decision"}],
            memory_context="x" * 20_000,
        )

    prompt = call_llm.call_args.kwargs["messages"][0]["content"]
    assert "x" * 8_000 in prompt
    assert "x" * 8_001 not in prompt


def test_provider_checkpoint_context_is_redacted_at_llm_boundary():
    compressor = _make_compressor()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
    )
    secret = "sk-test-secret-value-123456"

    with patch("agent.context_compressor.call_llm", return_value=response) as call_llm:
        compressor._generate_summary(
            [{"role": "user", "content": "old decision"}],
            memory_context=f"OPENAI_API_KEY={secret}",
        )

    prompt = call_llm.call_args.kwargs["messages"][0]["content"]
    assert secret not in prompt
    assert "OPENAI_API_KEY=" in prompt


def test_provider_checkpoint_context_survives_aux_model_fallback():
    """A main-model retry must receive the same checkpoint evidence."""
    compressor = _make_compressor()
    compressor.summary_model = "aux-model"
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "summary from main model"
    aux_error = Exception("404 model_not_found: aux-model")
    aux_error.status_code = 404

    with patch(
        "agent.context_compressor.call_llm",
        side_effect=[aux_error, response],
    ) as call_llm:
        result = compressor._generate_summary(
            [{"role": "user", "content": "old decision"}],
            memory_context="durable provider evidence",
        )

    assert result
    assert call_llm.call_count == 2
    retry_prompt = call_llm.call_args_list[1].kwargs["messages"][0]["content"]
    assert "durable provider evidence" in retry_prompt
    assert "BEGIN MEMORY CHECKPOINT" in retry_prompt
