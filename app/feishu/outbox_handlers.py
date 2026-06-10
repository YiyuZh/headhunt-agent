import hashlib
import json
import logging
from typing import Any, Protocol
from uuid import UUID

from app.core.config import Settings
from app.feishu.cards import build_task_confirmation_card, build_task_parse_failed_card
from app.feishu.dispatcher import OutboxDispatchError
from app.feishu.gateways import (
    BITABLE_BATCH_CREATE_MAX_RECORDS,
    FeishuGatewayError,
    FeishuHttpBitableGateway,
    FeishuHttpGateway,
)
from app.feishu.task_intake import (
    build_graph_dispatch_payload,
    create_task_plan,
    parse_task_intake,
    parse_task_intake_with_llm,
    task_confirmation_card_ref,
    task_parse_failed_card_ref,
    task_payload_ref,
)
from app.gateways.llm import get_gateway_model_info
from app.model_profiles.gateway_factory import UserModelGatewayFactory
from app.model_profiles.repository import ModelProfileRepository
from app.model_profiles.secrets import ModelSecretService
from app.policy.engine import PolicyEngine
from app.runtime.outbox import RuntimeNotReadyError
from app.storage.models import FeishuOutbox
from app.storage.repositories import (
    BitableActionNotApprovedError,
    BitableSyncRepository,
    FeishuOutboxWriteRepository,
    PayloadRepository,
)

logger = logging.getLogger(__name__)


class GraphDispatchHandler(Protocol):
    def dispatch_graph(self, payload: dict[str, Any]) -> None: ...
    def resume_graph(self, payload: dict[str, Any]) -> None: ...


