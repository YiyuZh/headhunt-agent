from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.gateways.embeddings import OpenAIEmbeddingGateway
from app.gateways.llm import (
    DeepSeekChatCompletionsLLMGateway,
    OpenAIResponsesLLMGateway,
)
from app.model_profiles.gateway_factory import (
    UserModelGatewayError,
    UserModelGatewayFactory,
    UserModelLLMGateway,
)
from app.model_profiles.repository import (
    ModelProfileAccessError,
    ModelProfileNotFoundError,
    ModelProfileRepository,
)
from app.model_profiles.secrets import ModelSecretService


def test_model_secret_service_encrypts_and_decrypts_without_plaintext() -> None:
    service = ModelSecretService("test-secret-key-for-model-profiles")

    encrypted = service.encrypt_api_key("sk-user-secret")

    assert "sk-user-secret" not in encrypted
    assert encrypted.startswith("v1:")
    assert service.decrypt_api_key(encrypted) == "sk-user-secret"


def test_model_secret_service_rejects_wrong_key() -> None:
    encrypted = ModelSecretService("correct-key").encrypt_api_key("sk-user-secret")

    with pytest.raises(Exception, match="could not be decrypted"):
        ModelSecretService("wrong-key").decrypt_api_key(encrypted)


class FakeProfileRepository:
    def __init__(self, profile):
        self.profile = profile
        self.mark_used_calls = []

    def get_active_profile(self, *, profile_id, guild_id, user_id, tenant_id=None, usage="chat"):
        if self.profile is None or self.profile.id != profile_id:
            raise ModelProfileNotFoundError("not found")
        if usage is not None and self.profile.usage != usage:
            raise ModelProfileNotFoundError("not found")
        if guild_id is not None and self.profile.guild_id != guild_id:
            raise ModelProfileAccessError("guild mismatch")
        if user_id is not None and self.profile.user_id != user_id:
            raise ModelProfileAccessError("user mismatch")
        if tenant_id is not None and self.profile.tenant_id not in {None, tenant_id}:
            raise ModelProfileAccessError("tenant mismatch")
        return self.profile

    def mark_used(self, *, profile_id):
        self.mark_used_calls.append(profile_id)


def make_profile(**overrides):
    data = {
        "id": uuid4(),
        "tenant_id": "tenant-1",
        "guild_id": "guild-1",
        "user_id": "user-1",
        "provider": "deepseek",
        "model_name": "deepseek-v4-pro",
        "encrypted_api_key": ModelSecretService("model-secret").encrypt_api_key("sk-user"),
        "base_url": None,
        "usage": "chat",
        "status": "active",
        "display_name": "profile",
        "is_default": True,
        "last_test_status": "untested",
        "last_used_at": None,
        "created_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_user_model_gateway_factory_builds_deepseek_chat_gateway_for_owner() -> None:
    profile = make_profile()
    factory = UserModelGatewayFactory(
        repository=FakeProfileRepository(profile),
        secret_service=ModelSecretService("model-secret"),
    )

    gateway = factory.build_chat_gateway(
        profile_id=profile.id,
        tenant_id="tenant-1",
        guild_id="guild-1",
        user_id="user-1",
    )

    assert isinstance(gateway, DeepSeekChatCompletionsLLMGateway)
    assert gateway.model == "deepseek-v4-pro"


def test_user_model_gateway_factory_blocks_other_users_profile() -> None:
    profile = make_profile()
    factory = UserModelGatewayFactory(
        repository=FakeProfileRepository(profile),
        secret_service=ModelSecretService("model-secret"),
    )

    with pytest.raises(ModelProfileAccessError):
        factory.build_chat_gateway(
            profile_id=profile.id,
            tenant_id="tenant-1",
            guild_id="guild-1",
            user_id="user-2",
        )


def test_user_model_gateway_factory_requires_owner_scope() -> None:
    profile = make_profile()
    factory = UserModelGatewayFactory(
        repository=FakeProfileRepository(profile),
        secret_service=ModelSecretService("model-secret"),
    )

    with pytest.raises(UserModelGatewayError, match="guild_id and user_id"):
        factory.build_chat_gateway(
            profile_id=profile.id,
            tenant_id="tenant-1",
            guild_id=None,
            user_id="user-1",
        )


def test_user_model_gateway_factory_builds_openai_chat_and_embedding_profiles() -> None:
    secret = ModelSecretService("model-secret")
    chat_profile = make_profile(
        provider="openai",
        model_name="gpt-4.1-mini",
        encrypted_api_key=secret.encrypt_api_key("sk-openai"),
        usage="chat",
    )
    embedding_profile = make_profile(
        id=uuid4(),
        provider="openai",
        model_name="text-embedding-3-small",
        encrypted_api_key=secret.encrypt_api_key("sk-openai"),
        usage="embedding",
    )

    chat_gateway = UserModelGatewayFactory(
        repository=FakeProfileRepository(chat_profile),
        secret_service=secret,
    ).build_chat_gateway(
        profile_id=chat_profile.id,
        tenant_id="tenant-1",
        guild_id="guild-1",
        user_id="user-1",
    )
    embedding_gateway = UserModelGatewayFactory(
        repository=FakeProfileRepository(embedding_profile),
        secret_service=secret,
    ).build_embedding_gateway(
        profile_id=embedding_profile.id,
        tenant_id="tenant-1",
        guild_id="guild-1",
        user_id="user-1",
    )

    assert isinstance(chat_gateway, OpenAIResponsesLLMGateway)
    assert isinstance(embedding_gateway, OpenAIEmbeddingGateway)


def test_user_model_gateway_factory_rejects_deepseek_embedding_profile() -> None:
    profile = make_profile(usage="embedding")
    factory = UserModelGatewayFactory(
        repository=FakeProfileRepository(profile),
        secret_service=ModelSecretService("model-secret"),
    )

    with pytest.raises(UserModelGatewayError, match="embedding profiles"):
        factory.build_embedding_gateway(
            profile_id=profile.id,
            tenant_id="tenant-1",
            guild_id="guild-1",
            user_id="user-1",
        )


class FakeLLMForUserProfile:
    def __init__(self):
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}

    def model_info(self, **kwargs):
        raise AssertionError("not used")


