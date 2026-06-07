import json
from time import time
from uuid import uuid4

from fastapi.testclient import TestClient
from nacl.signing import SigningKey

import app.api.discord as discord_api
from app.core.config import Settings
from app.main import create_app
from app.schemas.model_profiles import ModelProfileSummary


def _client(signing_key: SigningKey) -> TestClient:
    settings = Settings(
        discord_public_key=signing_key.verify_key.encode().hex(),
        discord_allowed_guild_ids="guild-1",
        discord_allowed_channel_ids="channel-1",
        model_secret_encryption_key="model-secret",
    )
    return TestClient(create_app(settings=settings))


def _signed_headers(signing_key: SigningKey, body: bytes) -> dict[str, str]:
    timestamp = str(int(time()))
    signature = signing_key.sign(timestamp.encode("utf-8") + body).signature.hex()
    return {
        "Content-Type": "application/json",
        "X-Signature-Ed25519": signature,
        "X-Signature-Timestamp": timestamp,
    }


def _post(client: TestClient, signing_key: SigningKey, payload: dict) -> object:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(
        "/discord/interactions",
        content=body,
        headers=_signed_headers(signing_key, body),
    )


def _identity_payload(data: dict, *, interaction_type: int = 2) -> dict:
    return {
        "type": interaction_type,
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "member": {"user": {"id": "user-1"}},
        "data": data,
    }


def _summary(**overrides) -> ModelProfileSummary:
    data = {
        "id": uuid4(),
        "tenant_id": "guild-1",
        "guild_id": "guild-1",
        "user_id": "user-1",
        "provider": "deepseek",
        "model_name": "deepseek-v4-pro",
        "display_name": "my-deepseek",
        "usage": "chat",
        "status": "active",
        "is_default": True,
        "last_test_status": "untested",
    }
    data.update(overrides)
    return ModelProfileSummary(**data)


class FakeModelProfileService:
    def __init__(self):
        self.created = None
        self.used = None
        self.revoked = None

    def create_profile(self, data):
        self.created = data
        return _summary(
            provider=data.provider,
            model_name=data.model_name,
            display_name=data.display_name,
        )

    def list_profiles(self, *, guild_id, user_id, usage=None):
        assert guild_id == "guild-1"
        assert user_id == "user-1"
        return [_summary()]

    def use_profile(self, *, guild_id, user_id, display_name, usage="chat"):
        self.used = (guild_id, user_id, display_name, usage)
        return _summary(display_name=display_name, usage=usage)

    def revoke_profile(self, *, guild_id, user_id, display_name, usage=None):
        self.revoked = (guild_id, user_id, display_name, usage)
        return _summary(display_name=display_name, status="revoked")

    def test_profile(self, *, guild_id, user_id, display_name, usage="chat"):
        return type(
            "Result",
            (),
            {
                "status": "ok",
                "provider": "deepseek",
                "model_name": "deepseek-v4-pro",
                "message": "passed",
            },
        )()


def test_discord_ping_requires_valid_signature_and_returns_pong() -> None:
    signing_key = SigningKey.generate()
    client = _client(signing_key)

    response = _post(client, signing_key, {"type": 1})

    assert response.status_code == 200
    assert response.json() == {"type": 1}


def test_discord_interaction_rejects_bad_signature() -> None:
    signing_key = SigningKey.generate()
    client = _client(signing_key)
    body = b'{"type":1}'

    response = client.post(
        "/discord/interactions",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature-Ed25519": "00" * 64,
            "X-Signature-Timestamp": "1760000000",
        },
    )

    assert response.status_code == 401


def test_discord_interaction_rejects_stale_timestamp() -> None:
    signing_key = SigningKey.generate()
    client = _client(signing_key)
    body = b'{"type":1}'
    timestamp = str(int(time()) - 1000)
    signature = signing_key.sign(timestamp.encode("utf-8") + body).signature.hex()

    response = client.post(
        "/discord/interactions",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
        },
    )

    assert response.status_code == 401


