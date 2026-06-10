from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.feishu.callbacks import (
    FeishuCallbackPayloadError,
    VerifiedFeishuCallback,
    build_event_dedupe_key,
    build_event_idempotency_key,
    is_model_profile_setup_callback,
    is_task_confirmation_callback,
    parse_card_action_command,
    parse_model_profile_setup_command,
    parse_task_confirmation_command,
)
from app.feishu.cards import (
    build_model_setup_required_card,
    build_model_setup_saved_card,
    build_task_confirmation_card,
)
from app.feishu.task_intake import (
    build_graph_dispatch_payload,
    create_task_plan,
    model_setup_card_ref,
    parse_task_intake,
    task_confirmation_card_ref,
    task_payload_ref,
)
from app.model_profiles.repository import ModelProfileNotFoundError, ModelProfileRepository
from app.model_profiles.service import (
    ModelProfileCreateInput,
    ModelProfileService,
    ModelProfileServiceError,
)
from app.policy.engine import PolicyEngine
from app.storage.models import GraphThread
from app.storage.repositories import (
    DuplicateCardActionError,
    DuplicateEventError,
    FeishuCardActionRepository,
    FeishuEventRepository,
    FeishuModelSetupRepository,
    FeishuOutboxWriteRepository,
    FeishuTaskConfirmationRepository,
    InvalidHumanApprovalError,
    PayloadRepository,
)


@dataclass(frozen=True)
class FeishuEnqueueResult:
    status: str
    idempotency_key: str
    payload_ref: str
    message: str | None = None


