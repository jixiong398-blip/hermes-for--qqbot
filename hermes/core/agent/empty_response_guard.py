"""Guards for deterministic empty completions and expensive retries.

An empty completion currently retries the full conversation up to three times.
That is useful for transient provider failures, but it can repeatedly bill a
large prompt when the provider deterministically returns zero output.  This
module records only the current consecutive empty streak and supplies two
conservative decisions for the existing agent loop:

* two identical, usage-confirmed zero-output completions may skip remaining
  retries;
* a known expensive attempt may reduce the retry budget from three to one.

Missing usage, unknown pricing, generated reasoning/output tokens, and route
changes all fail open so the legacy retry behavior remains available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_EMPTY_RETRY_BUDGET = 3
REDUCED_EMPTY_RETRY_BUDGET = 1
DEFAULT_COST_THRESHOLD_USD = Decimal("0.25")
DEFAULT_GUARD_ENABLED = True

_ATTEMPTS_ATTR = "_empty_attempt_history"
_STREAK_COST_ATTR = "_empty_streak_cost_usd"
_ENABLED_ATTR = "_empty_guard_enabled"
_THRESHOLD_ATTR = "_empty_guard_cost_threshold_usd"


@dataclass(frozen=True)
class EmptyAttempt:
    """One observed empty completion in the current streak."""

    model: str
    provider: str
    finish_reason: str
    usage_present: bool
    zero_output: bool

    @property
    def signature(self) -> tuple:
        return (self.model, self.provider, self.finish_reason)


def resolve_guard_settings(section: Any) -> Tuple[bool, Decimal]:
    """Resolve the ``agent.empty_response_guard`` config subsection.

    Malformed input is tolerated because a guard configuration error must not
    prevent the agent from starting.  Values are resolved once by
    ``AIAgent.__init__`` and are not read from disk in the hot loop.
    """
    if not isinstance(section, dict):
        return (DEFAULT_GUARD_ENABLED, DEFAULT_COST_THRESHOLD_USD)

    enabled_raw = section.get("enabled", DEFAULT_GUARD_ENABLED)
    if isinstance(enabled_raw, bool):
        enabled = enabled_raw
    elif isinstance(enabled_raw, str):
        enabled = enabled_raw.strip().lower() not in ("0", "false", "no", "off")
    else:
        enabled = DEFAULT_GUARD_ENABLED

    threshold = DEFAULT_COST_THRESHOLD_USD
    threshold_raw = section.get("cost_threshold_usd")
    if threshold_raw is not None and not isinstance(threshold_raw, bool):
        try:
            candidate = Decimal(str(threshold_raw))
            if candidate > 0:
                threshold = candidate
        except Exception:  # noqa: BLE001 - malformed config must not break init
            logger.debug(
                "empty-response guard: invalid cost_threshold_usd %r; using default",
                threshold_raw,
            )
    return (enabled, threshold)


def guard_enabled(agent: Any) -> bool:
    """Return whether the guard is enabled for ``agent``."""
    value = getattr(agent, _ENABLED_ATTR, DEFAULT_GUARD_ENABLED)
    return value if isinstance(value, bool) else DEFAULT_GUARD_ENABLED


def _cost_threshold_usd(agent: Any) -> Decimal:
    value = getattr(agent, _THRESHOLD_ATTR, None)
    if isinstance(value, Decimal) and value > 0:
        return value
    return DEFAULT_COST_THRESHOLD_USD


def _attempts(agent: Any) -> List[EmptyAttempt]:
    attempts = getattr(agent, _ATTEMPTS_ATTR, None)
    if attempts is None:
        attempts = []
        setattr(agent, _ATTEMPTS_ATTR, attempts)
    return attempts


def _estimate_attempt_cost(agent: Any, response: Any) -> Optional[Decimal]:
    """Best-effort USD estimate for one attempt, or ``None`` if unknown."""
    raw_usage = getattr(response, "usage", None)
    if not raw_usage:
        return None
    try:
        from agent.usage_pricing import estimate_usage_cost, normalize_usage

        canonical = normalize_usage(
            raw_usage,
            provider=getattr(agent, "provider", None),
            api_mode=getattr(agent, "api_mode", None),
        )
        result = estimate_usage_cost(
            getattr(agent, "model", "") or "",
            canonical,
            provider=getattr(agent, "provider", None),
            base_url=getattr(agent, "base_url", None),
            api_key=getattr(agent, "api_key", None),
        )
    except Exception:  # noqa: BLE001 - pricing must never break the loop
        logger.debug("empty-response guard: cost estimation failed", exc_info=True)
        return None
    return getattr(result, "amount_usd", None)


def _zero_output(agent: Any, response: Any) -> tuple:
    """Return ``(usage_present, zero_output)`` while failing open."""
    raw_usage = getattr(response, "usage", None)
    if not raw_usage:
        return (False, False)
    try:
        from agent.usage_pricing import normalize_usage

        canonical = normalize_usage(
            raw_usage,
            provider=getattr(agent, "provider", None),
            api_mode=getattr(agent, "api_mode", None),
        )
    except Exception:  # noqa: BLE001 - malformed usage must not affect retries
        logger.debug("empty-response guard: usage normalization failed", exc_info=True)
        return (False, False)

    output = getattr(canonical, "output_tokens", None)
    if output is None:
        return (False, False)
    # A usage object with no input tokens is not evidence of a real completion;
    # some proxies emit an object whose fields all default to zero.
    if getattr(canonical, "prompt_tokens", 0) <= 0:
        return (False, False)

    # Reasoning tokens count as generation.  The thinking-prefill path owns
    # reasoning-only responses and must retain its existing retry behavior.
    reasoning = getattr(canonical, "reasoning_tokens", 0) or 0
    return (True, (output + reasoning) == 0)


def record_empty_attempt(agent: Any, *, finish_reason: str, response: Any) -> None:
    """Record one empty completion before incrementing its retry counter."""
    attempts = _attempts(agent)
    if getattr(agent, "_empty_content_retries", 0) == 0:
        attempts.clear()
        setattr(agent, _STREAK_COST_ATTR, Decimal("0"))

    usage_present, zero_output = _zero_output(agent, response)
    attempts.append(
        EmptyAttempt(
            model=str(getattr(agent, "model", "") or ""),
            provider=str(getattr(agent, "provider", "") or ""),
            finish_reason=str(finish_reason or ""),
            usage_present=usage_present,
            zero_output=zero_output,
        )
    )

    cost = _estimate_attempt_cost(agent, response)
    if cost is not None and cost > 0:
        prior = getattr(agent, _STREAK_COST_ATTR, Decimal("0")) or Decimal("0")
        setattr(agent, _STREAK_COST_ATTR, prior + cost)


def deterministic_empty(agent: Any) -> bool:
    """Return whether the current streak is a deterministic empty response."""
    if not guard_enabled(agent):
        return False
    attempts = getattr(agent, _ATTEMPTS_ATTR, None) or []
    if len(attempts) < 2:
        return False
    first = attempts[0]
    return all(
        attempt.usage_present
        and attempt.zero_output
        and attempt.signature == first.signature
        for attempt in attempts
    )


def empty_retry_budget(agent: Any, response: Any) -> int:
    """Return 3, or 1 when one known attempt meets the cost threshold."""
    if not guard_enabled(agent):
        return DEFAULT_EMPTY_RETRY_BUDGET
    cost = _estimate_attempt_cost(agent, response)
    if cost is None:
        return DEFAULT_EMPTY_RETRY_BUDGET
    if cost >= _cost_threshold_usd(agent):
        return REDUCED_EMPTY_RETRY_BUDGET
    return DEFAULT_EMPTY_RETRY_BUDGET


def streak_cost_usd(agent: Any) -> Optional[Decimal]:
    """Return the accumulated known cost for the current streak."""
    cost = getattr(agent, _STREAK_COST_ATTR, None)
    if cost is None or cost <= 0:
        return None
    return cost
