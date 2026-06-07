from typing import Any

from app.model_profiles.service import (
    ModelProfileCreateInput,
    ModelProfileService,
    ModelProfileServiceError,
)
from app.schemas.model_profiles import ModelProfileSummary, ModelUsage

INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
INTERACTION_MODAL_SUBMIT = 5

RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4
RESPONSE_MODAL = 9

FLAG_EPHEMERAL = 64


class DiscordInteractionError(RuntimeError):
    pass


class DiscordModelInteractionHandler:
    def __init__(self, *, service: ModelProfileService):
        self.service = service

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        interaction_type = payload.get("type")
        if interaction_type == INTERACTION_PING:
            return {"type": RESPONSE_PONG}
        if interaction_type == INTERACTION_MODAL_SUBMIT:
            return self._handle_modal_submit(payload)
        if interaction_type == INTERACTION_APPLICATION_COMMAND:
            return self._handle_application_command(payload)
        return ephemeral_message("暂不支持这个 Discord interaction 类型")

    def _handle_application_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = _dict_value(payload.get("data"))
        if data.get("name") != "model":
            return ephemeral_message("当前仅支持 /model 命令；headhunt 流程仍未真实联调。")
        subcommand, options = _subcommand(data)
        identity = _identity(payload)
        if subcommand == "add":
            provider = _option_value(options, "provider") or "openai"
            usage = _usage(_option_value(options, "usage") or "chat")
            if provider not in {"openai", "deepseek"}:
                return ephemeral_message("provider 只支持 openai 或 deepseek")
            if usage == "embedding" and provider != "openai":
                return ephemeral_message("embedding profile 只支持 provider=openai")
            return model_add_modal(provider=provider, usage=usage)
        if subcommand == "list":
            usage = _optional_usage(_option_value(options, "usage"))
            profiles = self.service.list_profiles(
                guild_id=identity.guild_id,
                user_id=identity.user_id,
                usage=usage,
            )
            return ephemeral_message(_format_profile_list(profiles))
        if subcommand == "use":
            usage = _usage(_option_value(options, "usage") or "chat")
            display_name = _required_option(options, "display_name")
            try:
                profile = self.service.use_profile(
                    guild_id=identity.guild_id,
                    user_id=identity.user_id,
                    display_name=display_name,
                    usage=usage,
                )
            except Exception as exc:
                return ephemeral_message(_safe_error(exc))
            return ephemeral_message(f"已设置默认模型：{_profile_line(profile)}")
        if subcommand == "test":
            usage = _usage(_option_value(options, "usage") or "chat")
            display_name = _required_option(options, "display_name")
            try:
                result = self.service.test_profile(
                    guild_id=identity.guild_id,
                    user_id=identity.user_id,
                    display_name=display_name,
                    usage=usage,
                )
            except Exception as exc:
                return ephemeral_message(_safe_error(exc))
            return ephemeral_message(
                f"模型测试 {result.status}: {result.provider}/{result.model_name} - "
                f"{result.message}"
            )
        if subcommand == "revoke":
            usage = _optional_usage(_option_value(options, "usage"))
            display_name = _required_option(options, "display_name")
            try:
                profile = self.service.revoke_profile(
                    guild_id=identity.guild_id,
                    user_id=identity.user_id,
                    display_name=display_name,
                    usage=usage,
                )
            except Exception as exc:
                return ephemeral_message(_safe_error(exc))
            return ephemeral_message(f"已停用模型 profile：{_profile_line(profile)}")
        return ephemeral_message("未知 /model 子命令")

    def _handle_modal_submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = _dict_value(payload.get("data"))
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith("model:add:"):
            return ephemeral_message("未知 modal")
        try:
            _, _, provider, usage_text = custom_id.split(":", 3)
        except ValueError:
            return ephemeral_message("modal 状态无效，请重新执行 /model add")
        usage = _usage(usage_text)
        values = _modal_values(data)
        identity = _identity(payload)
        try:
            profile = self.service.create_profile(
                ModelProfileCreateInput(
                    tenant_id=identity.guild_id,
                    guild_id=identity.guild_id,
                    user_id=identity.user_id,
                    provider=provider,
                    model_name=_required_modal_value(values, "model_name"),
                    api_key=_required_modal_value(values, "api_key"),
                    display_name=values.get("display_name") or None,
                    base_url=values.get("base_url") or None,
                    usage=usage,
                    make_default=True,
                )
            )
        except Exception as exc:
            return ephemeral_message(_safe_error(exc))
        return ephemeral_message(f"模型 profile 已保存并加密：{_profile_line(profile)}")


class DiscordIdentity:
    def __init__(self, *, guild_id: str, user_id: str):
        self.guild_id = guild_id
        self.user_id = user_id


