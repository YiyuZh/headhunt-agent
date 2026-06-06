import json
from copy import deepcopy
from typing import Any, Protocol

import httpx

from app.schemas.context import ContextPack


class LLMGatewayError(RuntimeError):
    pass


class LLMGateway(Protocol):
    def generate_structured(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
    ) -> dict[str, Any]: ...


class OpenAIResponsesLLMGateway:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com",
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def generate_structured(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        strict_schema = to_openai_strict_json_schema(output_schema)
        response = self.client.post(
            f"{self.base_url}/v1/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": [
                    {
                        "role": "system",
                        "content": (
                            "You are an isolated recruiting workflow agent. "
                            "Use only the provided ContextPack. Return only structured JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "agent_name": agent_name,
                                "context_pack": context_pack.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": strict_schema,
                    }
                },
                "max_output_tokens": max_output_tokens,
            },
        )
        if response.status_code >= 400:
            raise LLMGatewayError(f"OpenAI Responses API error: HTTP {response.status_code}")
        payload = response.json()
        return _extract_structured_json(payload)


def _extract_structured_json(payload: dict[str, Any]) -> dict[str, Any]:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return _parse_json_object(output_text)

    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return _parse_json_object(text)

    raise LLMGatewayError("OpenAI Responses API response did not contain structured JSON text")


def to_openai_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _strict_schema_node(deepcopy(schema))


def _strict_schema_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_schema_node(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"default", "format"}:
            continue
        cleaned[key] = _strict_schema_node(item)

    properties = cleaned.get("properties")
    if isinstance(properties, dict):
        cleaned["additionalProperties"] = False
        cleaned["required"] = list(properties.keys())

    return cleaned


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMGatewayError("LLM structured output is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMGatewayError("LLM structured output must be a JSON object")
    return parsed