class FeishuCallbackService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        policy_engine: PolicyEngine | None = None,
    ):
        self.session = session
        self.settings = settings or Settings()
        self.policy_engine = policy_engine or PolicyEngine()

    def enqueue_event(self, callback: VerifiedFeishuCallback) -> FeishuEnqueueResult:
        payload_ref = build_payload_ref(callback)
        idempotency_key = build_event_idempotency_key(callback)
        try:
            intake = parse_task_intake(callback.payload, tenant_key=callback.tenant_key)
        except ValueError as exc:
            raise FeishuCallbackPayloadError(str(exc)) from exc
        chat_id = intake.chat_id or self.settings.feishu_default_chat_id
        if not chat_id:
            raise FeishuCallbackPayloadError("Feishu task intake requires chat_id")

        try:
            with self.session.begin():
                payload_repository = PayloadRepository(self.session)
                payload_repository.store_json_payload(
                    content_ref=payload_ref,
                    payload=callback.payload,
                    raw_text=callback.raw_body.decode("utf-8"),
                    sha256=callback.payload_hash,
                )
                model_profile = self._default_model_profile(intake)
                if model_profile is None:
                    card_payload_ref = self._store_model_setup_card(
                        payload_repository=payload_repository,
                        intake=intake,
                        chat_id=chat_id,
                        event_payload_ref=payload_ref,
                    )
                    thread_state = "pending_model_profile"
                    thread_id = intake.thread_id
                else:
                    card_payload_ref = self._store_task_confirmation_card(
                        payload_repository=payload_repository,
                        intake=intake,
                        chat_id=chat_id,
                        model_profile_id=model_profile.id,
                    )
                    thread_state = "pending_task_confirmation"
                    thread_id = intake.thread_id
                self._upsert_pending_intake_thread(intake, status=thread_state)
                FeishuEventRepository(self.session).record_event_and_enqueue(
                    event_id=callback.event_id or callback.payload_hash,
                    event_type=callback.event_type,
                    tenant_key=callback.tenant_key,
                    app_id=callback.app_id,
                    message_id=callback.message_id,
                    dedupe_key=build_event_dedupe_key(callback),
                    idempotency_key=idempotency_key,
                    payload_hash=callback.payload_hash,
                    payload_ref=payload_ref,
                    outbox_kind="card_send",
                    outbox_payload_ref=card_payload_ref,
                    thread_id=thread_id,
                )
        except DuplicateEventError:
            return FeishuEnqueueResult(
                status="duplicate",
                idempotency_key=idempotency_key,
                payload_ref=payload_ref,
            )

        return FeishuEnqueueResult(
            status="queued",
            idempotency_key=idempotency_key,
            payload_ref=payload_ref,
        )

    def _default_model_profile(self, intake):
        if not intake.model_owner_user_id:
            return None
        try:
            model_profile = ModelProfileRepository(self.session).get_default_profile(
                guild_id=intake.model_guild_id,
                user_id=intake.model_owner_user_id,
                usage="chat",
            )
        except ModelProfileNotFoundError:
            return None
        if not model_profile.encrypted_api_key:
            return None
        return model_profile

    def enqueue_card_action(self, callback: VerifiedFeishuCallback) -> FeishuEnqueueResult:
        if is_task_confirmation_callback(callback.payload):
            return self._enqueue_task_confirmation(callback)
        if is_model_profile_setup_callback(callback.payload):
            return self._enqueue_model_profile_setup(callback)

        payload_ref = build_payload_ref(callback)
        command = parse_card_action_command(callback, payload_ref=payload_ref)
        approver = build_approver(callback.payload)

        try:
            with self.session.begin():
                PayloadRepository(self.session).store_json_payload(
                    content_ref=payload_ref,
                    payload=callback.payload,
                    raw_text=callback.raw_body.decode("utf-8"),
                    sha256=callback.payload_hash,
                )
                FeishuCardActionRepository(self.session).record_action_and_enqueue_resume(
                    event_id=command.event_id,
                    thread_id=command.thread_id,
                    action_id=command.action_id,
                    interrupt_id=command.interrupt_id,
                    idempotency_key=command.idempotency_key,
                    open_message_id=command.open_message_id,
                    open_chat_id=command.open_chat_id,
                    card_update_token_ref=command.card_update_token_ref,
                    operator_open_id=command.operator_open_id,
                    decision=command.decision,
                    edited_payload_ref=payload_ref if command.edited_payload else None,
                    payload_ref=payload_ref,
                    approver=approver,
                )
        except DuplicateCardActionError:
            return FeishuEnqueueResult(
                status="duplicate",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
            )
        except InvalidHumanApprovalError as exc:
            raise FeishuCallbackPayloadError(str(exc)) from exc

        return FeishuEnqueueResult(
            status="queued",
            idempotency_key=command.idempotency_key,
            payload_ref=payload_ref,
        )

    def _enqueue_task_confirmation(
        self,
        callback: VerifiedFeishuCallback,
    ) -> FeishuEnqueueResult:
        payload_ref = build_payload_ref(callback)
        command = parse_task_confirmation_command(callback, payload_ref=payload_ref)

        try:
            with self.session.begin():
                payload_repository = PayloadRepository(self.session)
                payload_repository.store_json_payload(
                    content_ref=payload_ref,
                    payload=callback.payload,
                    raw_text=callback.raw_body.decode("utf-8"),
                    sha256=callback.payload_hash,
                )
                task_payload = payload_repository.get_json_payload(command.task_payload_ref)
                _validate_task_confirmation_payload(task_payload, command)
                FeishuTaskConfirmationRepository(self.session).record_confirmation(
                    event_id=command.event_id,
                    thread_id=command.thread_id,
                    task_id=command.task_id,
                    idempotency_key=command.idempotency_key,
                    open_message_id=command.open_message_id,
                    open_chat_id=command.open_chat_id,
                    card_update_token_ref=command.card_update_token_ref,
                    operator_open_id=command.operator_open_id,
                    decision=command.decision,
                    status="queued" if command.decision == "approve" else "received",
                )
                if command.decision == "approve":
                    approved_payload_ref = _approved_task_payload_ref(command.task_payload_ref)
                    approved_payload = _authorized_task_payload(
                        task_payload,
                        operator_open_id=command.operator_open_id,
                    )
                    self._upsert_graph_thread(approved_payload)
                    FeishuOutboxWriteRepository(self.session).enqueue_json(
                        kind="graph_dispatch",
                        idempotency_key=f"graph_dispatch:{command.idempotency_key}",
                        payload=approved_payload,
                        thread_id=command.thread_id,
                        content_ref=approved_payload_ref,
                    )
        except DuplicateCardActionError:
            return FeishuEnqueueResult(
                status="duplicate",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
            )

        return FeishuEnqueueResult(
            status="queued" if command.decision == "approve" else "rejected",
            idempotency_key=command.idempotency_key,
            payload_ref=payload_ref,
        )

    def _enqueue_model_profile_setup(
        self,
        callback: VerifiedFeishuCallback,
    ) -> FeishuEnqueueResult:
        payload_ref = build_payload_ref(callback)
        command = parse_model_profile_setup_command(callback, payload_ref=payload_ref)
        if not _operator_matches_model_owner(command):
            return FeishuEnqueueResult(
                status="model_setup_failed",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
                message="只能由原任务发起人配置该任务的模型。",
            )

        try:
            model_service = ModelProfileService(session=self.session, settings=self.settings)
        except ModelProfileServiceError as exc:
            return FeishuEnqueueResult(
                status="model_setup_failed",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
                message=_safe_model_setup_error(exc),
            )

        try:
            with self.session.begin():
                setup_repository = FeishuModelSetupRepository(self.session)
                if setup_repository.is_duplicate(command.idempotency_key):
                    return FeishuEnqueueResult(
                        status="duplicate",
                        idempotency_key=command.idempotency_key,
                        payload_ref=payload_ref,
                    )
                payload_repository = PayloadRepository(self.session)
                redacted_payload = _redact_model_setup_payload(callback.payload)
                redacted_raw = _json_payload_text(redacted_payload)
                payload_repository.store_json_payload(
                    content_ref=payload_ref,
                    payload=redacted_payload,
                    raw_text=redacted_raw,
                    sha256=_sha256(redacted_raw),
                )
                original_payload = payload_repository.get_json_payload(command.event_payload_ref)
                intake = parse_task_intake(original_payload, tenant_key=callback.tenant_key)
                _validate_model_setup_intake(intake, command)
                profile = model_service.create_profile(
                    ModelProfileCreateInput(
                        tenant_id=callback.tenant_key,
                        guild_id=command.chat_id,
                        user_id=command.model_owner_user_id,
                        provider=command.provider,
                        model_name=command.model_name,
                        api_key=command.api_key,
                        display_name=command.display_name,
                        base_url=command.base_url,
                        usage="chat",
                        make_default=command.make_default,
                    ),
                    commit=False,
                )
                setup_repository.record_setup(
                    event_id=command.event_id,
                    thread_id=command.thread_id,
                    profile_id=profile.id,
                    idempotency_key=command.idempotency_key,
                    open_message_id=command.open_message_id,
                    open_chat_id=command.open_chat_id,
                    card_update_token_ref=command.card_update_token_ref,
                    operator_open_id=command.operator_open_id,
                    payload_ref=payload_ref,
                )
                card_payload_ref = self._store_task_confirmation_card(
                    payload_repository=payload_repository,
                    intake=intake,
                    chat_id=command.chat_id,
                    model_profile_id=profile.id,
                )
                FeishuOutboxWriteRepository(self.session).enqueue_json(
                    kind="card_send",
                    idempotency_key=f"task_confirmation_after_model_setup:{command.idempotency_key}",
                    payload=payload_repository.get_json_payload(card_payload_ref),
                    thread_id=command.thread_id,
                    content_ref=card_payload_ref,
                )
                if command.open_message_id:
                    FeishuOutboxWriteRepository(self.session).enqueue_json(
                        kind="card_update",
                        idempotency_key=f"model_setup_card_saved:{command.idempotency_key}",
                        payload={
                            "open_message_id": command.open_message_id,
                            "card": build_model_setup_saved_card(
                                thread_id=command.thread_id,
                                provider=command.provider,
                                model_name=command.model_name,
                            ),
                        },
                        thread_id=command.thread_id,
                    )
        except DuplicateCardActionError:
            return FeishuEnqueueResult(
                status="duplicate",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
            )
        except Exception as exc:
            return FeishuEnqueueResult(
                status="model_setup_failed",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
                message=_safe_model_setup_error(exc),
            )

        return FeishuEnqueueResult(
            status="model_setup_saved",
            idempotency_key=command.idempotency_key,
            payload_ref=payload_ref,
            message="模型已保存并设为默认，已继续发送任务确认卡。",
        )

    def _store_task_confirmation_card(
        self,
        *,
        payload_repository: PayloadRepository,
        intake,
        chat_id: str,
        model_profile_id: UUID,
    ) -> str:
        task_plan = create_task_plan(intake, self.policy_engine)
        graph_payload = build_graph_dispatch_payload(
            intake=intake,
            task_plan=task_plan,
            model_profile_id=model_profile_id,
        )
        graph_payload_ref = task_payload_ref(task_plan.thread_id, task_plan.task_id)
        graph_raw = _json_payload_text(graph_payload)
        payload_repository.store_json_payload(
            content_ref=graph_payload_ref,
            payload=graph_payload,
            raw_text=graph_raw,
            sha256=_sha256(graph_raw),
        )
        card = build_task_confirmation_card(
            thread_id=task_plan.thread_id,
            task_id=task_plan.task_id,
            task_payload_ref=graph_payload_ref,
            source_ref=intake.source_ref,
            request_text=task_plan.request_text,
            task_type=task_plan.task_type,
            council_mode=task_plan.council_mode.value,
            mode_reason=task_plan.mode_reason,
            field_sources=intake.field_sources,
            missing_fields=intake.missing_fields,
            assumptions=intake.assumptions,
        )
        card_payload = {"chat_id": chat_id, "card": card}
        card_payload_ref = task_confirmation_card_ref(task_plan.thread_id, task_plan.task_id)
        card_raw = _json_payload_text(card_payload)
        payload_repository.store_json_payload(
            content_ref=card_payload_ref,
            payload=card_payload,
            raw_text=card_raw,
            sha256=_sha256(card_raw),
        )
        return card_payload_ref

    def _store_model_setup_card(
        self,
        *,
        payload_repository: PayloadRepository,
        intake,
        chat_id: str,
        event_payload_ref: str,
    ) -> str:
        card_payload = {
            "chat_id": chat_id,
            "card": build_model_setup_required_card(
                thread_id=intake.thread_id,
                source_ref=intake.source_ref,
                event_payload_ref=event_payload_ref,
                request_text=intake.request_text,
                chat_id=chat_id,
                user_id=intake.model_owner_user_id,
                user_id_type=intake.model_owner_id_type,
            ),
        }
        card_payload_ref = model_setup_card_ref(intake.source_ref)
        card_raw = _json_payload_text(card_payload)
        payload_repository.store_json_payload(
            content_ref=card_payload_ref,
            payload=card_payload,
            raw_text=card_raw,
            sha256=_sha256(card_raw),
        )
        return card_payload_ref

    def _upsert_pending_intake_thread(self, intake, *, status: str) -> None:
        insert_stmt = pg_insert(GraphThread).values(
            id=intake.thread_id,
            source="feishu",
            source_ref=intake.source_ref,
            task_type="task_intake",
            council_mode=None,
            mode_reason=None,
            status="queued",
            state_summary={
                "authorization_status": status,
                "task_id": str(intake.task_id),
                "request_summary": intake.request_text[:240],
                "source_ref": intake.source_ref,
                "message_id": intake.message_id,
                "chat_id": intake.chat_id,
                "model_owner_user_id": intake.model_owner_user_id,
                "model_owner_id_type": intake.model_owner_id_type,
            },
        )
        self.session.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "source": insert_stmt.excluded.source,
                    "source_ref": insert_stmt.excluded.source_ref,
                    "state_summary": insert_stmt.excluded.state_summary,
                },
            )
        )
        self.session.flush()

    def _upsert_graph_thread(self, task_payload: dict[str, Any]) -> None:
        insert_stmt = pg_insert(GraphThread).values(
            id=UUID(task_payload["thread_id"]),
            source="feishu",
            source_ref=task_payload["source_ref"],
            task_type=task_payload["task_type"],
            council_mode=task_payload["council_mode"],
            mode_reason=task_payload["mode_reason"],
            status="queued",
            state_summary={
                "authorization_status": "authorized",
                "task_id": task_payload["task_id"],
                "request_summary": task_payload["user_input"][:240],
                "council_mode": task_payload["council_mode"],
                "mode_reason": task_payload["mode_reason"],
                "source_ref": task_payload["source_ref"],
            },
        )
        self.session.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "source": insert_stmt.excluded.source,
                    "source_ref": insert_stmt.excluded.source_ref,
                    "task_type": insert_stmt.excluded.task_type,
                    "council_mode": insert_stmt.excluded.council_mode,
                    "mode_reason": insert_stmt.excluded.mode_reason,
                    "status": insert_stmt.excluded.status,
                    "state_summary": insert_stmt.excluded.state_summary,
                },
            )
        )
        self.session.flush()