def is_interaction_allowed(
    payload: dict[str, Any],
    *,
    guild_ids: str | None,
    channel_ids: str | None,
) -> bool:
    guild_id = str(payload.get("guild_id") or "")
    channel_id = _channel_id(payload)
    allowed_guilds = _csv_set(guild_ids)
    allowed_channels = _csv_set(channel_ids)
    if allowed_guilds and guild_id not in allowed_guilds:
        return False
    if allowed_channels and channel_id not in allowed_channels:
        return False
    return True


def ephemeral_message(content: str) -> dict[str, Any]:
    return {
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": content[:1900], "flags": FLAG_EPHEMERAL},
    }


def model_add_modal(*, provider: str, usage: ModelUsage) -> dict[str, Any]:
    return {
        "type": RESPONSE_MODAL,
        "data": {
            "custom_id": f"model:add:{provider}:{usage}",
            "title": "添加模型配置",
            "components": [
                _text_input_row(
                    "display_name",
                    "显示名称",
                    required=False,
                    placeholder=f"例如 我的{provider}",
                ),
                _text_input_row(
                    "model_name",
                    "模型名称",
                    placeholder=_default_model(provider, usage),
                ),
                _text_input_row("api_key", "API Key", style=1),
                _text_input_row(
                    "base_url",
                    "Base URL",
                    required=False,
                    placeholder=_default_base_url(provider),
                ),
            ],
        },
    }


def _text_input_row(
    custom_id: str,
    label: str,
    *,
    style: int = 1,
    required: bool = True,
    placeholder: str | None = None,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": 4,
        "custom_id": custom_id,
        "label": label,
        "style": style,
        "required": required,
    }
    if placeholder:
        component["placeholder"] = placeholder
    return {"type": 1, "components": [component]}


def _identity(payload: dict[str, Any]) -> DiscordIdentity:
    guild_id = str(payload.get("guild_id") or "")
    user = _dict_value(_dict_value(payload.get("member")).get("user"))
    user_id = str(user.get("id") or _dict_value(payload.get("user")).get("id") or "")
    if not guild_id or not user_id:
        raise DiscordInteractionError("Discord interaction missing guild_id or user_id")
    return DiscordIdentity(guild_id=guild_id, user_id=user_id)


def _channel_id(payload: dict[str, Any]) -> str:
    channel_id = str(payload.get("channel_id") or "")
    if channel_id:
        return channel_id
    return str(_dict_value(payload.get("channel")).get("id") or "")


def _subcommand(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    options = data.get("options")
    if not isinstance(options, list) or not options:
        return "", []
    first = options[0] if isinstance(options[0], dict) else {}
    return str(first.get("name") or ""), _option_list(first.get("options"))


def _option_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _option_value(options: list[dict[str, Any]], name: str) -> str | None:
    for option in options:
        if option.get("name") == name:
            value = option.get("value")
            return str(value) if value is not None else None
    return None


def _required_option(options: list[dict[str, Any]], name: str) -> str:
    value = _option_value(options, name)
    if not value:
        raise DiscordInteractionError(f"缺少参数：{name}")
    return value


def _modal_values(data: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    components = data.get("components")
    if not isinstance(components, list):
        return values
    for row in components:
        for component in _option_list(_dict_value(row).get("components")):
            custom_id = component.get("custom_id")
            value = component.get("value")
            if isinstance(custom_id, str) and isinstance(value, str):
                values[custom_id] = value.strip()
    return values


def _required_modal_value(values: dict[str, str], name: str) -> str:
    value = values.get(name)
    if not value:
        raise DiscordInteractionError(f"缺少字段：{name}")
    return value


def _usage(value: str) -> ModelUsage:
    if value not in {"chat", "embedding"}:
        raise DiscordInteractionError("usage 只支持 chat 或 embedding")
    return value


def _optional_usage(value: str | None) -> ModelUsage | None:
    return _usage(value) if value else None


def _format_profile_list(profiles: list[ModelProfileSummary]) -> str:
    if not profiles:
        return "当前没有可用模型 profile。请先使用 /model add。"
    lines = ["当前模型 profile："]
    lines.extend(f"- {_profile_line(profile)}" for profile in profiles)
    return "\n".join(lines)


def _profile_line(profile: ModelProfileSummary) -> str:
    default = " 默认" if profile.is_default else ""
    return (
        f"{profile.display_name} [{profile.usage}] "
        f"{profile.provider}/{profile.model_name} {profile.status}{default}"
    )


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _csv_set(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ModelProfileServiceError):
        return str(exc)
    text = str(exc) or exc.__class__.__name__
    if "sk-" in text or "api_key" in text.lower():
        return "模型配置处理失败，API Key 未返回。"
    return text[:300]


def _default_model(provider: str, usage: ModelUsage) -> str:
    if usage == "embedding":
        return "text-embedding-3-small"
    if provider == "deepseek":
        return "deepseek-v4-pro"
    return "gpt-4.1-mini"


def _default_base_url(provider: str) -> str:
    if provider == "deepseek":
        return "https://api.deepseek.com"
    return "https://api.openai.com"
