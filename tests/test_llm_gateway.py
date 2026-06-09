import json

import pytest

from app.gateways.llm import (
    DeepSeekChatCompletionsLLMGateway,
    LLMGatewayError,
    OpenAIResponsesLLMGateway,
    to_openai_strict_json_schema,
)
from app.schemas.common import CouncilMode
from app.schemas.context import ContextPack


class FakeHttpResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.posts = []

    def post(self, url, headers, json):
        self.posts.append((url, headers, json))
        return self.response


def make_context_pack() -> ContextPack:
    return ContextPack(
        thread_id="2c035461-6b47-4b92-a982-7b7eac099c36",
        agent_name="StrategyDraftAgent",
        task_brief="岗位校准",
        node_goal="输出策略",
        council_mode=CouncilMode.lite,
        mode_reason="常规任务",
    )


def test_openai_responses_gateway_uses_structured_output_without_raw_state() -> None:
    client = FakeHttpClient(
        FakeHttpResponse(
            {
                "output_text": json.dumps(
                    {"summary": "ok", "artifact_payload": {"answer": "done"}},
                    ensure_ascii=False,
                )
            }
        )
    )
    gateway = OpenAIResponsesLLMGateway(
        api_key="sk-test",
        model="gpt-test",
        client=client,
    )

    result = gateway.generate_structured(
        agent_name="StrategyDraftAgent",
        context_pack=make_context_pack(),
        output_schema={"type": "object"},
        schema_name="agent_output",
        max_output_tokens=800,
    )

    assert result["summary"] == "ok"
    url, headers, body = client.posts[0]
    assert url == "https://api.openai.com/v1/responses"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    user_payload = json.loads(body["input"][1]["content"])
    assert "context_pack" in user_payload
    assert "recruitment_state" not in body["input"][1]["content"]
    assert "node_history" not in body["input"][1]["content"]


def test_openai_responses_gateway_rejects_invalid_json_text() -> None:
    gateway = OpenAIResponsesLLMGateway(
        api_key="sk-test",
        model="gpt-test",
        client=FakeHttpClient(FakeHttpResponse({"output_text": "not-json"})),
    )

    with pytest.raises(LLMGatewayError):
        gateway.generate_structured(
            agent_name="StrategyDraftAgent",
            context_pack=make_context_pack(),
            output_schema={"type": "object"},
            schema_name="agent_output",
            max_output_tokens=800,
        )


def test_openai_responses_gateway_rejects_custom_base_url_resolving_private_ip() -> None:
    client = FakeHttpClient(FakeHttpResponse({"output_text": "{}"}))
    gateway = OpenAIResponsesLLMGateway(
        api_key="sk-test",
        model="gpt-test",
        base_url="https://models.example.com",
        client=client,
        base_url_resolver=lambda hostname: ["10.0.0.5"],
    )

    with pytest.raises(LLMGatewayError, match="Unsafe OpenAI base_url"):
        gateway.generate_structured(
            agent_name="StrategyDraftAgent",
            context_pack=make_context_pack(),
            output_schema={"type": "object"},
            schema_name="agent_output",
            max_output_tokens=800,
        )

    assert client.posts == []


def test_openai_responses_gateway_wraps_invalid_resolver_address() -> None:
    client = FakeHttpClient(FakeHttpResponse({"output_text": "{}"}))
    gateway = OpenAIResponsesLLMGateway(
        api_key="sk-test",
        model="gpt-test",
        base_url="https://models.example.com",
        client=client,
        base_url_resolver=lambda hostname: ["not-an-ip"],
    )

    with pytest.raises(LLMGatewayError, match="Unsafe OpenAI base_url"):
        gateway.generate_structured(
            agent_name="StrategyDraftAgent",
            context_pack=make_context_pack(),
            output_schema={"type": "object"},
            schema_name="agent_output",
            max_output_tokens=800,
        )

    assert client.posts == []


def test_deepseek_chat_gateway_uses_json_output_mode_without_raw_state() -> None:
    client = FakeHttpClient(
        FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"summary": "ok", "artifact_payload": {"answer": "done"}},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
    )
    gateway = DeepSeekChatCompletionsLLMGateway(
        api_key="sk-deepseek",
        model="deepseek-v4-pro",
        client=client,
    )

    result = gateway.generate_structured(
        agent_name="StrategyDraftAgent",
        context_pack=make_context_pack(),
        output_schema={"type": "object"},
        schema_name="agent_output",
        max_output_tokens=800,
    )

    assert result["summary"] == "ok"
    url, headers, body = client.posts[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-deepseek"
    assert body["model"] == "deepseek-v4-pro"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    user_payload = json.loads(body["messages"][1]["content"])
    assert "context_pack" in user_payload
    assert "json_schema" in user_payload
    assert "recruitment_state" not in body["messages"][1]["content"]
    assert "node_history" not in body["messages"][1]["content"]


def test_deepseek_chat_gateway_rejects_invalid_json_content() -> None:
    gateway = DeepSeekChatCompletionsLLMGateway(
        api_key="sk-deepseek",
        model="deepseek-v4-pro",
        client=FakeHttpClient(
            FakeHttpResponse({"choices": [{"message": {"content": "not-json"}}]})
        ),
    )

    with pytest.raises(LLMGatewayError):
        gateway.generate_structured(
            agent_name="StrategyDraftAgent",
            context_pack=make_context_pack(),
            output_schema={"type": "object"},
            schema_name="agent_output",
            max_output_tokens=800,
        )


def test_deepseek_chat_gateway_rejects_literal_private_base_url() -> None:
    client = FakeHttpClient(FakeHttpResponse({"choices": []}))
    gateway = DeepSeekChatCompletionsLLMGateway(
        api_key="sk-deepseek",
        model="deepseek-v4-pro",
        base_url="https://127.0.0.1:11434",
        client=client,
    )

    with pytest.raises(LLMGatewayError, match="Unsafe DeepSeek base_url"):
        gateway.generate_structured(
            agent_name="StrategyDraftAgent",
            context_pack=make_context_pack(),
            output_schema={"type": "object"},
            schema_name="agent_output",
            max_output_tokens=800,
        )

    assert client.posts == []


def test_strict_schema_conversion_removes_defaults_and_requires_all_object_fields() -> None:
    strict = to_openai_strict_json_schema(
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "summary": {"type": "string", "default": ""},
                "nested": {
                    "type": "object",
                    "properties": {"score": {"type": "number", "default": 0}},
                },
            },
        }
    )

    assert strict["additionalProperties"] is False
    assert strict["required"] == ["id", "summary", "nested"]
    assert "format" not in strict["properties"]["id"]
    assert "default" not in strict["properties"]["summary"]
    assert strict["properties"]["nested"]["additionalProperties"] is False
    assert strict["properties"]["nested"]["required"] == ["score"]
