from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.discord as discord_api
from app.core.config import Settings
from app.discord.commands import (
    DiscordCommandConfigurationError,
    DiscordCommandRegistrationError,
    DiscordCommandRegistry,
    discord_application_commands,
)
from app.main import create_app
from app.schemas.discord_commands import DiscordCommandRegistrationResult

ADMIN_HEADERS = {"X-Internal-Admin-Key": "admin-secret"}


def test_discord_application_commands_include_model_and_headhunt_subcommands() -> None:
    commands = discord_application_commands()
    by_name = {command["name"]: command for command in commands}

    assert set(by_name) == {"model", "headhunt"}
    assert {option["name"] for option in by_name["model"]["options"]} == {
        "add",
        "list",
        "use",
        "test",
        "revoke",
    }
    assert {option["name"] for option in by_name["headhunt"]["options"]} == {
        "new",
        "candidate",
        "map",
        "status",
    }


class FakeDiscordClient:
    def __init__(self, *, status_code: int = 200):
        self.status_code = status_code
        self.requests = []

    def put(self, url, *, headers, json):
        self.requests.append({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(status_code=self.status_code)


def test_discord_command_registry_registers_guild_commands_with_bot_token() -> None:
    client = FakeDiscordClient()
    registry = DiscordCommandRegistry(
        settings=Settings(
            discord_application_id="app-1",
            discord_bot_token="bot-secret",
            discord_allowed_guild_ids="guild-1,guild-2",
        ),
        client=client,
    )

    results = registry.register_commands()

    assert [result.target_id for result in results] == ["guild-1", "guild-2"]
    assert all(result.command_count == 2 for result in results)
    assert client.requests[0]["url"].endswith("/applications/app-1/guilds/guild-1/commands")
    assert client.requests[0]["headers"]["Authorization"] == "Bot bot-secret"
    assert {command["name"] for command in client.requests[0]["json"]} == {"model", "headhunt"}


def test_discord_command_registry_can_register_global_commands() -> None:
    client = FakeDiscordClient()
    registry = DiscordCommandRegistry(
        settings=Settings(
            discord_application_id="app-1",
            discord_bot_token="bot-secret",
        ),
        client=client,
    )

    results = registry.register_commands(include_global=True)

    assert len(results) == 1
    assert results[0].target_type == "global"
    assert results[0].target_id is None
    assert client.requests[0]["url"].endswith("/applications/app-1/commands")


def test_discord_command_registry_requires_target_or_global() -> None:
    registry = DiscordCommandRegistry(
        settings=Settings(
            discord_application_id="app-1",
            discord_bot_token="bot-secret",
        ),
        client=FakeDiscordClient(),
    )

    with pytest.raises(DiscordCommandConfigurationError, match="Set guild_ids"):
        registry.register_commands()


def test_discord_command_registry_error_does_not_leak_bot_token() -> None:
    registry = DiscordCommandRegistry(
        settings=Settings(
            discord_application_id="app-1",
            discord_bot_token="bot-secret",
            discord_allowed_guild_ids="guild-1",
        ),
        client=FakeDiscordClient(status_code=401),
    )

    with pytest.raises(DiscordCommandRegistrationError) as exc_info:
        registry.register_commands()

    assert "HTTP 401" in str(exc_info.value)
    assert "bot-secret" not in str(exc_info.value)


def test_discord_command_register_endpoint_requires_internal_admin_key() -> None:
    client = TestClient(
        create_app(settings=Settings(internal_admin_api_key="admin-secret"))
    )

    response = client.post("/discord/commands/register", json={"dry_run": True})

    assert response.status_code == 403


def test_discord_command_register_endpoint_supports_dry_run() -> None:
    client = TestClient(
        create_app(settings=Settings(internal_admin_api_key="admin-secret"))
    )

    response = client.post(
        "/discord/commands/register",
        json={"dry_run": True},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["command_names"] == ["model", "headhunt"]
    assert body["registered"] == []


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def command_names(self):
        return ["model", "headhunt"]

    def register_commands(self, *, guild_ids=None, include_global=False):
        self.calls.append((guild_ids, include_global))
        return [
            DiscordCommandRegistrationResult(
                target_type="guild",
                target_id="guild-1",
                command_count=2,
                status_code=200,
                route="/applications/app-1/guilds/guild-1/commands",
            )
        ]


def test_discord_command_register_endpoint_calls_registry(monkeypatch) -> None:
    registry = FakeRegistry()
    monkeypatch.setattr(
        discord_api,
        "build_discord_command_registry",
        lambda _request: registry,
    )
    client = TestClient(
        create_app(settings=Settings(internal_admin_api_key="admin-secret"))
    )

    response = client.post(
        "/discord/commands/register",
        json={"guild_ids": ["guild-1"], "include_global": False},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert registry.calls == [(["guild-1"], False)]
    assert response.json()["registered"][0]["target_id"] == "guild-1"