def test_model_add_slash_command_returns_modal(monkeypatch) -> None:
    signing_key = SigningKey.generate()
    service = FakeModelProfileService()
    monkeypatch.setattr(
        discord_api,
        "build_model_profile_service",
        lambda _request, _session: service,
    )
    client = _client(signing_key)

    response = _post(
        client,
        signing_key,
        _identity_payload(
            {
                "name": "model",
                "options": [
                    {
                        "type": 1,
                        "name": "add",
                        "options": [
                            {"name": "provider", "value": "deepseek"},
                            {"name": "usage", "value": "chat"},
                        ],
                    }
                ],
            }
        ),
    )

    body = response.json()
    assert response.status_code == 200
    assert body["type"] == 9
    assert body["data"]["custom_id"] == "model:add:deepseek:chat"


def test_model_add_modal_encrypt_path_returns_ephemeral_without_key(monkeypatch) -> None:
    signing_key = SigningKey.generate()
    service = FakeModelProfileService()
    monkeypatch.setattr(
        discord_api,
        "build_model_profile_service",
        lambda _request, _session: service,
    )
    client = _client(signing_key)
    api_key = "sk-user-secret"

    response = _post(
        client,
        signing_key,
        _identity_payload(
            {
                "custom_id": "model:add:deepseek:chat",
                "components": [
                    {"components": [{"custom_id": "display_name", "value": "my-deepseek"}]},
                    {"components": [{"custom_id": "model_name", "value": "deepseek-v4-pro"}]},
                    {"components": [{"custom_id": "api_key", "value": api_key}]},
                    {"components": [{"custom_id": "base_url", "value": "https://api.deepseek.com"}]},
                ],
            },
            interaction_type=5,
        ),
    )

    text = response.text
    assert response.status_code == 200
    assert response.json()["type"] == 4
    assert response.json()["data"]["flags"] == 64
    assert api_key not in text
    assert service.created.guild_id == "guild-1"
    assert service.created.user_id == "user-1"


def test_model_list_does_not_return_plain_or_encrypted_key(monkeypatch) -> None:
    signing_key = SigningKey.generate()
    service = FakeModelProfileService()
    monkeypatch.setattr(
        discord_api,
        "build_model_profile_service",
        lambda _request, _session: service,
    )
    client = _client(signing_key)

    response = _post(
        client,
        signing_key,
        _identity_payload(
            {"name": "model", "options": [{"type": 1, "name": "list", "options": []}]}
        ),
    )

    text = response.text
    assert "my-deepseek" in text
    assert "sk-" not in text
    assert "encrypted" not in text.lower()


def test_model_use_and_revoke_are_scoped_to_discord_identity(monkeypatch) -> None:
    signing_key = SigningKey.generate()
    service = FakeModelProfileService()
    monkeypatch.setattr(
        discord_api,
        "build_model_profile_service",
        lambda _request, _session: service,
    )
    client = _client(signing_key)

    use_response = _post(
        client,
        signing_key,
        _identity_payload(
            {
                "name": "model",
                "options": [
                    {
                        "type": 1,
                        "name": "use",
                        "options": [
                            {"name": "display_name", "value": "my-deepseek"},
                            {"name": "usage", "value": "chat"},
                        ],
                    }
                ],
            }
        ),
    )
    revoke_response = _post(
        client,
        signing_key,
        _identity_payload(
            {
                "name": "model",
                "options": [
                    {
                        "type": 1,
                        "name": "revoke",
                        "options": [{"name": "display_name", "value": "my-deepseek"}],
                    }
                ],
            }
        ),
    )

    assert use_response.status_code == 200
    assert revoke_response.status_code == 200
    assert service.used == ("guild-1", "user-1", "my-deepseek", "chat")
    assert service.revoked == ("guild-1", "user-1", "my-deepseek", None)


def test_model_test_command_returns_ephemeral_smoke_result(monkeypatch) -> None:
    signing_key = SigningKey.generate()
    service = FakeModelProfileService()
    monkeypatch.setattr(
        discord_api,
        "build_model_profile_service",
        lambda _request, _session: service,
    )
    client = _client(signing_key)

    response = _post(
        client,
        signing_key,
        _identity_payload(
            {
                "name": "model",
                "options": [
                    {
                        "type": 1,
                        "name": "test",
                        "options": [
                            {"name": "display_name", "value": "my-deepseek"},
                            {"name": "usage", "value": "chat"},
                        ],
                    }
                ],
            }
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == 4
    assert body["data"]["flags"] == 64
    assert "模型测试 ok" in body["data"]["content"]
