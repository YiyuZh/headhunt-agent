import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.policy.engine import PolicyEngine
from app.schemas.council import CouncilDeliberateRequest, TaskPlan
from app.schemas.tasks import TaskAuthorizeRequest, TaskAuthorizeResponse
from app.storage.models import GraphThread
from app.storage.repositories import FeishuOutboxWriteRepository, OutboxPayloadConflictError


class TaskAuthorizationConflictError(RuntimeError):
    pass


class TaskAuthorizationService:
    def __init__(
        self,
        session: Session,
        *,
        policy_engine: PolicyEngine | None = None,
        outbox_writer: FeishuOutboxWriteRepository | None = None,
    ):
        self.session = session
        self.policy_engine = policy_engine or PolicyEngine()
        self.outbox_writer = outbox_writer or FeishuOutboxWriteRepository(session)

    def authorize(self, request: TaskAuthorizeRequest) -> TaskAuthorizeResponse:
        source_ref = _resolve_source_ref(request)
        thread_id = request.thread_id or uuid5(
            NAMESPACE_URL,
            f"lietou:task-authorization:thread:{request.source}:{source_ref}",
        )
        task_plan = self.policy_engine.create_task_plan(
            CouncilDeliberateRequest(
                request_text=request.request_text,
                source=request.source,
                thread_id=thread_id,
            )
        )
        task_plan = task_plan.model_copy(
            update={
                "task_id": uuid5(
                    NAMESPACE_URL,
                    f"lietou:task-authorization:task:{request.source}:{source_ref}",
                )
            }
        )
        if not request.approved:
            return self._response(
                task_plan,
                status="rejected",
                source_ref=source_ref,
                idempotency_key=None,
                outbox_payload_ref=None,
                model_profile_id=request.model_profile_id,
                next_actions=["任务未授权，未创建 graph_dispatch outbox。"],
            )

        idempotency_key = f"task_authorize:{request.source}:{source_ref}"
        payload = _build_graph_dispatch_payload(
            request=request,
            task_plan=task_plan,
            source_ref=source_ref,
        )
        content_ref = (
            f"artifact://task-authorization/{task_plan.thread_id}/{task_plan.task_id}/v1"
        )

        try:
            with self.session.begin():
                self._ensure_thread_can_be_used(
                    task_plan=task_plan,
                    source=request.source,
                    source_ref=source_ref,
                )
                self._upsert_graph_thread(
                    task_plan=task_plan,
                    source=request.source,
                    source_ref=source_ref,
                    approver=request.approver,
                )
                outbox_payload_ref = self.outbox_writer.enqueue_json(
                    kind="graph_dispatch",
                    idempotency_key=idempotency_key,
                    payload=payload,
                    thread_id=task_plan.thread_id,
                    content_ref=content_ref,
                )
        except OutboxPayloadConflictError as exc:
            raise TaskAuthorizationConflictError(str(exc)) from exc

        return self._response(
            task_plan,
            status="queued",
            source_ref=source_ref,
            idempotency_key=idempotency_key,
            outbox_payload_ref=outbox_payload_ref,
            model_profile_id=request.model_profile_id,
            next_actions=[
                "已写入 durable graph_dispatch outbox。",
                "启动 lietou-outbox-worker 后会异步进入 headhunter_war_room_graph。",
                f"本次会审模式：{task_plan.council_mode.value}。",
            ],
        )

    def _ensure_thread_can_be_used(
        self,
        *,
        task_plan: TaskPlan,
        source: str,
        source_ref: str,
    ) -> None:
        session_get = getattr(self.session, "get", None)
        if not callable(session_get):
            return
        existing = session_get(GraphThread, task_plan.thread_id)
        if existing is None:
            return
        if existing.source != source or existing.source_ref != source_ref:
            raise TaskAuthorizationConflictError(
                "thread_id already belongs to a different source/source_ref"
            )

    def _upsert_graph_thread(
        self,
        *,
        task_plan: TaskPlan,
        source: str,
        source_ref: str,
        approver: dict,
    ) -> None:
        self.session.execute(
            pg_insert(GraphThread)
            .values(
                id=task_plan.thread_id,
                source=source,
                source_ref=source_ref,
                task_type=task_plan.task_type,
                council_mode=task_plan.council_mode.value,
                mode_reason=task_plan.mode_reason,
                status="queued",
                state_summary={
                    "authorization_status": "authorized",
                    "task_id": str(task_plan.task_id),
                    "request_summary": task_plan.request_text[:240],
                    "council_mode": task_plan.council_mode.value,
                    "mode_reason": task_plan.mode_reason,
                    "approver": approver,
                },
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        self.session.flush()

    @staticmethod
    def _response(
        task_plan: TaskPlan,
        *,
        status: Literal["queued", "rejected"],
        source_ref: str,
        idempotency_key: str | None,
        outbox_payload_ref: str | None,
        model_profile_id: UUID | None,
        next_actions: list[str],
    ) -> TaskAuthorizeResponse:
        return TaskAuthorizeResponse(
            status=status,
            task_id=task_plan.task_id,
            thread_id=task_plan.thread_id,
            source_ref=source_ref,
            task_type=task_plan.task_type,
            council_mode=task_plan.council_mode,
            mode_reason=task_plan.mode_reason,
            required_agents=task_plan.required_agents,
            optional_agents=task_plan.optional_agents,
            user_forced_full_council=task_plan.user_forced_full_council,
            model_profile_id=model_profile_id,
            idempotency_key=idempotency_key,
            outbox_payload_ref=outbox_payload_ref,
            next_actions=next_actions,
        )


def _build_graph_dispatch_payload(
    *,
    request: TaskAuthorizeRequest,
    task_plan: TaskPlan,
    source_ref: str,
) -> dict:
    return {
        "thread_id": str(task_plan.thread_id),
        "task_id": str(task_plan.task_id),
        "source": request.source,
        "source_ref": source_ref,
        "user_input": task_plan.request_text,
        "task_type": task_plan.task_type,
        "council_mode": task_plan.council_mode.value,
        "mode_reason": task_plan.mode_reason,
        "required_agents": task_plan.required_agents,
        "optional_agents": task_plan.optional_agents,
        "user_forced_full_council": task_plan.user_forced_full_council,
        "model_profile_id": str(request.model_profile_id) if request.model_profile_id else None,
        "model_owner_user_id": request.model_owner_user_id,
        "model_guild_id": request.model_guild_id,
        "model_tenant_id": request.model_tenant_id,
        "embedding_profile_id": (
            str(request.embedding_profile_id) if request.embedding_profile_id else None
        ),
        "authorization": {
            "status": "authorized",
            "approver": request.approver,
            "authorized_at": datetime.now(UTC).isoformat(),
        },
    }


def _resolve_source_ref(request: TaskAuthorizeRequest) -> str:
    if request.source_ref:
        return request.source_ref

    seed = {
        "request_text": " ".join(request.request_text.split()),
        "source": request.source,
        "thread_id": str(request.thread_id) if request.thread_id else None,
    }
    raw_seed = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:24]
    return f"request:{digest}"
