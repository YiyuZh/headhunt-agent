from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import Settings
from app.schemas.discord_commands import DiscordCommandRegistrationResult

APPLICATION_COMMAND = 1
OPTION_SUB_COMMAND = 1
OPTION_STRING = 3


class DiscordCommandConfigurationError(RuntimeError):
    pass


class DiscordCommandRegistrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscordCommandRegistry:
    settings: Settings
    client: httpx.Client | None = None

    def command_payload(self) -> list[dict[str, Any]]:
        return discord_application_commands()

    def command_names(self) -> list[str]:
        return [command["name"] for command in self.command_payload()]

    def register_commands(
        self,
        *,
        guild_ids: list[str] | None = None,
        include_global: bool = False,
    ) -> list[DiscordCommandRegistrationResult]:
        application_id = _required_text(
            self.settings.discord_application_id,
            "DISCORD_APPLICATION_ID is required for Discord command registration",
        )
        bot_token = _required_secret(
            self.settings.discord_bot_token,
            "DISCORD_BOT_TOKEN is required for Discord command registration",
        )
        targets = _target_guild_ids(
            explicit_guild_ids=guild_ids,
            register_guild_id=self.settings.discord_command_register_guild_id,
            allowed_guild_ids=self.settings.discord_allowed_guild_ids,
        )
        if not targets and not include_global:
            raise DiscordCommandConfigurationError(
                "Set guild_ids, DISCORD_COMMAND_REGISTER_GUILD_ID, "
                "DISCORD_ALLOWED_GUILD_IDS, or include_global=true"
            )

        if self.client is not None:
            return self._register_with_client(
                client=self.client,
                bot_token=bot_token,
                application_id=application_id,
                targets=targets,
                include_global=include_global,
            )

        with httpx.Client(timeout=30.0) as client:
            return self._register_with_client(
                client=client,
                bot_token=bot_token,
                application_id=application_id,
                targets=targets,
                include_global=include_global,
            )

    def _register_with_client(
        self,
        *,
        client: httpx.Client,
        bot_token: str,
        application_id: str,
        targets: list[str],
        include_global: bool,
    ) -> list[DiscordCommandRegistrationResult]:
        results: list[DiscordCommandRegistrationResult] = []
        for guild_id in targets:
            results.append(
                self._put_commands(
                    client=client,
                    bot_token=bot_token,
                    route=f"/applications/{application_id}/guilds/{guild_id}/commands",
                    target_type="guild",
                    target_id=guild_id,
                )
            )
        if include_global:
            results.append(
                self._put_commands(
                    client=client,
                    bot_token=bot_token,
                    route=f"/applications/{application_id}/commands",
                    target_type="global",
                    target_id=None,
                )
            )
        return results

    def _put_commands(
        self,
        *,
        client: httpx.Client,
        bot_token: str,
        route: str,
        target_type: Literal["guild", "global"],
        target_id: str | None,
    ) -> DiscordCommandRegistrationResult:
        url = f"{self.settings.discord_api_base_url.rstrip('/')}{route}"
        response = client.put(
            url,
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json",
            },
            json=self.command_payload(),
        )
        if response.status_code >= 400:
            raise DiscordCommandRegistrationError(
                f"Discord command registration failed for {target_type}"
                f"{':' + target_id if target_id else ''}: HTTP {response.status_code}"
            )
        return DiscordCommandRegistrationResult(
            target_type=target_type,
            target_id=target_id,
            command_count=len(self.command_payload()),
            status_code=response.status_code,
            route=route,
        )


