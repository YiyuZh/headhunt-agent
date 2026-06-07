from uuid import UUID

from app.gateways.embeddings import OpenAIEmbeddingGateway
from app.gateways.llm import (
    DeepSeekChatCompletionsLLMGateway,
    LLMGateway,
    LLMModelInfo,
    OpenAIResponsesLLMGateway,
)
from app.model_profiles.repository import (
    ModelProfileAccessError,
    ModelProfileNotFoundError,
    ModelProfileRepository,
)
from app.model_profiles.secrets import ModelSecretService


class UserModelGatewayError(RuntimeError):
    pass


class UserModelGatewayFactory:
    def __init__(
        self,
        *,
        repository: ModelProfileRepository,
        secret_service: ModelSecretService,
        provider_allowlist: set[str] | None = None,
        timeout_seconds: float | None = None,
    ):
        self.repository = repository
        self.secret_service = secret_service
        self.provider_allowlist = provider_allowlist or {"openai", "deepseek"}
        self.timeout_seconds = timeout_seconds

    def build_chat_gateway(
        self,
        *,
        profile_id: UUID,
        guild_id: str | None,
        user_id: str | None,
        tenant_id: str | None = None,
    ) -> LLMGateway:
        _require_owner_scope(guild_id=guild_id, user_id=user_id)
        profile = self.repository.get_active_profile(
            profile_id=profile_id,
            tenant_id=tenant_id,
            guild_id=guild_id,
            user_id=user_id,
            usage="chat",
        )
        provider = str(profile.provider).lower()
        if provider not in self.provider_allowlist:
            raise UserModelGatewayError(f"model provider is not allowed: {provider}")
        api_key = self.secret_service.decrypt_api_key(profile.encrypted_api_key)
        if provider == "openai":
            return OpenAIResponsesLLMGateway(
                api_key=api_key,
                model=profile.model_name,
                base_url=profile.base_url or "https://api.openai.com",
                timeout_seconds=self.timeout_seconds or 60.0,
                model_profile_id=profile.id,
                model_owner_user_id=profile.user_id,
            )
        if provider == "deepseek":
            return DeepSeekChatCompletionsLLMGateway(
                api_key=api_key,
                model=profile.model_name,
                base_url=profile.base_url or "https://api.deepseek.com",
                timeout_seconds=self.timeout_seconds or 60.0,
                model_profile_id=profile.id,
                model_owner_user_id=profile.user_id,
            )
        raise UserModelGatewayError(f"unsupported model provider: {provider}")

    def build_embedding_gateway(
        self,
        *,
        profile_id: UUID,
        guild_id: str | None,
        user_id: str | None,
        tenant_id: str | None = None,
    ) -> OpenAIEmbeddingGateway:
        _require_owner_scope(guild_id=guild_id, user_id=user_id)
        profile = self.repository.get_active_profile(
            profile_id=profile_id,
            tenant_id=tenant_id,
            guild_id=guild_id,
            user_id=user_id,
            usage="embedding",
        )
        provider = str(profile.provider).lower()
        if provider != "openai":
            raise UserModelGatewayError("embedding profiles must use provider=openai")
        api_key = self.secret_service.decrypt_api_key(profile.encrypted_api_key)
        return OpenAIEmbeddingGateway(
            api_key=api_key,
            model=profile.model_name,
            base_url=profile.base_url or "https://api.openai.com",
            timeout_seconds=self.timeout_seconds or 30.0,
        )

    def model_info(
        self,
        *,
        profile_id: UUID,
        guild_id: str | None,
        user_id: str | None,
        tenant_id: str | None = None,
    ) -> LLMModelInfo:
        _require_owner_scope(guild_id=guild_id, user_id=user_id)
        profile = self.repository.get_active_profile(
            profile_id=profile_id,
            tenant_id=tenant_id,
            guild_id=guild_id,
            user_id=user_id,
            usage="chat",
        )
        return LLMModelInfo(
            model_profile_id=profile.id,
            model_provider=profile.provider,
            model_name=profile.model_name,
            model_owner_user_id=profile.user_id,
        )


class UserModelLLMGateway:
    def __init__(
        self,
        *,
        factory: UserModelGatewayFactory,
        fallback_gateway: LLMGateway | None = None,
    ):
        self.factory = factory
        self.fallback_gateway = fallback_gateway

    def generate_structured(self, **kwargs):
        profile_id = kwargs.get("model_profile_id")
        if profile_id is None:
            if self.fallback_gateway is None:
                raise UserModelGatewayError("model_profile_id is required for Discord BYOK runtime")
            return self.fallback_gateway.generate_structured(**kwargs)
        if not kwargs.get("model_guild_id") or not kwargs.get("model_owner_user_id"):
            raise UserModelGatewayError(
                "model_guild_id and model_owner_user_id are required for BYOK profile calls"
            )
        gateway = self.factory.build_chat_gateway(
            profile_id=profile_id,
            tenant_id=kwargs.get("model_tenant_id"),
            guild_id=kwargs.get("model_guild_id"),
            user_id=kwargs.get("model_owner_user_id"),
        )
        result = gateway.generate_structured(**kwargs)
        self.factory.repository.mark_used(profile_id=profile_id)
        return result

    def model_info(
        self,
        *,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> LLMModelInfo:
        if model_profile_id is None:
            if self.fallback_gateway is not None:
                return self.fallback_gateway.model_info()
            return LLMModelInfo(
                model_profile_id=None,
                model_provider="unconfigured",
                model_name="unconfigured",
                model_owner_user_id=None,
            )
        if not model_guild_id or not model_owner_user_id:
            raise UserModelGatewayError(
                "model_guild_id and model_owner_user_id are required for BYOK profile calls"
            )
        try:
            return self.factory.model_info(
                profile_id=model_profile_id,
                tenant_id=model_tenant_id,
                guild_id=model_guild_id,
                user_id=model_owner_user_id,
            )
        except (ModelProfileAccessError, ModelProfileNotFoundError) as exc:
            raise UserModelGatewayError(str(exc)) from exc


def _require_owner_scope(*, guild_id: str | None, user_id: str | None) -> None:
    if not guild_id or not user_id:
        raise UserModelGatewayError(
            "guild_id and user_id are required for user-level BYOK model profiles"
        )
