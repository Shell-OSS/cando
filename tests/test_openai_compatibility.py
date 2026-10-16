# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Offline compatibility checks for LangChain and the OpenAI SDK."""

from unittest.mock import Mock, patch

import pytest
from openai.types.chat import ChatCompletion

from cando.agents.verifiers.model_verifier import ModelVerifierOutput
from cando.model_factory import resolve_chat_model


@pytest.fixture(params=["openai:gpt-4o", "azure_openai:gpt-4o"])
def model_id(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/"
    )
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-10-21")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    return request.param


@pytest.mark.parametrize("profile", ["max", "fast", "no_thinking"])
def test_chat_completion_compatibility(
    model_id: str, profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CANDO_INFERENCE_PROFILE", profile)
    model = resolve_chat_model("generator_zero", model_id)
    completion = ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
        }
    )

    raw_response = Mock(headers={}, parse=Mock(return_value=completion))
    with patch.object(
        model.client.with_raw_response, "create", return_value=raw_response
    ) as call:
        response = model.invoke("Generate a layout.")

    assert response.content == "ok"
    assert call.call_args.kwargs["messages"] == [
        {"role": "user", "content": "Generate a layout."}
    ]
    if profile != "max":
        assert call.call_args.kwargs["extra_body"]["chat_template_kwargs"][
            "enable_thinking"
        ] is (profile == "fast")


@pytest.mark.parametrize("profile", ["max", "fast", "no_thinking"])
def test_structured_verifier_output_compatibility(
    model_id: str, profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CANDO_INFERENCE_PROFILE", profile)
    model = resolve_chat_model("model_verifier", model_id)
    expected = ModelVerifierOutput.model_validate(
        {
            category: {"compliant": True, "score": 1.0, "feedback": "ok"}
            for category in ModelVerifierOutput.model_fields
        }
    )
    completion = ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-test",
                                "type": "function",
                                "function": {
                                    "name": "ModelVerifierOutput",
                                    "arguments": expected.model_dump_json(),
                                },
                            }
                        ],
                    },
                }
            ],
        }
    )
    structured_model = model.with_structured_output(
        ModelVerifierOutput, method="function_calling"
    )

    raw_response = Mock(headers={}, parse=Mock(return_value=completion))
    with patch.object(
        model.client.with_raw_response, "create", return_value=raw_response
    ) as call:
        output = structured_model.invoke("Verify the layout.")

    assert ModelVerifierOutput.model_validate(output) == expected
    assert call.call_args.kwargs["tools"][0]["function"]["name"] == (
        "ModelVerifierOutput"
    )
