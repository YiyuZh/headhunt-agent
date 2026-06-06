from sqlalchemy.orm import Session

from app.artifacts.repository import PostgresArtifactStore
from app.core.config import Settings, get_settings
from app.gateways.embeddings import OpenAIEmbeddingGateway
from app.gateways.llm import OpenAIResponsesLLMGateway
from app.harness.agent_harness import AgentHarness
from app.memory.gateway import PostgresMemoryGateway
from app.memory.vector_store import PostgresVectorMemoryStore
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
    embedding_gateway = build_embedding_gateway(resolved_settings)
    vector_store = PostgresVectorMemoryStore(
        session,
        model=resolved_settings.embedding_model or "unknown",
        model_version=resolved_settings.embedding_model or "unknown",
    )
    memory_gateway = PostgresMemoryGateway(
        session=session,
        embedding_gateway=embedding_gateway,
        vector_store=vector_store,
    )
    return AgentHarness(
        session=session,
        llm_gateway=build_llm_gateway(resolved_settings),
        memory_gateway=memory_gateway,
        artifact_store=PostgresArtifactStore(session),
        war_room_notifier=war_room_notifier,
    )


def build_llm_gateway(settings: Settings):
    provider = (settings.llm_provider or "").lower()
    if provider not in {"openai", "openai_responses"}:
        raise RuntimeDependencyError("LLM_PROVIDER must be openai or openai_responses")
    if not settings.llm_api_key or not settings.llm_model:
        raise RuntimeDependencyError("LLM_MODEL and LLM_API_KEY are required for real runtime")
    return OpenAIResponsesLLMGateway(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
    )


def build_embedding_gateway(settings: Settings):
    provider = (settings.embedding_provider or "").lower()
    if provider != "openai":
        raise RuntimeDependencyError("EMBEDDING_PROVIDER must be openai for first-version runtime")
    if not settings.llm_api_key or not settings.embedding_model:
        raise RuntimeDependencyError("EMBEDDING_MODEL and LLM_API_KEY are required for embeddings")
    return OpenAIEmbeddingGateway(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.embedding_model,
    )
