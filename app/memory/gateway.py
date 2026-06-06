import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.schemas.common import MemoryStatus
from app.schemas.memory import MemoryItem as MemoryItemSchema
from app.schemas.memory import MemoryRef
from app.storage.models import (
    MemoryItem as MemoryItemRecord,
)
from app.storage.models import (
    MemoryRetrievalAudit,
    MemoryUpdateProposal,
)

PII_ORDER = ("none", "low", "medium", "high")
LONG_TERM_SCOPE_FILTERS = (
    "tenant_id",
    "guild_id",
    "user_id",
    "project_id",
    "requisition_id",
    "candidate_id",
)


class MemoryPolicyError(RuntimeError):
    pass


class PostgresMemoryGateway:
    def __init__(
        self,
        *,
        session: Session | None,
        embedding_gateway,
        vector_store,
    ):
        self.session = session
        self.embedding_gateway = embedding_gateway
        self.vector_store = vector_store

    def retrieve(
        self,
        agent_name: str,
        task_brief: str,
        memory_scopes: list[str],
        filters: dict[str, Any],
        top_k: int,
        max_tokens: int,
        policy: dict[str, Any],
    ) -> list[MemoryRef]:
        allowed_scopes = _intersect_scopes(
            requested=memory_scopes,
            allowed=policy.get("allowed_memory_scopes", []),
        )
        effective_top_k = max(0, min(top_k, int(policy.get("max_memory_items", top_k) or 0)))
        effective_max_tokens = max(
            0,
            min(max_tokens, int(policy.get("max_context_tokens", max_tokens) or 0)),
        )

        if not allowed_scopes or effective_top_k == 0 or effective_max_tokens == 0:
            self._audit(
                agent_name=agent_name,
                task_brief=task_brief,
                allowed_scopes=allowed_scopes,
                filters=filters,
                candidate_refs=[],
                selected_refs=[],
                excluded_reason=["no_allowed_scope_or_budget"],
                token_estimate=0,
            )
            return []
        scoped_allowed_scopes, scope_exclusions = _scopes_allowed_by_filters(
            allowed_scopes,
            filters,
        )
        if not scoped_allowed_scopes:
            self._audit(
                agent_name=agent_name,
                task_brief=task_brief,
                allowed_scopes=allowed_scopes,
                filters=filters,
                candidate_refs=[],
                selected_refs=[],
                excluded_reason=scope_exclusions,
                token_estimate=0,
            )
            return []

        query_embedding = self.embedding_gateway.embed_texts(
            [task_brief],
            purpose=f"memory_retrieval:{agent_name}",
        )[0]
        store_filters = {
            **filters,
            "allowed_scopes": scoped_allowed_scopes,
            "max_pii_level": _policy_pii_level(policy),
            "agent_memory_owner": policy.get("agent_name") or agent_name,
            "now": filters.get("now") or datetime.now(UTC),
        }
        candidates = self.vector_store.search(
            query_embedding=query_embedding,
            filters=store_filters,
            top_k=max(effective_top_k * 3, effective_top_k),
        )
        selected, excluded_reason = _select_refs(
            candidates,
            allowed_scopes=scoped_allowed_scopes,
            max_pii_level=_policy_pii_level(policy),
            top_k=effective_top_k,
            max_tokens=effective_max_tokens,
        )
        self._audit(
            agent_name=agent_name,
            task_brief=task_brief,
            allowed_scopes=scoped_allowed_scopes,
            filters=filters,
            candidate_refs=candidates,
            selected_refs=selected,
            excluded_reason=[*scope_exclusions, *excluded_reason],
            token_estimate=sum(ref.tokens_estimate for ref in selected),
        )
        return selected

    def propose_update(self, agent_name: str, item: MemoryItemSchema) -> str:
        if self.session is None:
            raise MemoryPolicyError("memory update proposals require a database session")
        status = (
            MemoryStatus.active
            if item.scope.value == "RunMemory"
            else MemoryStatus.pending_review
        )
        stored_item = item.model_copy(
            update={
                "owner_agent": item.owner_agent or agent_name,
                "status": status,
            }
        )
        embedding = self.embedding_gateway.embed_texts(
            [stored_item.summary],
            purpose=f"memory_update:{agent_name}:{stored_item.scope.value}",
        )[0]
        self.vector_store.upsert(stored_item, embedding)
        if item.scope.value == "RunMemory":
            self.session.flush()
            return str(item.memory_id)

        proposal = MemoryUpdateProposal(
            memory_id=item.memory_id,
            proposal_type="create",
            payload_ref=item.content_ref,
            status="pending",
        )
        self.session.add(proposal)
        self.session.flush()
        return str(proposal.id)

    def approve_update(self, proposal_id: str, reviewer: str) -> None:
        if self.session is None:
            raise MemoryPolicyError("memory approvals require a database session")
        proposal = self.session.get(MemoryUpdateProposal, UUID(proposal_id))
        if proposal is None:
            raise KeyError(proposal_id)
        memory = self.session.get(MemoryItemRecord, proposal.memory_id)
        if memory is None:
            raise KeyError(str(proposal.memory_id))

        proposal.status = "approved"
        proposal.approver = {"reviewer": reviewer}
        proposal.decided_at = datetime.now(UTC)
        memory.status = "active"
        self.session.flush()

    def revoke_update(self, memory_id: str, reviewer: str, reason: str) -> None:
        if self.session is None:
            raise MemoryPolicyError("memory revocation requires a database session")
        memory = self.session.get(MemoryItemRecord, UUID(memory_id))
        if memory is None:
            raise KeyError(memory_id)
        memory.status = "revoked"
        proposal = MemoryUpdateProposal(
            memory_id=memory.id,
            proposal_type="revoke",
            payload_ref=memory.content_ref,
            status="approved",
            approver={"reviewer": reviewer},
            decision_reason=reason,
            decided_at=datetime.now(UTC),
        )
        self.session.add(proposal)
        self.session.flush()

    def _audit(
        self,
        *,
        agent_name: str,
        task_brief: str,
        allowed_scopes: list[str],
        filters: dict[str, Any],
        candidate_refs: list[MemoryRef],
        selected_refs: list[MemoryRef],
        excluded_reason: list[str],
        token_estimate: int,
    ) -> None:
        if self.session is None:
            return
        self.session.add(
            MemoryRetrievalAudit(
                thread_id=filters.get("thread_id"),
                run_id=filters.get("run_id"),
                agent_name=agent_name,
                task_brief_hash=hashlib.sha256(task_brief.encode("utf-8")).hexdigest(),
                allowed_scopes=allowed_scopes,
                filters=_json_safe(filters),
                candidate_memory_ids=[str(ref.memory_id) for ref in candidate_refs],
                selected_memory_refs=[
                    ref.model_dump(mode="json") for ref in selected_refs
                ],
                excluded_reason=excluded_reason,
                token_estimate=token_estimate,
            )
        )
        self.session.flush()


