"""Custom / local provider profile.

Covers any endpoint registered as provider="custom", including local
Ollama / vLLM / llama.cpp instances and self-hosted gateways.

Reasoning ("thinking") dialects — different inference backends express the
same intent with different API shapes. The dialect is chosen via
config.yaml ``model.thinking_dialect`` (explicit) or auto-detected from the
model name / base_url when unset:

  qwen    DashScope/Qwen style:
            on  -> extra_body.thinking={"type":"enabled"}
                   + top-level reasoning_effort (minimal..xhigh)
            off -> extra_body.thinking={"type":"disabled"}
  ollama  Ollama style:
            off -> extra_body.think=False
            on  -> no extra params (server default)
  openai  OpenAI-compatible standard (vLLM/OpenAI-style gateways):
            top-level reasoning_effort only; nothing in extra_body
  kimi    Kimi/Moonshot style: extra_body.thinking={"type":enabled/disabled}
  none    never emit thinking params (endpoint default behaviour)

Auto-detect heuristic (when thinking_dialect is unset/empty):
  model name contains "qwen"                     -> qwen
  base_url contains "11434" or "ollama"          -> ollama
  anything else                                  -> none (safest)
"""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

# effort levels accepted by upstream hermes_constants.VALID_REASONING_EFFORTS
_EFFORT_MAP = {"minimal": "low", "xhigh": "high"}  # providers accepting a narrower range


def _detect_dialect(model: str | None, base_url: str | None) -> str:
    """Best-effort dialect guess. Returns "" when nothing matches (-> none)."""
    m = (model or "").lower()
    u = (base_url or "").lower()
    if "qwen" in m:
        return "qwen"
    if "ollama" in u or ":11434" in u:
        return "ollama"
    return ""


def _config_thinking_dialect() -> str:
    """Read model.thinking_dialect from $HERMES_HOME/config.yaml.

    The transport doesn't pass this key, so the profile reads it directly
    (same pattern as semantic_judge._load_main_model_cfg). Empty on failure.
    """
    try:
        import os as _os
        import yaml as _y
        cfg_path = _os.path.join(
            _os.getenv("HERMES_HOME", _os.path.expanduser("~/.hermes")),
            "config.yaml",
        )
        if not _os.path.exists(cfg_path):
            return ""
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _y.safe_load(f) or {}
        m = cfg.get("model", {}) or {}
        return str(m.get("thinking_dialect", "") or "").strip().lower()
    except Exception:
        return ""


class CustomProfile(ProviderProfile):
    """Custom/local endpoints — dialect-table driven thinking control."""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        thinking_dialect: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        ollama_num_ctx: int | None = None,
        **ctx: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}

        # Ollama context window
        if ollama_num_ctx:
            options = extra_body.get("options", {})
            options["num_ctx"] = ollama_num_ctx
            extra_body["options"] = options

        # Resolve dialect: explicit config > auto-detect > none
        # (transport doesn't pass thinking_dialect, so read it from config.yaml here)
        dialect = (thinking_dialect or "").strip().lower()
        if not dialect:
            dialect = _config_thinking_dialect()
        if not dialect:
            dialect = _detect_dialect(model, base_url)

        if not reasoning_config or not isinstance(reasoning_config, dict):
            return extra_body, top_level

        enabled = reasoning_config.get("enabled", True)
        effort = (reasoning_config.get("effort") or "").strip().lower()
        wants_off = (effort == "none") or (enabled is False)

        if dialect == "qwen":
            if wants_off:
                extra_body["thinking"] = {"type": "disabled"}
            else:
                extra_body["thinking"] = {"type": "enabled"}
                top_level["reasoning_effort"] = _EFFORT_MAP.get(effort, effort) if effort else "medium"
        elif dialect == "kimi":
            if wants_off:
                extra_body["thinking"] = {"type": "disabled"}
            else:
                extra_body["thinking"] = {"type": "enabled"}
                top_level.setdefault("reasoning_effort", effort or "medium")
        elif dialect == "ollama":
            if wants_off:
                extra_body["think"] = False
            # on -> no params; ollama thinks by default when the model supports it
        elif dialect == "openai":
            if not wants_off and effort:
                top_level["reasoning_effort"] = effort
            # off -> simply omit reasoning_effort
        # dialect == "" (none): never emit thinking params

        return extra_body, top_level

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Custom/Ollama: base_url is user-configured; fetch if set."""
        if not self.base_url:
            return None
        return super().fetch_models(api_key=api_key, timeout=timeout)


custom = CustomProfile(
    name="custom",
    aliases=(
        "ollama",
        "local",
        "vllm",
        "llamacpp",
        "llama.cpp",
        "llama-cpp",
        "openai-compat",
        "generic",
    ),
    env_vars=(),  # No fixed key — custom endpoint
    base_url="",  # User-configured
)

register_provider(custom)
