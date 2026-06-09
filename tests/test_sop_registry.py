from pathlib import Path

import pytest

from app.sops.registry import AgentSOPRegistry, AgentSOPRegistryError


def test_default_sop_registry_resolves_review_ref_for_artifact_agent() -> None:
    refs = AgentSOPRegistry.from_default_repo().resolve(
        agent_name="StrategyDraftAgent",
        node_name="strategy_draft",
        task_type="requisition_calibration",
        output_artifact_type="RequisitionCalibrationDraft",
        policy={"allowed_artifact_types_write": ["RequisitionCalibrationDraft"]},
    )

    assert [item.sop_id for item in refs] == ["artifact-review-gate"]
    assert refs[0].content_ref == "sop://reviewers/artifact-review-gate/0.1.0"
    assert refs[0].tokens_estimate > 0
    assert not hasattr(refs[0], "content")


def test_sop_registry_adds_task_intake_ref_only_for_intake_nodes() -> None:
    refs = AgentSOPRegistry.from_default_repo().resolve(
        agent_name="TaskIntakeParser",
        node_name="task_intake_double_check",
        task_type="task_intake",
        output_artifact_type="CanonicalTaskBrief",
        policy={"allowed_artifact_types_write": ["CanonicalTaskBrief"]},
    )

    assert [item.sop_id for item in refs] == [
        "task-intake-double-check",
        "artifact-review-gate",
    ]
    assert len(refs) <= 3


def test_sop_registry_rejects_invalid_registry(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('{"sops": [{"sop_id": "missing-fields"}]}', encoding="utf-8")

    with pytest.raises(AgentSOPRegistryError, match="missing fields"):
        AgentSOPRegistry.from_file(registry_path)
