import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.schemas.artifacts import AgentArtifact
from app.storage.models import (
    AgentArtifact as AgentArtifactRecord,
)
from app.storage.models import (
    ArtifactBlob,
    ContentAccessAudit,
)

PII_ORDER = ("none", "low", "medium", "high")


class ContentAccessDenied(RuntimeError):
    pass


class PostgresArtifactStore:
    def __init__(self, session: Session):
        self.session = session

    def write(
        self,
        artifact: AgentArtifact,
        policy: dict[str, Any],
        *,
        payload: dict[str, Any] | str | None = None,
        media_type: str = "application/json",
    ) -> str:
        self._ensure_artifact_write_allowed(artifact, policy)
        if payload is not None:
            self._write_blob(
                content_ref=artifact.content_ref,
                payload=payload,
                media_type=media_type,
            )

        self.session.execute(
            pg_insert(AgentArtifactRecord)
            .values(
                id=artifact.artifact_id,
                thread_id=artifact.thread_id,
                run_id=artifact.run_id,
                kind=artifact.artifact_type,
                summary=artifact.summary,
                content_ref=artifact.content_ref,
                evidence_refs=artifact.evidence_refs,
                source_refs=artifact.source_refs,
                pii_level=artifact.pii_level.value,
                version=artifact.version,
                size_tokens_estimate=artifact.size_tokens_estimate,
            )
            .on_conflict_do_update(
                index_elements=["content_ref"],
                set_={
                    "summary": artifact.summary,
                    "evidence_refs": artifact.evidence_refs,
                    "source_refs": artifact.source_refs,
                    "pii_level": artifact.pii_level.value,
                    "version": artifact.version,
                    "size_tokens_estimate": artifact.size_tokens_estimate,
                },
            )
        )
        self.session.flush()
        return artifact.content_ref

    def read_summary(self, artifact_id: UUID, policy: dict[str, Any]) -> AgentArtifact:
        record = self.session.get(AgentArtifactRecord, artifact_id)
        if record is None:
            raise KeyError(str(artifact_id))
        self._ensure_artifact_read_allowed(record.kind, policy)
        return _artifact_from_record(record)

    def read_content(
        self,
        content_ref: str,
        policy: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any] | str:
        artifact = self.session.execute(
            select(AgentArtifactRecord).where(AgentArtifactRecord.content_ref == content_ref)
        ).scalar_one_or_none()
        if artifact is None:
            raise KeyError(content_ref)

        denied_reason = _artifact_access_denial_reason(artifact, policy)
        if denied_reason:
            self._record_content_access_audit(
                artifact=artifact,
                content_ref=content_ref,
                policy=policy,
                purpose=purpose,
                allowed=False,
                denied_reason=denied_reason,
            )
            raise ContentAccessDenied(denied_reason)

        blob = self.session.get(ArtifactBlob, content_ref)
        if blob is None:
            raise KeyError(content_ref)

        self._record_content_access_audit(
            artifact=artifact,
            content_ref=content_ref,
            policy=policy,
            purpose=purpose,
            allowed=True,
        )

        if blob.content_json is not None:
            return blob.content_json
        return blob.content_text or ""

    def _record_content_access_audit(
        self,
        *,
        artifact: AgentArtifactRecord,
        content_ref: str,
        policy: dict[str, Any],
        purpose: str,
        allowed: bool,
        denied_reason: str | None = None,
    ) -> None:
        policy_decision = _json_safe_policy(policy)
        policy_decision["allowed"] = allowed
        if denied_reason:
            policy_decision["denied_reason"] = denied_reason
        self.session.add(
            ContentAccessAudit(
                content_ref=content_ref,
                content_kind="artifact",
                actor_type=str(policy.get("actor_type", "agent")),
                actor_id=_optional_str(policy.get("actor_id")),
                agent_name=_optional_str(policy.get("agent_name")),
                thread_id=getattr(artifact, "thread_id", None) or policy.get("thread_id"),
                run_id=getattr(artifact, "run_id", None) or policy.get("run_id"),
                pii_level=artifact.pii_level,
                purpose=purpose,
                policy_decision=policy_decision,
            )
        )
        self.session.flush()

    def _write_blob(
        self,
        *,
        content_ref: str,
        payload: dict[str, Any] | str,
        media_type: str,
    ) -> None:
        content_json = payload if isinstance(payload, dict) else None
        content_text = payload if isinstance(payload, str) else _stable_json(payload)
        self.session.execute(
            pg_insert(ArtifactBlob)
            .values(
                content_ref=content_ref,
                media_type=media_type,
                content_json=content_json,
                content_text=content_text,
                sha256=hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
            )
            .on_conflict_do_update(
                index_elements=["content_ref"],
                set_={
                    "media_type": media_type,
                    "content_json": content_json,
                    "content_text": content_text,
                    "sha256": hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
                },
            )
        )

    @staticmethod
    def _ensure_artifact_write_allowed(
        artifact: AgentArtifact,
        policy: dict[str, Any],
    ) -> None:
        allowed = policy.get("allowed_artifact_types_write")
        if allowed is not None and artifact.artifact_type not in set(allowed):
            raise ContentAccessDenied(f"policy cannot write artifact type {artifact.artifact_type}")

    @staticmethod
    def _ensure_artifact_read_allowed(kind: str, policy: dict[str, Any]) -> None:
        allowed = policy.get("allowed_artifact_types_read")
        if allowed is not None and kind not in set(allowed):
            raise ContentAccessDenied(f"policy cannot read artifact type {kind}")


def _artifact_from_record(record: AgentArtifactRecord) -> AgentArtifact:
    return AgentArtifact(
        artifact_id=record.id,
        run_id=record.run_id,
        thread_id=record.thread_id,
        producer_agent="ArtifactStore",
        artifact_type=record.kind,
        summary=record.summary,
        content_ref=record.content_ref,
        evidence_refs=list(record.evidence_refs or []),
        source_refs=list(record.source_refs or []),
        pii_level=record.pii_level,
        version=record.version,
        size_tokens_estimate=record.size_tokens_estimate or 0,
    )


def _stable_json(content: Any) -> str:
    return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _json_safe_policy(policy: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in policy.items():
        if key.lower().endswith(("secret", "token", "key")):
            safe[key] = "<redacted>"
        elif isinstance(value, UUID):
            safe[key] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [str(item) if isinstance(item, UUID) else item for item in value]
        else:
            safe[key] = str(value)
    return safe


def _artifact_access_denial_reason(
    artifact: AgentArtifactRecord,
    policy: dict[str, Any],
) -> str | None:
    if not policy.get("can_read_artifact_content", False):
        return "policy does not allow artifact content reads"
    allowed = policy.get("allowed_artifact_types_read")
    if allowed is not None and artifact.kind not in set(allowed):
        return f"policy cannot read artifact type {artifact.kind}"
    max_pii_level = str(policy.get("pii_access_level", "none"))
    if not _pii_allowed(artifact.pii_level, max_pii_level):
        return f"policy cannot read artifact pii_level {artifact.pii_level}"
    return None


def _pii_allowed(actual: str, max_pii_level: str) -> bool:
    if actual not in PII_ORDER or max_pii_level not in PII_ORDER:
        return actual == "none"
    return PII_ORDER.index(actual) <= PII_ORDER.index(max_pii_level)