def discord_application_commands() -> list[dict[str, Any]]:
    return [
        {
            "name": "model",
            "description": "Manage your BYOK OpenAI or DeepSeek model profiles.",
            "type": APPLICATION_COMMAND,
            "options": [
                _subcommand(
                    "add",
                    "Add an encrypted user-owned model profile.",
                    [
                        _string_option(
                            "provider",
                            "Model provider.",
                            required=True,
                            choices=["openai", "deepseek"],
                        ),
                        _string_option(
                            "usage",
                            "Profile usage.",
                            required=False,
                            choices=["chat", "embedding"],
                        ),
                    ],
                ),
                _subcommand(
                    "list",
                    "List your model profiles without showing API keys.",
                    [
                        _string_option(
                            "usage",
                            "Optional profile usage filter.",
                            required=False,
                            choices=["chat", "embedding"],
                        )
                    ],
                ),
                _profile_subcommand("use", "Set one of your profiles as the default."),
                _profile_subcommand("test", "Run a short smoke test for a model profile."),
                _profile_subcommand("revoke", "Revoke a model profile and delete its secret."),
            ],
        },
        {
            "name": "headhunt",
            "description": "Start or inspect an AI headhunting workflow.",
            "type": APPLICATION_COMMAND,
            "options": [
                _subcommand(
                    "new",
                    "Create a requisition intake draft for double check.",
                    [
                        _string_option("role_title", "Target role title.", required=True),
                        _string_option(
                            "jd",
                            "Job description or requirement brief.",
                            required=True,
                        ),
                        _string_option("city", "Target city or region.", required=False),
                    ],
                ),
                _subcommand(
                    "candidate",
                    "Create a candidate evidence intake draft.",
                    [
                        _string_option(
                            "candidate",
                            "Candidate resume or evidence text.",
                            required=True,
                        ),
                        _string_option(
                            "requisition",
                            "Related requisition or thread id.",
                            required=False,
                        ),
                    ],
                ),
                _subcommand(
                    "map",
                    "Create a talent mapping intake draft.",
                    [
                        _string_option(
                            "query",
                            "Mapping goal, market, company, or skill scope.",
                            required=True,
                        ),
                        _string_option("city", "Optional city or region.", required=False),
                    ],
                ),
                _subcommand(
                    "status",
                    "Show a workflow thread status summary.",
                    [_string_option("thread_id", "Workflow thread id.", required=True)],
                ),
            ],
        },
    ]


def _profile_subcommand(name: str, description: str) -> dict[str, Any]:
    return _subcommand(
        name,
        description,
        [
            _string_option("display_name", "Your model profile display name.", required=True),
            _string_option(
                "usage",
                "Profile usage.",
                required=False,
                choices=["chat", "embedding"],
            ),
        ],
    )


def _subcommand(
    name: str,
    description: str,
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    command: dict[str, Any] = {
        "type": OPTION_SUB_COMMAND,
        "name": name,
        "description": description,
    }
    if options:
        command["options"] = options
    return command


def _string_option(
    name: str,
    description: str,
    *,
    required: bool,
    choices: list[str] | None = None,
) -> dict[str, Any]:
    option: dict[str, Any] = {
        "type": OPTION_STRING,
        "name": name,
        "description": description,
        "required": required,
    }
    if choices:
        option["choices"] = [{"name": choice, "value": choice} for choice in choices]
    return option


def _target_guild_ids(
    *,
    explicit_guild_ids: list[str] | None,
    register_guild_id: str | None,
    allowed_guild_ids: str | None,
) -> list[str]:
    targets: list[str] = []
    for source in (
        explicit_guild_ids or [],
        _csv_values(register_guild_id),
        _csv_values(allowed_guild_ids),
    ):
        values = source if isinstance(source, list) else [source]
        for value in values:
            text = str(value).strip()
            if text and text not in targets:
                targets.append(text)
    return targets


def _csv_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _required_text(value: str | None, message: str) -> str:
    if not value or not value.strip():
        raise DiscordCommandConfigurationError(message)
    return value.strip()


def _required_secret(value, message: str) -> str:
    if value is None or not value.get_secret_value().strip():
        raise DiscordCommandConfigurationError(message)
    return value.get_secret_value().strip()
