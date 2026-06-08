"""Tests for NVIDIA NIM OpenAI-compatible provider support."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from tradingagents.llm_clients.capabilities import get_capabilities
from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.llm_clients.openai_client import OpenAIClient


def test_nvidia_catalog_includes_required_models():
    models = {
        value
        for mode in ("quick", "deep")
        for _, value in get_model_options("nvidia", mode)
    }
    assert "google/gemma-4-31b-it" in models
    assert "minimaxai/minimax-m2.7" in models
    assert "qwen/qwen3-coder-480b-a35b-instruct" in models
    assert "google/gemma-3n-e4b-it" in models
    assert "custom" in models


def test_nvidia_client_uses_nim_base_url_and_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    llm = OpenAIClient(
        model="minimaxai/minimax-m2.7",
        provider="nvidia",
    ).get_llm()

    assert "integrate.api.nvidia.com/v1" in str(llm.openai_api_base)
    assert llm.openai_api_key.get_secret_value() == "nvapi-test"


def test_nvidia_applies_model_card_sampling_defaults(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    llm = OpenAIClient(
        model="google/gemma-3n-e4b-it",
        provider="nvidia",
    ).get_llm()

    payload = llm._get_request_payload([HumanMessage(content="hello")])

    assert payload["model"] == "google/gemma-3n-e4b-it"
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.7
    assert payload["frequency_penalty"] == 0.0
    assert payload["presence_penalty"] == 0.0
    assert payload["max_tokens"] == 512
    assert "max_completion_tokens" not in payload


def test_nvidia_applies_gemma_4_sampling_defaults(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    llm = OpenAIClient(
        model="google/gemma-4-31b-it",
        provider="nvidia",
    ).get_llm()

    payload = llm._get_request_payload([HumanMessage(content="hello")])

    assert payload["model"] == "google/gemma-4-31b-it"
    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 0.95
    assert payload["max_tokens"] == 16384
    assert payload["extra_body"] == {
        "top_k": 64,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert "max_completion_tokens" not in payload


def test_nvidia_defaults_are_overridable(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    llm = OpenAIClient(
        model="qwen/qwen3-coder-480b-a35b-instruct",
        provider="nvidia",
        temperature=0.1,
        max_completion_tokens=64,
        extra_body={"top_k": 20},
    ).get_llm()

    payload = llm._get_request_payload([HumanMessage(content="hello")])

    assert payload["temperature"] == 0.1
    assert payload["top_p"] == 0.8
    assert payload["max_tokens"] == 64
    assert payload["extra_body"] == {"top_k": 20}


def test_nvidia_minimax_suppresses_tool_choice_for_direct_tool_binding(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    llm = OpenAIClient(
        model="minimaxai/minimax-m2.7",
        provider="nvidia",
    ).get_llm()

    bound = llm.bind_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "lookup_price",
                    "description": "Look up a stock price.",
                    "parameters": {
                        "type": "object",
                        "properties": {"symbol": {"type": "string"}},
                        "required": ["symbol"],
                    },
                },
            }
        ]
    )

    assert get_capabilities("minimaxai/minimax-m2.7").supports_tool_choice is False
    assert "tools" not in bound.kwargs
    assert bound.kwargs.get("tool_choice") is None or "tool_choice" not in bound.kwargs


def test_nvidia_minimax_suppresses_tool_choice_for_structured_output(monkeypatch):
    class Pick(BaseModel):
        action: str

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    llm = OpenAIClient(
        model="minimaxai/minimax-m2.7",
        provider="nvidia",
    ).get_llm()
    bound = llm.with_structured_output(Pick)
    first = bound.steps[0] if hasattr(bound, "steps") else bound

    assert "tools" not in first.kwargs
    assert first.kwargs["response_format"] == {"type": "json_object"}
    assert first.kwargs.get("tool_choice") is None or "tool_choice" not in first.kwargs