class TaskConfirmationPrepareHandler(Protocol):
    def prepare_task_confirmation(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> None: ...


class FeishuOutboxHandler:
    def __init__(
        self,
        *,
        payload_repository: PayloadRepository,
        feishu_gateway: FeishuHttpGateway,
        bitable_gateway: FeishuHttpBitableGateway,
        graph_handler: GraphDispatchHandler | None = None,
        bitable_sync_repository: BitableSyncRepository | None = None,
        task_confirmation_preparer: TaskConfirmationPrepareHandler | None = None,
    ):
        self.payload_repository = payload_repository
        self.feishu_gateway = feishu_gateway
        self.bitable_gateway = bitable_gateway
        self.graph_handler = graph_handler
        self.bitable_sync_repository = bitable_sync_repository
        self.task_confirmation_preparer = task_confirmation_preparer

    def handle(self, item: FeishuOutbox) -> None:
        payload = self.payload_repository.get_json_payload(item.payload_ref)

        try:
            match item.kind:
                case "card_send":
                    self._send_card(payload, item.idempotency_key)
                case "card_update":
                    self._update_card(payload, item.idempotency_key)
                case "bitable_write":
                    self._write_bitable(item, payload)
                case "task_confirmation_prepare":
                    self._prepare_task_confirmation(payload, item.idempotency_key)
                case "graph_dispatch":
                    self._dispatch_graph(payload)
                case "resume":
                    self._resume_graph(payload)
                case _:
                    raise OutboxDispatchError(f"Unsupported outbox kind: {item.kind}")
        except FeishuGatewayError as exc:
            raise OutboxDispatchError(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    def _send_card(self, payload: dict[str, Any], idempotency_key: str) -> None:
        chat_id = _required_str(payload, "chat_id")
        card = _required_dict(payload, "card")
        self.feishu_gateway.send_card(chat_id, card, idempotency_key)

    def _update_card(self, payload: dict[str, Any], idempotency_key: str) -> None:
        open_message_id = _required_str(payload, "open_message_id")
        card = _required_dict(payload, "card")
        self.feishu_gateway.update_card(open_message_id, card, idempotency_key)

    def _prepare_task_confirmation(self, payload: dict[str, Any], idempotency_key: str) -> None:
        if self.task_confirmation_preparer is None:
            raise OutboxDispatchError(
                "task_confirmation_prepare handler is not wired yet",
                retry_after_seconds=300,
            )
        self.task_confirmation_preparer.prepare_task_confirmation(
            payload,
            idempotency_key=idempotency_key,
        )

    def _write_bitable(self, item: FeishuOutbox, payload: dict[str, Any]) -> None:
        action_id = _optional_uuid(payload.get("action_id"))
        if action_id is None:
            raise OutboxDispatchError("bitable_write payload.action_id is required")
        if self.bitable_sync_repository is None:
            raise OutboxDispatchError(
                "bitable_write requires BitableSyncRepository approval checker"
            )
        try:
            self.bitable_sync_repository.ensure_action_approved(action_id)
        except BitableActionNotApprovedError as exc:
            raise OutboxDispatchError(str(exc), retry_after_seconds=300) from exc
        app_token = _required_str(payload, "app_token")
        table_id = _required_str(payload, "table_id")
        client_token = _required_str(payload, "client_token")
        records = payload.get("records")
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise OutboxDispatchError("bitable_write payload.records must be a list of objects")
        if not records or len(records) > BITABLE_BATCH_CREATE_MAX_RECORDS:
            raise OutboxDispatchError(
                "bitable_write payload.records must contain "
                f"1-{BITABLE_BATCH_CREATE_MAX_RECORDS} objects"
            )
        record_ids = self.bitable_gateway.batch_create(app_token, table_id, records, client_token)
        payload_hash = payload.get("payload_hash")
        entity_refs = payload.get("entity_refs")
        self.bitable_sync_repository.record_chunk_success(
            client_token=client_token,
            app_token=app_token,
            table_id=table_id,
            record_ids=record_ids,
            records=records,
            outbox_id=getattr(item, "id", None),
            action_id=action_id,
            chunk_index=_optional_int(payload.get("chunk_index")),
            payload_hash=payload_hash if isinstance(payload_hash, str) else None,
            entity_refs=entity_refs if isinstance(entity_refs, list) else [],
        )

    def _dispatch_graph(self, payload: dict[str, Any]) -> None:
        if self.graph_handler is None:
            raise OutboxDispatchError(
                "graph_dispatch handler is not wired yet",
                retry_after_seconds=300,
            )
        try:
            self.graph_handler.dispatch_graph(payload)
        except RuntimeNotReadyError as exc:
            raise OutboxDispatchError(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    def _resume_graph(self, payload: dict[str, Any]) -> None:
        if self.graph_handler is None:
            raise OutboxDispatchError("resume handler is not wired yet", retry_after_seconds=300)
        try:
            self.graph_handler.resume_graph(payload)
        except RuntimeNotReadyError as exc:
            raise OutboxDispatchError(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc


class FeishuTaskConfirmationPrepareHandler:
    def __init__(
        self,
        *,
        payload_repository: PayloadRepository,
        outbox_writer: FeishuOutboxWriteRepository,
        settings: Settings,
        policy_engine: PolicyEngine | None = None,
        model_profile_repository: ModelProfileRepository | None = None,
    ):
        self.payload_repository = payload_repository
        self.outbox_writer = outbox_writer
        self.settings = settings
        self.policy_engine = policy_engine or PolicyEngine()
        self.model_profile_repository = model_profile_repository or ModelProfileRepository(
            outbox_writer.session
        )

    def prepare_task_confirmation(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> None:
        chat_id = _required_str(payload, "chat_id")
        event_payload_ref = _required_str(payload, "event_payload_ref")
        source_ref = _required_str(payload, "source_ref")
        model_profile_id = _required_uuid(payload, "model_profile_id")
        thread_id = _required_uuid(payload, "thread_id")
        model_label = str(model_profile_id)

        try:
            event_payload = self.payload_repository.get_json_payload(event_payload_ref)
            intake = parse_task_intake(
                event_payload,
                tenant_key=_optional_str(payload.get("tenant_key")),
            )
            _validate_prepare_payload(intake, payload, model_profile_id=model_profile_id)
            parsed_intake, model_label = self._parse_with_user_model(intake, model_profile_id)
            self._enqueue_task_confirmation_card(
                chat_id=chat_id,
                intake=parsed_intake,
                model_profile_id=model_profile_id,
            )
            logger.info(
                "Feishu task_confirmation_prepare succeeded: thread_id=%s source_ref=%s "
                "model_profile_id=%s parser_status=%s idempotency_key=%s",
                parsed_intake.thread_id,
                parsed_intake.source_ref,
                model_profile_id,
                parsed_intake.parser_status,
                idempotency_key,
            )
        except Exception as exc:
            safe_error = _safe_intake_parse_error(exc)
            self._enqueue_parse_failed_card(
                chat_id=chat_id,
                thread_id=thread_id,
                source_ref=source_ref,
                model_profile_id=model_profile_id,
                model_label=model_label,
                error_summary=safe_error,
            )
            logger.warning(
                "Feishu task_confirmation_prepare parse failed: thread_id=%s source_ref=%s "
                "model_profile_id=%s idempotency_key=%s error=%s",
                thread_id,
                source_ref,
                model_profile_id,
                idempotency_key,
                safe_error,
            )

    def _parse_with_user_model(self, intake, model_profile_id: UUID):
        if self.settings.model_secret_encryption_key is None:
            raise RuntimeError("MODEL_SECRET_ENCRYPTION_KEY is required for task parsing")
        if not intake.model_owner_user_id or not intake.model_guild_id:
            raise RuntimeError("missing BYOK owner scope")

        secret_service = ModelSecretService(
            self.settings.model_secret_encryption_key.get_secret_value()
        )
        gateway_factory = UserModelGatewayFactory(
            repository=self.model_profile_repository,
            secret_service=secret_service,
            provider_allowlist=_provider_allowlist(self.settings),
            timeout_seconds=60.0,
        )
        gateway = gateway_factory.build_chat_gateway(
            profile_id=model_profile_id,
            tenant_id=intake.tenant_key,
            guild_id=intake.model_guild_id,
            user_id=intake.model_owner_user_id,
        )
        model_info = get_gateway_model_info(
            gateway,
            model_profile_id=model_profile_id,
            model_owner_user_id=intake.model_owner_user_id,
            model_guild_id=intake.model_guild_id,
            model_tenant_id=intake.tenant_key,
        )
        parsed = parse_task_intake_with_llm(
            intake,
            gateway,
            model_profile_id=model_profile_id,
        )
        return parsed, f"{model_info.model_provider}:{model_info.model_name}"

    def _enqueue_task_confirmation_card(
        self,
        *,
        chat_id: str,
        intake,
        model_profile_id: UUID,
    ) -> None:
        task_plan = create_task_plan(intake, self.policy_engine)
        graph_payload = build_graph_dispatch_payload(
            intake=intake,
            task_plan=task_plan,
            model_profile_id=model_profile_id,
        )
        graph_payload_ref = task_payload_ref(task_plan.thread_id, task_plan.task_id)
        graph_raw = _json_payload_text(graph_payload)
        self.payload_repository.store_json_payload(
            content_ref=graph_payload_ref,
            payload=graph_payload,
            raw_text=graph_raw,
            sha256=_sha256(graph_raw),
        )

        card_payload_ref = task_confirmation_card_ref(task_plan.thread_id, task_plan.task_id)
        card_payload = {
            "chat_id": chat_id,
            "card": build_task_confirmation_card(
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
                structured_fields=intake.structured_fields,
                raw_request_text=intake.request_text,
                parser_status=intake.parser_status,
                parser_error=intake.parser_error,
            ),
        }
        self.outbox_writer.enqueue_json(
            kind="card_send",
            idempotency_key=f"task_confirmation:{intake.source_ref}:{model_profile_id}",
            payload=card_payload,
            thread_id=task_plan.thread_id,
            content_ref=card_payload_ref,
        )

    def _enqueue_parse_failed_card(
        self,
        *,
        chat_id: str,
        thread_id: UUID,
        source_ref: str,
        model_profile_id: UUID,
        model_label: str,
        error_summary: str,
    ) -> None:
        card_payload = {
            "chat_id": chat_id,
            "card": build_task_parse_failed_card(
                thread_id=thread_id,
                source_ref=source_ref,
                model_label=model_label,
                error_summary=error_summary,
            ),
        }
        self.outbox_writer.enqueue_json(
            kind="card_send",
            idempotency_key=f"task_parse_failed:{source_ref}:{model_profile_id}",
            payload=card_payload,
            thread_id=thread_id,
            content_ref=task_parse_failed_card_ref(source_ref, model_profile_id),
        )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OutboxDispatchError(f"outbox payload missing {key}")
    return value


def _required_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise OutboxDispatchError(f"outbox payload missing {key}")
    return value


def _required_uuid(payload: dict[str, Any], key: str) -> UUID:
    value = payload.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise OutboxDispatchError(f"outbox payload missing {key}")


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    return None


def _optional_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _validate_prepare_payload(intake, payload: dict[str, Any], *, model_profile_id: UUID) -> None:
    expected = {
        "source_ref": intake.source_ref,
        "chat_id": intake.chat_id,
        "thread_id": str(intake.thread_id),
        "model_profile_id": str(model_profile_id),
        "model_owner_user_id": intake.model_owner_user_id,
        "model_owner_id_type": intake.model_owner_id_type,
        "model_guild_id": intake.model_guild_id,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise OutboxDispatchError(f"task_confirmation_prepare {key} does not match")


def _json_payload_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provider_allowlist(settings: Settings) -> set[str]:
    return {
        item.strip().lower()
        for item in settings.model_provider_allowlist.split(",")
        if item.strip()
    } or {"openai", "deepseek"}


def _safe_intake_parse_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "sk-" in text or "api_key" in lowered or "secret" in lowered:
        return f"{exc.__class__.__name__}: model credential rejected or unavailable"
    return f"{exc.__class__.__name__}: {text[:240]}"
