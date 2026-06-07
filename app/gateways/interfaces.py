from typing import Any, Protocol
from uuid import UUID

from app.schemas.context import ContextPack
from app.schemas.memory import MemoryItem, MemoryRef


class FeishuGateway(Protocol):
    def send_card(self, chat_id: str, card: dict, idempotency_key: str) -> str: ...
    def update_card(self, open_message_id: str, card: dict, idempotency_key: str) -> str: ...


class FeishuBitableGateway(Protocol):
    def batch_create(
        self,
        app_token: str,
        table_id: str,
        records: list[dict],
        client_token: str,
    ) -> list[str]: ...


class EmbeddingGateway(Protocol):
    def embed_texts(self, texts: list[str], purpose: str) -> list[list[float]]: ...


class VectorMemoryStore(Protocol):
    def upsert(self, item: MemoryItem, embedding: list[float]) -> str: ...

    def search(
        self,
        query_embedding: list[float],
        filters: dict,
        top_k: int,
    ) -> list[MemoryRef]: ...


class MemoryGateway(Protocol):
    def retrieve(
        self,
        agent_name: str,
        task_brief: str,
        memory_scopes: list[str],
        filters: dict[str, Any],
        top_k: int,
        max_tokens: int,
        policy: dict[str, Any],
    ) -> list[MemoryRef]: ...

    def propose_update(self, agent_name: str, item: MemoryItem) -> str: ...
    def approve_update(self, proposal_id: str, reviewer: str) -> None: ...
    def revoke_update(self, memory_id: str, reviewer: str, reason: str) -> None: ...


class LLMGateway(Protocol):
    def generate_structured(
        self,
        *,
        agent_name: str,
        context_pack: ContextPack,
        output_schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int,
        model_profile_id: UUID | None = None,
        model_owner_user_id: str | None = None,
        model_guild_id: str | None = None,
        model_tenant_id: str | None = None,
    ) -> dict[str, Any]: ...
