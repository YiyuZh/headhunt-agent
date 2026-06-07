from app.core.config import Settings
from app.memory.gateway import DisabledMemoryGateway, PostgresMemoryGateway
from app.memory.vector_store import PostgresVectorMemoryStore
from app.model_profiles.gateway_factory import UserModelGatewayFactory
from app.model_profiles.repository import ModelProfileAccessError, ModelProfileNotFoundError
from app.model_profiles.secrets import ModelSecretService
from app.schemas.agent import AgentTask
from app.storage.models import UserModelProfile


class UserModelMemoryGatewayRouter:
    def __init__(self, *, session, settings: Settings, fallback_gateway=None):
        self.session = session
        self.settings = settings
        self.fallback_gateway = fallback_gateway

    def for_task(self, task: AgentTask):
        if not task.embedding_profile_id:
            if self.fallback_gateway is not None:
                return self.fallback_gateway
            return DisabledMemoryGateway(
                session=self.session,
                reason="embedding_runtime_disabled:no_user_embedding_profile",
            )
        if not task.model_guild_id or not task.model_owner_user_id:
            return DisabledMemoryGateway(
                session=self.session,
                reason="embedding_runtime_disabled:missing_discord_model_scope",
            )
        if self.settings.model_secret_encryption_key is None:
            return DisabledMemoryGateway(
                session=self.session,
                reason="embedding_runtime_disabled:model_secret_missing",
            )
        factory = UserModelGatewayFactory(
            repository=self._repository(),
            secret_service=ModelSecretService(
                self.settings.model_secret_encryption_key.get_secret_value()
            ),
            provider_allowlist=_provider_allowlist(self.settings),
        )
        try:
            embedding_gateway = factory.build_embedding_gateway(
                profile_id=task.embedding_profile_id,
                tenant_id=task.model_tenant_id,
                guild_id=task.model_guild_id,
                user_id=task.model_owner_user_id,
            )
            profile = self.session.get(UserModelProfile, task.embedding_profile_id)
        except (ModelProfileAccessError, ModelProfileNotFoundError, RuntimeError) as exc:
            return DisabledMemoryGateway(
                session=self.session,
                reason=f"embedding_runtime_disabled:{exc}",
            )
        model_name = getattr(profile, "model_name", None) or "user_embedding_profile"
        return PostgresMemoryGateway(
            session=self.session,
            embedding_gateway=embedding_gateway,
            vector_store=PostgresVectorMemoryStore(
                self.session,
                model=model_name,
                model_version=model_name,
            ),
        )

    def retrieve(self, *args, **kwargs):
        return DisabledMemoryGateway(
            session=self.session,
            reason="embedding_runtime_disabled:task_scope_required",
        ).retrieve(*args, **kwargs)

    def propose_update(self, *args, **kwargs):
        return None

    def _repository(self):
        from app.model_profiles.repository import ModelProfileRepository

        return ModelProfileRepository(self.session)


def _provider_allowlist(settings: Settings) -> set[str]:
    return {
        item.strip().lower()
        for item in settings.model_provider_allowlist.split(",")
        if item.strip()
    } or {"openai", "deepseek"}