class FakeUserModelFactory:
    def __init__(self):
        self.repository = SimpleNamespace(
            mark_used=lambda profile_id: self.marked.append(profile_id)
        )
        self.marked = []
        self.gateway = FakeLLMForUserProfile()

    def build_chat_gateway(self, **kwargs):
        self.build_kwargs = kwargs
        return self.gateway


def test_user_model_llm_gateway_requires_discord_owner_scope_for_profile_calls() -> None:
    gateway = UserModelLLMGateway(factory=FakeUserModelFactory())

    with pytest.raises(UserModelGatewayError, match="model_guild_id"):
        gateway.generate_structured(model_profile_id=uuid4(), model_owner_user_id="user-1")


def test_user_model_llm_gateway_model_info_requires_discord_owner_scope() -> None:
    gateway = UserModelLLMGateway(factory=FakeUserModelFactory())

    with pytest.raises(UserModelGatewayError, match="model_guild_id"):
        gateway.model_info(model_profile_id=uuid4(), model_owner_user_id="user-1")


def test_user_model_llm_gateway_marks_profile_used_after_success() -> None:
    factory = FakeUserModelFactory()
    profile_id = uuid4()
    gateway = UserModelLLMGateway(factory=factory)

    result = gateway.generate_structured(
        model_profile_id=profile_id,
        model_guild_id="guild-1",
        model_owner_user_id="user-1",
        model_tenant_id="tenant-1",
    )

    assert result == {"ok": True}
    assert factory.build_kwargs["guild_id"] == "guild-1"
    assert factory.build_kwargs["user_id"] == "user-1"
    assert factory.marked == [profile_id]


class FakeRepositorySession:
    def __init__(self, profile):
        self.profile = profile
        self.flushed = False

    def get(self, model, profile_id):
        if profile_id == self.profile.id:
            return self.profile
        return None

    def flush(self):
        self.flushed = True


def test_model_profile_repository_revoke_allows_embedding_profiles() -> None:
    profile = make_profile(usage="embedding")
    session = FakeRepositorySession(profile)

    summary = ModelProfileRepository(session).revoke(
        profile_id=profile.id,
        guild_id="guild-1",
        user_id="user-1",
    )

    assert summary.usage == "embedding"
    assert summary.status == "revoked"
    assert profile.encrypted_api_key is None
    assert session.flushed is True
