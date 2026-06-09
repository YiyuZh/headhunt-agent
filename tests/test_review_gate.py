from uuid import uuid4

from app.runtime.review_gate import ReviewGate, repair_artifact_for_review
from app.schemas.artifacts import ArtifactRef


def test_review_gate_passes_supported_artifact_with_evidence() -> None:
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="CandidateMatchDraft",
        summary="候选人与岗位匹配，具备明确证据和后续人工确认路径。",
        content_ref="artifact://candidate/1",
        evidence_refs=["evidence://candidate/1"],
    )

    result = ReviewGate().review_artifact(artifact)

    assert result.status == "pass"
    assert result.findings == []


def test_review_gate_requires_human_when_business_artifact_has_no_evidence() -> None:
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="ReportDraft",
        summary="建议推进候选人进入下一轮。",
        content_ref="artifact://report/1",
    )

    result = ReviewGate().review_artifact(artifact)

    assert result.status == "needs_human"
    assert result.findings[0].reviewer == "EvidenceConsistencyReviewer"


def test_review_gate_requires_human_for_high_pii_artifact() -> None:
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="CandidateMatchDraft",
        summary="Candidate match draft with enough evidence for business review.",
        content_ref="artifact://candidate/high-pii",
        evidence_refs=["evidence://candidate/high-pii"],
        pii_level="high",
    )

    result = ReviewGate().review_artifact(artifact)

    assert result.status == "needs_human"
    assert result.findings[0].reviewer == "SafetyReviewer"
    assert result.findings[0].path == "pii_level"


def test_review_gate_malformed_artifact_id_fails_closed_without_exception() -> None:
    result = ReviewGate().review_artifact(
        {
            "artifact_id": "not-a-uuid",
            "kind": "CandidateMatchDraft",
            "summary": "Malformed artifact should not become an action proposal.",
            "content_ref": "artifact://candidate/bad",
            "evidence_refs": ["evidence://candidate/bad"],
            "source_refs": [],
            "version": 1,
            "size_tokens_estimate": 12,
        }
    )

    assert result.status == "needs_human"
    assert result.artifact_id is None
    assert result.findings[0].reviewer == "SchemaValidator"
    assert result.findings[0].severity == "human"


def test_review_gate_needs_fix_for_short_summary_and_escalates_after_repair_attempt() -> None:
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="CouncilOpinion",
        summary="短",
        content_ref="artifact://opinion/1",
    )

    first = ReviewGate().review_artifact(artifact, repair_attempts=0)
    second = ReviewGate().review_artifact(artifact, repair_attempts=1)

    assert first.status == "needs_fix"
    assert first.findings[0].reviewer == "PracticalityReviewer"
    assert second.status == "needs_human"


def test_review_gate_rejects_forbidden_raw_context() -> None:
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="CouncilOpinion",
        summary="结构化意见摘要足够清晰。",
        content_ref="artifact://opinion/2",
    )

    result = ReviewGate().review_artifact(
        artifact,
        review_context={"messages": ["raw chat history"]},
    )

    assert result.status == "needs_human"
    assert result.findings[0].reviewer == "ContextBudgetReviewer"


def test_repair_artifact_for_review_keeps_artifact_ref_shape() -> None:
    artifact_id = uuid4()
    repaired = repair_artifact_for_review(
        {
            "artifact_id": str(artifact_id),
            "kind": "CouncilOpinion",
            "summary": "短",
            "content_ref": "artifact://opinion/3",
            "evidence_refs": [],
            "source_refs": [],
            "version": 1,
            "size_tokens_estimate": 1,
        },
        {"status": "needs_fix", "findings": [{"reviewer": "PracticalityReviewer"}]},
    )

    parsed = ArtifactRef.model_validate(repaired)
    assert parsed.artifact_id == artifact_id
    assert parsed.version == 2
    assert parsed.source_refs[0].startswith("review://artifact-review-gate/")
