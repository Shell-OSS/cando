# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Tests for src/cando/model_factory.py.

Only exercises the pure profile-resolution logic. `resolve_chat_model` /
`resolve_agent_model` are tested with `init_chat_model` mocked out so no
real model client or network call is ever made.
"""

from unittest.mock import patch

import pytest

from cando import model_factory
from cando.model_factory import (
    INFERENCE_PROFILE_ENV_VAR,
    active_profile,
    resolve_agent_model,
    resolve_chat_model,
)


def test_active_profile_defaults_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INFERENCE_PROFILE_ENV_VAR, raising=False)
    assert active_profile() == "max"


@pytest.mark.parametrize("profile", ["no_thinking", "fast", "max"])
def test_active_profile_reads_env_var(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setenv(INFERENCE_PROFILE_ENV_VAR, profile)
    assert active_profile() == profile


def test_active_profile_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(INFERENCE_PROFILE_ENV_VAR, "bogus")
    with pytest.raises(ValueError, match="Unknown"):
        active_profile()


def test_resolve_agent_model_passthrough_under_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under "max", resolve_agent_model must return the model id
    unchanged and must NOT call init_chat_model."""
    monkeypatch.setenv(INFERENCE_PROFILE_ENV_VAR, "max")
    with patch.object(model_factory, "init_chat_model") as mock_init:
        result = resolve_agent_model("generator", "azure_openai:gpt-5.2")

    assert result == "azure_openai:gpt-5.2"
    mock_init.assert_not_called()


def test_resolve_agent_model_initializes_under_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(INFERENCE_PROFILE_ENV_VAR, "fast")
    with patch.object(model_factory, "init_chat_model") as mock_init:
        mock_init.return_value = "sentinel-model"
        result = resolve_agent_model("generator", "openai:some-model")

    mock_init.assert_called_once()
    _, kwargs = mock_init.call_args
    assert kwargs["model"] == "openai:some-model"
    assert (
        kwargs["max_tokens"]
        == model_factory.FAST_ROLE_CONFIGS["generator"].max_tokens
    )
    assert result == "sentinel-model"


def test_resolve_chat_model_under_max_omits_token_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under "max", resolve_chat_model behaves like a plain
    init_chat_model(model_id, max_retries=...) call: no max_tokens /
    extra_body override is sent."""
    monkeypatch.setenv(INFERENCE_PROFILE_ENV_VAR, "max")
    with patch.object(model_factory, "init_chat_model") as mock_init:
        mock_init.return_value = "sentinel-model"
        resolve_chat_model(
            "generator_zero", "azure_openai:gpt-5.2", max_retries=3
        )

    mock_init.assert_called_once_with(
        model="azure_openai:gpt-5.2", max_retries=3
    )


def test_resolve_chat_model_under_no_thinking_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(INFERENCE_PROFILE_ENV_VAR, "no_thinking")
    with patch.object(model_factory, "init_chat_model") as mock_init:
        mock_init.return_value = "sentinel-model"
        resolve_chat_model("historian", "azure_openai:gpt-5.2")

    _, kwargs = mock_init.call_args
    assert (
        kwargs["extra_body"]["chat_template_kwargs"]["enable_thinking"]
        is False
    )