def _intersect_scopes(*, requested: list[str], allowed: list[str]) -> list[str]:
    allowed_set = {_scope_value(scope) for scope in allowed}
    return [_scope_value(scope) for scope in requested if _scope_value(scope) in allowed_set]


def _scopes_allowed_by_filters(
    allowed_scopes: list[str],
    filters: dict[str, Any],
) -> tuple[list[str], list[str]]:
    scoped: list[str] = []
    excluded: list[str] = []
    has_long_term_boundary = any(filters.get(key) for key in LONG_TERM_SCOPE_FILTERS)
    has_thread_boundary = bool(filters.get("thread_id"))
    for scope in allowed_scopes:
        if scope == "RunMemory":
            if has_thread_boundary:
                scoped.append(scope)
            else:
                excluded.append("RunMemory:missing_thread_id_scope_filter")
            continue
        if has_long_term_boundary:
            scoped.append(scope)
        else:
            excluded.append(f"{scope}:missing_tenant_or_business_scope_filter")
    return scoped, excluded


def _select_refs(
    candidates: list[MemoryRef],
    *,
    allowed_scopes: list[str],
    max_pii_level: str,
    top_k: int,
    max_tokens: int,
) -> tuple[list[MemoryRef], list[str]]:
    allowed_scope_set = set(allowed_scopes)
    selected: list[MemoryRef] = []
    excluded: list[str] = []
    seen: set[tuple[str, str]] = set()
    token_total = 0

    for ref in sorted(candidates, key=lambda item: item.relevance_score, reverse=True):
        scope = _scope_value(ref.scope)
        if scope not in allowed_scope_set:
            excluded.append(f"{ref.memory_id}:scope_not_allowed")
            continue
        if not _pii_allowed(_scope_value(ref.pii_level), max_pii_level):
            excluded.append(f"{ref.memory_id}:pii_not_allowed")
            continue
        identity = (ref.content_ref, "")
        if identity in seen:
            excluded.append(f"{ref.memory_id}:duplicate")
            continue
        if token_total + ref.tokens_estimate > max_tokens:
            excluded.append(f"{ref.memory_id}:token_budget_exceeded")
            continue
        selected.append(ref)
        seen.add(identity)
        token_total += ref.tokens_estimate
        if len(selected) >= top_k:
            break

    return selected, excluded


def _policy_pii_level(policy: dict[str, Any]) -> str:
    return str(policy.get("pii_access_level") or policy.get("max_pii_level") or "none")


def _pii_allowed(actual: str, max_pii_level: str) -> bool:
    if actual not in PII_ORDER or max_pii_level not in PII_ORDER:
        return actual == "none"
    return PII_ORDER.index(actual) <= PII_ORDER.index(max_pii_level)


def _scope_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _json_safe(values: dict[str, Any]) -> dict[str, Any]:
    safe = {}
    for key, value in values.items():
        if isinstance(value, UUID):
            safe[key] = str(value)
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
        elif isinstance(value, (str, int, float, bool, list, dict)) or value is None:
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe
