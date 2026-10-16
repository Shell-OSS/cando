# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Model factory providing inference-profile-aware model resolution.

Centralizes model instantiation so that a single environment variable
(CANDO_INFERENCE_PROFILE) can switch every agent between three inference
profiles without touching each agent's initialization code individually:

- "no_thinking": thinking disabled for every role; max_tokens covers only
  the final structured answer.
- "fast": thinking enabled for roles that benefit from it, with both a
  reasoning-token sub-cap (thinking_token_budget) and an overall
  max_tokens cap.
- "max": thinking enabled (provider default) and max_tokens uncapped for
  every role -- no extra_body/max_tokens override is sent at all, so a
  role can consume as much of the model's context window as the provider
  allows. This is the historical default behavior (formerly named
  "current"), preserved unchanged: models are passed through unmodified.

"max" is the default profile, so importing this module never changes
existing behavior unless a run explicitly opts into "fast" or
"no_thinking". The legacy value "current" is still accepted as an alias
for "max".
"""

import os
from dataclasses import dataclass, replace
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

INFERENCE_PROFILE_ENV_VAR = "CANDO_INFERENCE_PROFILE"
VALID_PROFILES = ("no_thinking", "fast", "max")


@dataclass(frozen=True)
class RoleConfig:
    thinking: bool
    max_tokens: int
    thinking_token_budget: int | None = None


FAST_ROLE_CONFIGS: dict[str, RoleConfig] = {
    "historian": RoleConfig(thinking=False, max_tokens=1_536),
    "planner_worker": RoleConfig(
        thinking=True, max_tokens=8_192, thinking_token_budget=4_096
    ),
    "planner_supervisor": RoleConfig(
        thinking=True, max_tokens=12_288, thinking_token_budget=6_144
    ),
    "generator": RoleConfig(
        thinking=True, max_tokens=8_192, thinking_token_budget=3_072
    ),
    "generator_direct": RoleConfig(
        thinking=True, max_tokens=8_192, thinking_token_budget=3_072
    ),
    "generator_zero": RoleConfig(
        thinking=True, max_tokens=16_384, thinking_token_budget=6_144
    ),
    "model_verifier": RoleConfig(
        thinking=True, max_tokens=16_384, thinking_token_budget=8_192
    ),
}


NO_THINKING_ROLE_CONFIGS: dict[str, RoleConfig] = {
    role: replace(
        cfg,
        thinking=False,
        max_tokens=cfg.max_tokens - (cfg.thinking_token_budget or 0),
        thinking_token_budget=None,
    )
    for role, cfg in FAST_ROLE_CONFIGS.items()
}

_PROFILE_ROLE_CONFIGS: dict[str, dict[str, RoleConfig]] = {
    "no_thinking": NO_THINKING_ROLE_CONFIGS,
    "fast": FAST_ROLE_CONFIGS,
}


def active_profile() -> str:
    """Returns the active inference profile name ("no_thinking", "fast", or
    "max").

    Reads CANDO_INFERENCE_PROFILE from the environment. Defaults to "max"
    (today's unmodified pass-through behavior) when unset, so importing
    this module never changes existing behavior unless a run explicitly
    opts into "fast" or "no_thinking". The legacy value "current" is
    accepted as an alias for "max".
    """
    value = os.environ.get(INFERENCE_PROFILE_ENV_VAR, "max")
    if value not in VALID_PROFILES:
        raise ValueError(
            f"Unknown {INFERENCE_PROFILE_ENV_VAR}={value!r}; expected one "
            f"of {VALID_PROFILES}"
        )
    return value


def _role_config(role: str) -> RoleConfig | None:
    """Returns the RoleConfig for `role` under the active profile, or None
    under "max" (meaning: no override, pass the model through unchanged)."""
    role_configs = _PROFILE_ROLE_CONFIGS.get(active_profile())
    return None if role_configs is None else role_configs[role]


def _qwen_extra_body(
    enable_thinking: bool,
    thinking_token_budget: int | None = None,
) -> dict:
    """Builds the `extra_body` payload for a Qwen3.6 vLLM/SGLang
    OpenAI-compatible endpoint.

    Isolated here so that if the current provider requires a different
    parameter shape, only this function needs to change.

    Args:
        enable_thinking (bool): Whether to enable the model's thinking mode.
        thinking_token_budget (int | None): Optional cap on reasoning-only
            tokens, applied independently of `max_tokens` (which bounds
            reasoning + final answer combined). Sent as a top-level
            `thinking_token_budget` sampling param, matching vLLM's
            "Thinking Budget Control" feature. Omitted entirely when None,
            so passing None is a no-op identical to today's behavior.

    Returns:
        dict: The `extra_body` payload.
    """
    extra_body: dict[str, Any] = {
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking,
            "preserve_thinking": False,
        }
    }
    if thinking_token_budget is not None:
        extra_body["thinking_token_budget"] = thinking_token_budget
    return extra_body


def resolve_agent_model(role: str, model_id: str) -> str | BaseChatModel:
    """Resolves the model argument passed to `langchain.agents.create_agent`.

    Under the "max" profile, returns the model identifier unchanged so
    `create_agent` initializes exactly as before. Under "fast" or
    "no_thinking", returns an initialized chat model configured with the
    role's max output tokens and thinking-mode setting.

    Args:
        role (str): Key into FAST_ROLE_CONFIGS/NO_THINKING_ROLE_CONFIGS
            (e.g. "generator", "generator_direct").
        model_id (str): The model identifier (e.g. ModelsConfig.generator).

    Returns:
        The model identifier (str) under "max", or an initialized chat
        model instance under "fast"/"no_thinking".
    """
    cfg = _role_config(role)
    if cfg is None:
        return model_id

    return init_chat_model(
        model=model_id,
        max_tokens=cfg.max_tokens,
        extra_body=_qwen_extra_body(cfg.thinking, cfg.thinking_token_budget),
    )


def resolve_chat_model(
    role: str,
    model_id: str,
    *,
    max_retries: int | None = None,
) -> BaseChatModel:
    """Resolves a directly-initialized chat model (bypassing create_agent).

    Under the "max" profile, behaves exactly like a plain
    `init_chat_model(model_id, max_retries=...)` call. Under "fast" or
    "no_thinking", additionally sets the role's max output tokens and
    thinking-mode `extra_body`.

    Args:
        role (str): Key into FAST_ROLE_CONFIGS/NO_THINKING_ROLE_CONFIGS
            (e.g. "historian", "generator_zero").
        model_id (str): The model identifier.
        max_retries (int | None): Optional retry budget forwarded to
            `init_chat_model`.

    Returns:
        The initialized chat model.
    """
    kwargs: dict = {}

    if max_retries is not None:
        kwargs["max_retries"] = max_retries

    cfg = _role_config(role)
    if cfg is not None:
        kwargs["max_tokens"] = cfg.max_tokens
        kwargs["extra_body"] = _qwen_extra_body(
            cfg.thinking, cfg.thinking_token_budget
        )

    return init_chat_model(
        model=model_id,
        **kwargs,
    )
