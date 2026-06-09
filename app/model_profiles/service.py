from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.gateways.llm import LLMGatewayError
from app.model_profiles.gateway_factory import UserModelGatewayError, UserModelGatewayFactory
from app.model_profiles.repository import ModelProfileRepository
from app.model_profiles.secrets import ModelSecretService
from app.schemas.common import CouncilMode
from app.schemas.context import ContextPack
from app.schemas.model_profiles import (
    CreateModelProfileRequest,
    ModelProfileSummary,
    ModelTestResult,
    ModelUsage,
)


class ModelProfileServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProfileCreateInput:
    tenant_id: str | None
    guild_id: str
    user_id: str
    provider: str
    model_name: str
    api_key: str
    display_name: str | None = None
    base_url: str | None = None
    usage: ModelUsage = "chat"
    make_default: bool = True


class ModelProfileService:
    def __init__(self, *, session: Session, settings: Settings):
        if settings.model_secret_encryption_key is None:
            raise ModelProfileServiceError("MODEL_SECRET_ENCRYPTION_KEY is required")
        self.session = session
        self.repository = ModelProfileRepository(session)
        self.secret_service = ModelSecretService(
            settings.model_secret_encryption_key.get_secret_value()
        )
        self.factory = UserModelGatewayFactory(
            repository=self.repository,
            secret_service=self.secret_service,
            provider_allowlist=_provider_allowlist(settings),
            timeout_seconds=2.0,
        )

    def create_profile(
        self,
        data: ModelProfileCreateInput,
        *,
        commit: bool = True,
    ) -> ModelProfileSummary:
        request = CreateModelProfileRequest(
            provider=data.provider,
            model_name=data.model_name,
            api_key=SecretStr(data.api_key),
            display_name=data.display_name,
            base_url=data.base_url,
            usage=data.usage,
            make_default=data.make_default,
        )
        encrypted = self.secret_service.encrypt_api_key(request.api_key.get_secret_value())
        summary = self.repository.create_profile(
            tenant_id=data.tenant_id,
            guild_id=data.guild_id,
            user_id=data.user_id,
            request=request,
            encrypted_api_key=encrypted,
        )
        if commit:
            self.session.commit()
        return summary

    def list_profiles(
        self,
        *,
        guild_id: str,
        user_id: str,
        usage: ModelUsage | None = None,
    ) -> list[ModelProfileSummary]:
        return self.repository.list_profiles(guild_id=guild_id, user_id=user_id, usage=usage)

    def use_profile(
        self,
        *,
        guild_id: str,
        user_id: str,
        display_name: str,
        usage: ModelUsage = "chat",
    ) -> ModelProfileSummary:
        profile = self.repository.get_active_profile_by_display_name(
            guild_id=guild_id,
            user_id=user_id,
            display_name=display_name,
            usage=usage,
        )
        summary = self.repository.set_default(
            profile_id=profile.id,
            guild_id=guild_id,
            user_id=user_id,
            usage=usage,
        )
        self.session.commit()
        return summary

    def revoke_profile(
        self,
        *,
        guild_id: str,
        user_id: str,
        display_name: str,
        usage: ModelUsage | None = None,
    ) -> ModelProfileSummary:
        profile = self.repository.get_active_profile_by_display_name(
            guild_id=guild_id,
            user_id=user_id,
            display_name=display_name,
            usage=usage,
        )
        summary = self.repository.revoke(
            profile_id=profile.id,
            guild_id=guild_id,
            user_id=user_id,
        )
        self.session.commit()
        return summary

    def test_profile(
        self,
        *,
        guild_id: str,
        user_id: str,
        display_name: str,
        usage: ModelUsage = "chat",
    ) -> ModelTestResult:
        profile = self.repository.get_active_profile_by_display_name(
            guild_id=guild_id,
            user_id=user_id,
            display_name=display_name,
            usage=usage,
        )
        status = "ok"
        message = "profile smoke check passed"
        try:
            if usage == "embedding":
                gateway = self.factory.build_embedding_gateway(
                    profile_id=profile.id,
                    tenant_id=profile.tenant_id,
                    guild_id=guild_id,
                    user_id=user_id,
                )
                vectors = gateway.embed_texts(["lietou model profile smoke"], purpose="model_test")
                if not vectors or not vectors[0]:
                    raise UserModelGatewayError("embedding response was empty")
            else:
                gateway = self.factory.build_chat_gateway(
                    profile_id=profile.id,
                    tenant_id=profile.tenant_id,
                    guild_id=guild_id,
                    user_id=user_id,
                )
                result = gateway.generate_structured(
                    agent_name="ModelProfileSmokeAgent",
                    context_pack=_smoke_context_pack(),
                    output_schema={
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                    },
                    schema_name="model_profile_smoke",
                    max_output_tokens=64,
                )
                if not isinstance(result.get("ok"), bool):
                    raise LLMGatewayError("model response did not match smoke schema")
        except Exception as exc:
            status = "failed"
            message = _safe_error_message(exc)
        self.repository.mark_test_result(profile_id=profile.id, status=status)
        self.session.commit()
        return ModelTestResult(
            profile_id=profile.id,
            provider=profile.provider,
            model_name=profile.model_name,
            status=status,
            message=message,
        )


def _smoke_context_pack() -> ContextPack:
    return ContextPack(
        thread_id="00000000-0000-4000-8000-000000000002",
        agent_name="ModelProfileSmokeAgent",
        task_brief="Return JSON: {\"ok\": true}.",
        node_goal="Verify user BYOK model profile.",
        council_mode=CouncilMode.triage,
        mode_reason="model profile smoke check",
    )


def _provider_allowlist(settings: Settings) -> set[str]:
    return {
        item.strip().lower()
        for item in settings.model_provider_allowlist.split(",")
        if item.strip()
    } or {"openai", "deepseek"}


def _safe_error_message(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if "sk-" in text:
        return "profile smoke check failed"
    return text[:300]
