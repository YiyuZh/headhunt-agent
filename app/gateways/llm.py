import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from app.gateways.url_safety import AddressResolver, BaseUrlSafetyError, validate_https_base_url
from app.schemas.context import ContextPack

logger = logging.getLogger(__name__)


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
        attempts = 3
        safe_base_url = self._safe_base_url()
        max_tokens = _deepseek_max_tokens(schema_name, max_output_tokens)
        last_error: LLMGatewayError | None = None
        for attempt_index in range(attempts):
            body = self._structured_json_body(
                agent_name=agent_name,
                context_pack=context_pack,
                output_schema=output_schema,
                schema_name=schema_name,
                max_output_tokens=max_tokens,
                retry=attempt_index > 0,
            )
            response = self.client.post(
                f"{safe_base_url}/chat/completions",
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
            payload = response.json()
            try:
                return _extract_chat_completion_json(payload)
            except LLMGatewayError as exc:
                last_error = exc
                logger.warning(
                    "DeepSeek structured JSON extraction failed: model=%s schema=%s "
                    "attempt=%s/%s error=%s diagnostics=%s",
                    self.model,
                    schema_name,
                    attempt_index + 1,
                    attempts,
                    _safe_llm_error(exc),
                    _chat_completion_diagnostics(payload),
                )
                if attempt_index >= attempts - 1 or not _retryable_json_output_error(exc):
                    raise
        raise last_error or LLMGatewayError("DeepSeek structured JSON extraction failed")

    def _structured_json_body(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        retry: bool,
    ) -> dict[str, Any]:
        system_content = (
            "You are an isolated recruiting workflow agent. "
            "Use only the provided ContextPack. Return exactly one valid json object. "
            "Do not return markdown, code fences, comments, explanations, or empty content. "
            f"The json object must match the schema named {schema_name}."
        )
        if retry:
            system_content += (
                " The previous response was empty or not valid JSON. "
                "Retry now with only the JSON object."
            )
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "agent_name": agent_name,
                            "context_pack": context_pack.model_dump(mode="json"),
                            "json_schema": output_schema,
                            "json_output_contract": {
                                "format": "json object",
                                "only_json": True,
                                "no_markdown": True,
                                "example": {"ok": True},
                            },
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
        if not retry:
            if self.reasoning_effort:
                body["reasoning_effort"] = self.reasoning_effort
            if self.thinking:
                body["thinking"] = {"type": self.thinking}
        return body

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
    content = _message_content_text(message)
    if content:
        return _parse_chat_completion_json_text(content)
    tool_arguments = _message_tool_arguments(message)
    if tool_arguments:
        return _parse_chat_completion_json_text(tool_arguments)
    raise LLMGatewayError(
        "chat completion response did not contain JSON content "
        f"({_chat_choice_diagnostics(first_choice)})"
    )


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


def _parse_chat_completion_json_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates = [stripped, *_json_fence_candidates(stripped)]
    embedded = _extract_first_json_object_text(stripped)
    if embedded:
        candidates.append(embedded)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return _parse_json_object(candidate)
        except LLMGatewayError:
            continue
    raise LLMGatewayError("LLM structured output is not valid JSON")


def _json_fence_candidates(text: str) -> list[str]:
    if "```" not in text:
        return []
    candidates: list[str] = []
    parts = text.split("```")
    for index in range(1, len(parts), 2):
        block = parts[index].strip()
        if block.lower().startswith("json"):
            block = block[4:].strip()
        candidates.append(block)
    return candidates


def _extract_first_json_object_text(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                pieces.append(text)
            elif isinstance(item.get("content"), str):
                pieces.append(item["content"])
        return "\n".join(piece for piece in pieces if piece.strip()).strip()
    return ""


def _message_tool_arguments(message: dict[str, Any]) -> str:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return ""
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments.strip():
            return arguments.strip()
    return ""


def _deepseek_max_tokens(schema_name: str, requested: int) -> int:
    if schema_name == "feishu_task_intake":
        return max(requested, 2400)
    return requested


def _retryable_json_output_error(exc: LLMGatewayError) -> bool:
    text = str(exc)
    return "JSON content" in text or "not valid JSON" in text


def _safe_llm_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "sk-" in text or "api_key" in lowered or "secret" in lowered:
        return f"{exc.__class__.__name__}: credential rejected or unavailable"
    return f"{exc.__class__.__name__}: {text[:240]}"


def _chat_completion_diagnostics(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "choices=missing"
    return _chat_choice_diagnostics(choices[0])


def _chat_choice_diagnostics(choice: dict[str, Any]) -> str:
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = _message_content_text(message) if isinstance(message, dict) else ""
    tool_arguments = _message_tool_arguments(message) if isinstance(message, dict) else ""
    finish_reason = choice.get("finish_reason")
    return (
        f"finish_reason={finish_reason or '-'} "
        f"content_empty={not bool(content)} "
        f"tool_arguments_present={bool(tool_arguments)}"
    )
