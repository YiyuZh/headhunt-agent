from sqlalchemy.orm import Session

from app.artifacts.repository import PostgresArtifactStore
from app.core.config import Settings, get_settings
from app.gateways.embeddings import OpenAIEmbeddingGateway
from app.gateways.llm import OpenAIResponsesLLMGateway
from app.harness.agent_harness import AgentHarness
from app.memory.gateway import DisabledMemoryGateway, PostgresMemoryGateway
from app.memory.vector_store import PostgresVectorMemoryStore
from app.model_profiles.gateway_factory import UserModelGatewayFactory, UserModelLLMGateway
from app.model_profiles.memory import UserModelMemoryGatewayRouter
from app.model_profiles.repository import ModelProfileRepository
from app.model_profiles.secrets import ModelSecretService
from app.runtime.action_executor import ActionExecutor
from app.runtime.action_gate import ActionGate
from app.runtime.graph_factory import RuntimeGraphFactory
from app.runtime.war_room import WarRoomNotifier
from app.storage.repositories import FeishuOutboxWriteRepository


class RuntimeDependencyError(RuntimeError):
    pass


def build_runtime_graph_factory(
    *,
    session: Session,
    settings: Settings | None = None,
) -> RuntimeGraphFactory:
    resolved_settings = settings or get_settings()
    outbox_writer = FeishuOutboxWriteRepository(session)
    war_room_notifier = WarRoomNotifier(
        outbox_writer=outbox_writer,
        default_chat_id=resolved_settings.feishu_default_chat_id,
    )
    harness = build_agent_harness(
        session=session,
        settings=resolved_settings,
        war_room_notifier=war_room_notifier,
    )
    action_gate = ActionGate(session, war_room_notifier=war_room_notifier)
    action_executor = ActionExecutor(
        session,
        settings=resolved_settings,
        outbox_writer=outbox_writer,
    )
    return RuntimeGraphFactory(
        settings=resolved_settings,
        agent_harness=harness,
        action_gate=action_gate,
        action_executor=action_executor,
    )


def build_agent_harness(
    *,
    session: Session,
    settings: Settings | None = None,
    war_room_notifier: WarRoomNotifier | None = None,
) -> AgentHarness:
    resolved_settings = settings or get_settings()
    fallback_memory_gateway = None
    try:
        embedding_gateway = build_embedding_gateway(resolved_settings)
    except RuntimeDependencyError as exc:
        memory_gateway = DisabledMemoryGateway(
            session=session,
            reason=f"embedding_runtime_disabled:{exc}",
        )
    else:
        vector_store = PostgresVectorMemoryStore(
            session,
            model=resolved_settings.embedding_model or "unknown",
            model_version=resolved_settings.embedding_model or "unknown",
        )
        fallback_memory_gateway = PostgresMemoryGateway(
            session=session,
            embedding_gateway=embedding_gateway,
            vector_store=vector_store,
        )
        memory_gateway = fallback_memory_gateway
    if resolved_settings.model_secret_encryption_key:
        memory_gateway = UserModelMemoryGatewayRouter(
            session=session,
            settings=resolved_settings,
            fallback_gateway=fallback_memory_gateway,
        )
    return AgentHarness(
        session=session,
        llm_gateway=build_llm_gateway(resolved_settings, session=session),
        memory_gateway=memory_gateway,
        artifact_store=PostgresArtifactStore(session),
        war_room_notifier=war_room_notifier,
    )


def build_llm_gateway(settings: Settings, *, session: Session | None = None):
    fallback_gateway = _build_legacy_llm_gateway(
        settings,
        required=not settings.model_secret_encryption_key,
    )
    if settings.model_secret_encryption_key:
        if session is None:
            if fallback_gateway is not None:
                return fallback_gateway
            raise RuntimeDependencyError(
                "a database session is required for user BYOK model profiles"
            )
        return UserModelLLMGateway(
            factory=UserModelGatewayFactory(
                repository=ModelProfileRepository(session),
                secret_service=ModelSecretService(
                    settings.model_secret_encryption_key.get_secret_value()
                ),
                provider_allowlist=_provider_allowlist(settings),
            ),
            fallback_gateway=fallback_gateway,
        )
    if fallback_gateway is not None:
        return fallback_gateway
    raise RuntimeDependencyError(
        "MODEL_SECRET_ENCRYPTION_KEY is required for user BYOK runtime; "
        "LLM_PROVIDER/LLM_MODEL/LLM_API_KEY are only a local debug fallback"
    )


def _build_legacy_llm_gateway(settings: Settings, *, required: bool = True):
    provider = (settings.llm_provider or "").lower()
    if not provider and not settings.llm_model and not settings.llm_api_key:
        return None
    if not required and not (provider and settings.llm_model and settings.llm_api_key):
        return None
    if provider not in {"openai", "openai_responses"}:
        raise RuntimeDependencyError("legacy LLM_PROVIDER must be openai or openai_responses")
    if not settings.llm_api_key or not settings.llm_model:
        raise RuntimeDependencyError(
            "legacy LLM_MODEL and LLM_API_KEY must be configured together"
        )
    return OpenAIResponsesLLMGateway(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
    )


def build_embedding_gateway(settings: Settings):
    provider = (settings.embedding_provider or "").lower()
    if provider != "openai":
        raise RuntimeDependencyError("EMBEDDING_PROVIDER must be openai for first-version runtime")
    api_key = settings.embedding_api_key_value()
    if not api_key or not settings.embedding_model:
        raise RuntimeDependencyError(
            "EMBEDDING_MODEL and EMBEDDING_API_KEY/OPENAI_API_KEY are required for embeddings"
        )
    return OpenAIEmbeddingGateway(
        api_key=api_key.get_secret_value(),
        model=settings.embedding_model,
    )


def _provider_allowlist(settings: Settings) -> set[str]:
    return {
        item.strip().lower()
        for item in settings.model_provider_allowlist.split(",")
        if item.strip()
    } or {"openai", "deepseek"}