def build_payload_ref(callback: VerifiedFeishuCallback) -> str:
    return f"artifact://feishu-callback/{callback.payload_hash}"


def build_approver(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if not isinstance(event, dict):
        return {"source": "feishu"}

    operator = event.get("operator")
    if not isinstance(operator, dict):
        return {"source": "feishu"}

    return {
        "source": "feishu",
        "open_id": operator.get("open_id"),
        "union_id": operator.get("union_id"),
        "user_id": operator.get("user_id"),
    }


def _validate_task_confirmation_payload(
    task_payload: dict[str, Any],
    command,
) -> None:
    expected = {
        "thread_id": str(command.thread_id),
        "task_id": str(command.task_id),
        "source": "feishu",
        "source_ref": command.source_ref,
    }
    for key, value in expected.items():
        if task_payload.get(key) != value:
            raise FeishuCallbackPayloadError(f"task confirmation {key} does not match")
    required_model_scope = ("model_profile_id", "model_owner_user_id", "model_guild_id")
    if not all(task_payload.get(key) for key in required_model_scope):
        raise FeishuCallbackPayloadError("task confirmation payload missing BYOK model scope")


def _validate_model_setup_intake(intake, command) -> None:
    if intake.source_ref != command.source_ref:
        raise FeishuCallbackPayloadError("model setup source_ref does not match original task")
    if intake.chat_id != command.chat_id:
        raise FeishuCallbackPayloadError("model setup chat_id does not match original task")
    if intake.thread_id != command.thread_id:
        raise FeishuCallbackPayloadError("model setup thread_id does not match original task")
    if intake.model_owner_user_id != command.model_owner_user_id:
        raise FeishuCallbackPayloadError("model setup owner does not match original task")
    if intake.model_owner_id_type != command.model_owner_id_type:
        raise FeishuCallbackPayloadError("model setup owner id type does not match original task")


def _json_payload_text(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _approved_task_payload_ref(task_payload_ref: str) -> str:
    return f"{task_payload_ref}:approved"


def _authorized_task_payload(
    task_payload: dict[str, Any],
    *,
    operator_open_id: str | None,
) -> dict[str, Any]:
    authorized = dict(task_payload)
    authorized["authorization"] = {
        "status": "authorized",
        "source": "feishu_card",
        "approver": {"source": "feishu", "open_id": operator_open_id},
        "authorized_at": datetime.now(UTC).isoformat(),
    }
    return authorized


def _operator_matches_model_owner(command) -> bool:
    operator_id_by_type = {
        "open_id": command.operator_open_id,
        "user_id": command.operator_user_id,
        "union_id": command.operator_union_id,
    }
    return operator_id_by_type.get(command.model_owner_id_type) == command.model_owner_user_id


def _redact_model_setup_payload(payload: dict[str, Any]) -> dict[str, Any]:
    import copy

    redacted = copy.deepcopy(payload)
    _redact_secret_keys(redacted)
    return redacted


def _redact_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if str(key).lower() in {"api_key", "apikey", "secret", "token"}:
                value[key] = "***redacted***"
            elif isinstance(item, str) and _string_may_contain_secret(item):
                value[key] = _redacted_secret_string(item)
            else:
                _redact_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            _redact_secret_keys(item)


def _string_may_contain_secret(value: str) -> bool:
    lowered = value.lower()
    return "api_key" in lowered or "apikey" in lowered or "sk-" in value


def _redacted_secret_string(value: str) -> str:
    import json

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return "***redacted***"
    if isinstance(parsed, dict | list):
        _redact_secret_keys(parsed)
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "***redacted***"


def _safe_model_setup_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "sk-" in text or "api_key" in lowered or "secret" in lowered:
        return "模型配置失败：API Key 未保存或验证失败。"
    return f"模型配置失败：{text[:240]}"
