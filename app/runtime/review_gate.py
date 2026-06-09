from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.schemas.artifacts import ArtifactRef
from app.schemas.review import ReviewFinding, ReviewGateResult

BUSINESS_ARTIFACT_KINDS = {
    "RequisitionCalibrationDraft",
    "TalentMapDraft",
    "CandidateMatchDraft",
    "ReportDraft",
    "FollowupReviewDraft",
    "CaseDataDraft",
}
FORBIDDEN_CONTEXT_KEYS = {
    "messages",
    "chat_history",
    "raw_prompt",
    "node_history",
    "recruitment_state",
    "all_artifacts",
    "all_memories",
    "agent_runs",
    "full_sop_body",
}
SIDE_EFFECT_PHRASES = (
    "已发送",
    "已经发送",
    "已触达",
    "已经触达",
    "已联系",
    "已经联系",
    "已写入",
    "已经写入",
    "sent to candidate",
    "contacted candidate",
    "wrote to bitable",
)


class ReviewGate:
    def review_artifact(
        self,
        artifact: ArtifactRef | dict[str, Any],
        *,
        review_context: dict[str, Any] | None = None,
        repair_attempts: int = 0,
    ) -> ReviewGateResult:
        context = review_context or {}
        parsed, findings = self._schema_findings(artifact)
        raw_artifact = artifact if isinstance(artifact, dict) else artifact.model_dump(mode="json")

        if parsed is not None:
            findings.extend(self._evidence_findings(parsed))
            findings.extend(self._practicality_findings(parsed))
        findings.extend(self._context_budget_findings(context))
        findings.extend(self._safety_findings(raw_artifact, context))

        human_findings = [item for item in findings if item.severity == "human"]
        fix_findings = [item for item in findings if item.severity == "fix"]
        if human_findings or (fix_findings and repair_attempts >= 1):
            status = "needs_human"
        elif fix_findings:
            status = "needs_fix"
        else:
            status = "pass"

        return ReviewGateResult(
            status=status,
            artifact_id=parsed.artifact_id if parsed is not None else _optional_uuid(raw_artifact),
            artifact_kind=(
                parsed.kind if parsed is not None else _optional_str(raw_artifact, "kind")
            ),
            findings=findings,
            repair_attempts=repair_attempts,
            reviewed_at=datetime.now(UTC),
        )

    def _schema_findings(
        self,
        artifact: ArtifactRef | dict[str, Any],
    ) -> tuple[ArtifactRef | None, list[ReviewFinding]]:
        if isinstance(artifact, ArtifactRef):
            return artifact, []
        try:
            return ArtifactRef.model_validate(artifact), []
        except ValidationError as exc:
            return None, [
                ReviewFinding(
                    reviewer="SchemaValidator",
                    severity="human",
                    message=(
                        "ArtifactRef schema validation failed; artifact is not "
                        f"eligible for action proposal: {exc.errors()[0]['msg']}"
                    ),
                    path="artifact",
                )
            ]

    def _evidence_findings(self, artifact: ArtifactRef) -> list[ReviewFinding]:
        if artifact.kind not in BUSINESS_ARTIFACT_KINDS:
            return []
        if artifact.evidence_refs or artifact.source_refs:
            return []
        return [
            ReviewFinding(
                reviewer="EvidenceConsistencyReviewer",
                severity="human",
                message=(
                    "Business artifact has no evidence_refs or source_refs to support "
                    "the conclusion."
                ),
                path="evidence_refs",
            )
        ]

    def _practicality_findings(self, artifact: ArtifactRef) -> list[ReviewFinding]:
        if len(artifact.summary.strip()) >= 12:
            return []
        return [
            ReviewFinding(
                reviewer="PracticalityReviewer",
                severity="fix",
                message="Artifact summary is too short to be actionable.",
                path="summary",
            )
        ]

    def _context_budget_findings(self, context: dict[str, Any]) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        forbidden = sorted(FORBIDDEN_CONTEXT_KEYS.intersection(context))
        if forbidden:
            findings.append(
                ReviewFinding(
                    reviewer="ContextBudgetReviewer",
                    severity="human",
                    message=(
                        "Review context contains forbidden raw context keys: "
                        f"{', '.join(forbidden)}."
                    ),
                    path="review_context",
                )
            )
        if _int_value(context.get("memory_ref_count")) > 10:
            findings.append(
                ReviewFinding(
                    reviewer="ContextBudgetReviewer",
                    severity="human",
                    message=(
                        "Review context contains too many memory refs for "
                        "artifact-level review."
                    ),
                    path="review_context.memory_ref_count",
                )
            )
        if _int_value(context.get("total_tokens_estimate")) > 3500:
            findings.append(
                ReviewFinding(
                    reviewer="ContextBudgetReviewer",
                    severity="human",
                    message="Review context token estimate exceeds the artifact-level budget.",
                    path="review_context.total_tokens_estimate",
                )
            )
        return findings

    def _safety_findings(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any],
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        pii_level = str(artifact.get("pii_level") or context.get("pii_level") or "none")
        if pii_level == "high":
            findings.append(
                ReviewFinding(
                    reviewer="SafetyReviewer",
                    severity="human",
                    message="High-PII artifact requires human review before any side effect.",
                    path="pii_level",
                )
            )
        summary = str(artifact.get("summary") or "").lower()
        if any(phrase.lower() in summary for phrase in SIDE_EFFECT_PHRASES):
            findings.append(
                ReviewFinding(
                    reviewer="SafetyReviewer",
                    severity="human",
                    message="Artifact summary implies an external side effect already happened.",
                    path="summary",
                )
            )
        return findings


def repair_artifact_for_review(
    artifact: dict[str, Any],
    review_result: dict[str, Any],
) -> dict[str, Any]:
    repaired = dict(artifact)
    summary = str(repaired.get("summary") or "").strip()
    if len(summary) < 12:
        repaired["summary"] = f"{summary}；ReviewGate 已补充可执行摘要，需人工确认后落库。"
    repaired["version"] = int(repaired.get("version") or 1) + 1
    source_refs = list(repaired.get("source_refs") or [])
    artifact_id = str(repaired.get("artifact_id") or "unknown")
    source_refs.append(f"review://artifact-review-gate/{artifact_id}/repair")
    repaired["source_refs"] = source_refs
    return repaired


def _optional_uuid(artifact: dict[str, Any]) -> UUID | None:
    value = artifact.get("artifact_id")
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _optional_str(artifact: dict[str, Any], key: str) -> str | None:
    value = artifact.get(key)
    return str(value) if value is not None else None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
