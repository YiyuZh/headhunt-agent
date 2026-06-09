import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from app.gateways.url_safety import AddressResolver, BaseUrlSafetyError, validate_https_base_url
from app.schemas.context import ContextPack


class LLMGatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMModelInfo:
    model_profile_id: UUID | None
    model_provider: str
    model_name: str
    model_owner_user_id: str | None = None


class LLMGateway(Protocol):
    def generate_structured(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> dict[str, Any]: ...

    def model_info(
        self,
        *,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> LLMModelInfo: ...


class OpenAIResponsesLLMGateway:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com",
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        base_url_resolver: AddressResolver | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.base_url_resolver = base_url_resolver
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self._model_info = LLMModelInfo(
            model_profile_id=model_profile_id,
            model_provider="openai",
            model_name=model,
            model_owner_user_id=model_owner_user_id,
        )

    def generate_structured(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> dict[str, Any]:
        strict_schema = to_openai_strict_json_schema(output_schema)
        response = self.client.post(
            f"{self._safe_base_url()}/v1/responses",
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

    def model_info(
        self,
        *,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> LLMModelInfo:
        return self._model_info

    def _safe_base_url(self) -> str:
        try:
            return validate_https_base_url(
                self.base_url,
                trusted_public_hosts={"api.openai.com"},
                resolver=self.base_url_resolver,
            )
        except BaseUrlSafetyError as exc:
            raise LLMGatewayError(f"Unsafe OpenAI base_url: {exc}") from exc


class DeepSeekChatCompletionsLLMGateway:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
        thinking: str = "enabled",
        reasoning_effort: str = "high",
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        base_url_resolver: AddressResolver | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.base_url_resolver = base_url_resolver
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self._model_info = LLMModelInfo(
            model_profile_id=model_profile_id,
            model_provider="deepseek",
            model_name=model,
            model_owner_user_id=model_owner_user_id,
        )

    def generate_structured(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an isolated recruiting workflow agent. "
                        "Use only the provided ContextPack. Return valid JSON only. "
                        f"The JSON object must match the schema named {schema_name}."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "agent_name": agent_name,
                            "context_pack": context_pack.model_dump(mode="json"),
                            "json_schema": output_schema,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.thinking:
            body["thinking"] = {"type": self.thinking}

        response = self.client.post(
            f"{self._safe_base_url()}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if response.status_code >= 400:
            raise LLMGatewayError(
                f"DeepSeek Chat Completions API error: HTTP {response.status_code}"
            )
        return _extract_chat_completion_json(response.json())

    def model_info(
        self,
        *,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> LLMModelInfo:
        return self._model_info

    def _safe_base_url(self) -> str:
        try:
            return validate_https_base_url(
                self.base_url,
                trusted_public_hosts={"api.deepseek.com"},
                resolver=self.base_url_resolver,
            )
        except BaseUrlSafetyError as exc:
            raise LLMGatewayError(f"Unsafe DeepSeek base_url: {exc}") from exc


def get_gateway_model_info(
    gateway: LLMGateway,
    *,
    model_profile_id: UUID | None = None,
    model_owner_user_id: str | None = None,
    model_guild_id: str | None = None,
    model_tenant_id: str | None = None,
) -> LLMModelInfo:
    model_info = getattr(gateway, "model_info", None)
    if callable(model_info):
        return model_info(
            model_profile_id=model_profile_id,
            model_owner_user_id=model_owner_user_id,
            model_guild_id=model_guild_id,
            model_tenant_id=model_tenant_id,
        )
    return LLMModelInfo(
        model_profile_id=model_profile_id,
        model_provider="unknown",
        model_name="unknown",
        model_owner_user_id=model_owner_user_id,
    )


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


def _extract_chat_completion_json(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMGatewayError("chat completion response did not contain choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMGatewayError("chat completion choice is not an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMGatewayError("chat completion choice missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMGatewayError("chat completion response did not contain JSON content")
    return _parse_json_object(content)


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
