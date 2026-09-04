"""Unit tests for deterministic empty-response retry protection."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from agent import empty_response_guard as guard


def _agent(**overrides):
    values = {
        "model": "anthropic/claude-sonnet-4-6",
        "provider": "nous",
        "api_mode": "chat_completions",
        "base_url": None,
        "api_key": None,
        "_empty_content_retries": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _response(*, prompt_tokens=25_900, completion_tokens=0, usage_present=True,
              reasoning_tokens=0):
    if not usage_present:
        return SimpleNamespace(usage=None)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    if reasoning_tokens:
        usage.output_tokens_details = SimpleNamespace(
            reasoning_tokens=reasoning_tokens
        )
    return SimpleNamespace(usage=usage)


def _record_streak(agent, responses, finish_reasons=None):
    reasons = finish_reasons or ["stop"] * len(responses)
    for response, reason in zip(responses, reasons):
        guard.record_empty_attempt(
            agent, finish_reason=reason, response=response
        )
        agent._empty_content_retries += 1


class TestDeterministicEmpty:
    def test_two_zero_output_attempts_same_signature_are_deterministic(self):
        agent = _agent()
        _record_streak(agent, [_response(), _response()])

        assert guard.deterministic_empty(agent) is True

    def test_single_attempt_is_not_deterministic(self):
        agent = _agent()
        _record_streak(agent, [_response()])

        assert guard.deterministic_empty(agent) is False

    def test_missing_usage_fails_open(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(usage_present=False), _response(usage_present=False)],
        )

        assert guard.deterministic_empty(agent) is False

    def test_mixed_usage_presence_fails_open(self):
        agent = _agent()
        _record_streak(agent, [_response(), _response(usage_present=False)])

        assert guard.deterministic_empty(agent) is False

    def test_generated_tokens_fail_open(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(completion_tokens=0), _response(completion_tokens=7)],
        )

        assert guard.deterministic_empty(agent) is False

    def test_reasoning_tokens_count_as_generation(self):
        agent = _agent()
        _record_streak(
            agent,
            [
                _response(reasoning_tokens=128),
                _response(reasoning_tokens=128),
            ],
        )

        assert guard.deterministic_empty(agent) is False

    def test_model_change_fails_open(self):
        agent = _agent()
        guard.record_empty_attempt(
            agent, finish_reason="stop", response=_response()
        )
        agent._empty_content_retries += 1
        agent.model = "other/model"
        guard.record_empty_attempt(
            agent, finish_reason="stop", response=_response()
        )

        assert guard.deterministic_empty(agent) is False

    def test_finish_reason_change_fails_open(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(), _response()],
            finish_reasons=["stop", "length"],
        )

        assert guard.deterministic_empty(agent) is False

    def test_retry_counter_reset_starts_a_new_streak(self):
        agent = _agent()
        _record_streak(agent, [_response(), _response()])
        assert guard.deterministic_empty(agent) is True

        agent._empty_content_retries = 0
        guard.record_empty_attempt(
            agent, finish_reason="stop", response=_response()
        )

        assert guard.deterministic_empty(agent) is False

    def test_disabled_guard_fails_open(self):
        agent = _agent(_empty_guard_enabled=False)
        _record_streak(agent, [_response(), _response()])

        assert guard.deterministic_empty(agent) is False


class TestEmptyRetryBudget:
    def test_unknown_cost_keeps_three_retries(self, monkeypatch):
        monkeypatch.setattr(guard, "_estimate_attempt_cost", lambda *_: None)

        assert guard.empty_retry_budget(_agent(), _response()) == 3

    def test_cost_at_threshold_reduces_budget_to_one(self, monkeypatch):
        monkeypatch.setattr(
            guard, "_estimate_attempt_cost", lambda *_: Decimal("0.80")
        )

        assert guard.empty_retry_budget(_agent(), _response()) == 1

    def test_cost_below_threshold_keeps_three_retries(self, monkeypatch):
        monkeypatch.setattr(
            guard, "_estimate_attempt_cost", lambda *_: Decimal("0.01")
        )

        assert guard.empty_retry_budget(_agent(), _response()) == 3

    def test_custom_threshold_is_respected(self, monkeypatch):
        monkeypatch.setattr(
            guard, "_estimate_attempt_cost", lambda *_: Decimal("0.80")
        )

        assert guard.empty_retry_budget(
            _agent(_empty_guard_cost_threshold_usd=Decimal("5.00")),
            _response(),
        ) == 3

    def test_disabled_guard_keeps_three_retries(self, monkeypatch):
        monkeypatch.setattr(
            guard, "_estimate_attempt_cost", lambda *_: Decimal("9.99")
        )

        assert guard.empty_retry_budget(
            _agent(_empty_guard_enabled=False), _response()
        ) == 3

    def test_pricing_failure_keeps_three_retries(self):
        response = SimpleNamespace(usage=object())

        assert guard.empty_retry_budget(
            _agent(model=None, provider=None), response
        ) == 3


class TestStreakCost:
    def test_known_cost_accumulates(self, monkeypatch):
        costs = iter([Decimal("1.10"), Decimal("1.23")])
        monkeypatch.setattr(
            guard, "_estimate_attempt_cost", lambda *_: next(costs)
        )
        agent = _agent()
        _record_streak(agent, [_response(), _response()])

        assert guard.streak_cost_usd(agent) == Decimal("2.33")

    def test_unknown_cost_returns_none(self, monkeypatch):
        monkeypatch.setattr(guard, "_estimate_attempt_cost", lambda *_: None)
        agent = _agent()
        _record_streak(agent, [_response(), _response()])

        assert guard.streak_cost_usd(agent) is None

    def test_new_streak_resets_cost(self, monkeypatch):
        monkeypatch.setattr(
            guard, "_estimate_attempt_cost", lambda *_: Decimal("1.00")
        )
        agent = _agent()
        _record_streak(agent, [_response(), _response()])
        agent._empty_content_retries = 0
        guard.record_empty_attempt(
            agent, finish_reason="stop", response=_response()
        )

        assert guard.streak_cost_usd(agent) == Decimal("1.00")


class TestZeroOutputExtraction:
    def test_openai_zero_completion_is_evidence(self):
        present, zero = guard._zero_output(_agent(), _response())

        assert (present, zero) == (True, True)

    def test_openai_completion_is_not_zero_output(self):
        present, zero = guard._zero_output(
            _agent(), _response(completion_tokens=9)
        )

        assert (present, zero) == (True, False)

    def test_missing_usage_is_not_evidence(self):
        present, zero = guard._zero_output(
            _agent(), _response(usage_present=False)
        )

        assert (present, zero) == (False, False)

    def test_anthropic_zero_output_is_evidence(self):
        usage = SimpleNamespace(input_tokens=25_900, output_tokens=0)
        present, zero = guard._zero_output(
            _agent(api_mode="anthropic_messages"), SimpleNamespace(usage=usage)
        )

        assert (present, zero) == (True, True)

    def test_all_zero_usage_object_fails_open(self):
        usage = SimpleNamespace()
        present, zero = guard._zero_output(
            _agent(), SimpleNamespace(usage=usage)
        )

        assert (present, zero) == (False, False)


class TestResolveGuardSettings:
    def test_missing_section_uses_defaults(self):
        assert guard.resolve_guard_settings(None) == (
            guard.DEFAULT_GUARD_ENABLED,
            guard.DEFAULT_COST_THRESHOLD_USD,
        )

    def test_disabled_and_string_booleans_are_supported(self):
        assert guard.resolve_guard_settings({"enabled": False})[0] is False
        assert guard.resolve_guard_settings({"enabled": "false"})[0] is False
        assert guard.resolve_guard_settings({"enabled": "true"})[0] is True

    def test_custom_threshold_is_decimal(self):
        _, threshold = guard.resolve_guard_settings({"cost_threshold_usd": "1.50"})

        assert threshold == Decimal("1.50")

    def test_bad_threshold_uses_default(self):
        for value in ("banana", -1, True):
            _, threshold = guard.resolve_guard_settings(
                {"cost_threshold_usd": value}
            )
            assert threshold == guard.DEFAULT_COST_THRESHOLD_USD

    def test_shipped_default_config_matches_guard_defaults(self):
        from hermes_cli.config import DEFAULT_CONFIG

        assert guard.resolve_guard_settings(
            DEFAULT_CONFIG["agent"]["empty_response_guard"]
        ) == (
            guard.DEFAULT_GUARD_ENABLED,
            guard.DEFAULT_COST_THRESHOLD_USD,
        )
