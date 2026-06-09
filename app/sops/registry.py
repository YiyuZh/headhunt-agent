import json
from pathlib import Path
from typing import Any

from app.schemas.context import SOPRef


class AgentSOPRegistryError(RuntimeError):
    pass


class AgentSOPRegistry:
    def __init__(
        self,
        *,
        registry_path: Path,
        records: list[dict[str, Any]],
        max_primary_sops: int = 1,
        max_review_sops: int = 2,
    ):
        self.registry_path = registry_path
        self.records = records
        self.max_primary_sops = max_primary_sops
        self.max_review_sops = max_review_sops

    @classmethod
    def from_default_repo(cls) -> "AgentSOPRegistry":
        repo_root = Path(__file__).resolve().parents[2]
        return cls.from_file(repo_root / "docs" / "agent-sops" / "registry.json")

    @classmethod
    def from_file(cls, registry_path: Path) -> "AgentSOPRegistry":
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise AgentSOPRegistryError(
                f"Agent SOP registry is not readable: {registry_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise AgentSOPRegistryError(
                f"Agent SOP registry is invalid JSON: {registry_path}"
            ) from exc

        records = payload.get("sops")
        if not isinstance(records, list):
            raise AgentSOPRegistryError("Agent SOP registry must contain a sops list")
        limits = payload.get("max_refs_per_node") if isinstance(payload, dict) else None
        limits = limits if isinstance(limits, dict) else {}
        max_primary = _positive_int(limits.get("primary_sop"), default=1)
        max_review = _positive_int(limits.get("review_sop"), default=2)
        return cls(
            registry_path=registry_path,
            records=[_validate_record(item) for item in records],
            max_primary_sops=max_primary,
            max_review_sops=max_review,
        )

    def resolve(
        self,
        *,
        agent_name: str,
        node_name: str,
        task_type: str,
        output_artifact_type: str,
        policy: dict[str, Any],
    ) -> list[SOPRef]:
        primary: list[SOPRef] = []
        review: list[SOPRef] = []
        for record in self.records:
            ref = self._resolve_record(
                record,
                agent_name=agent_name,
                node_name=node_name,
                task_type=task_type,
                output_artifact_type=output_artifact_type,
                policy=policy,
            )
            if ref is None:
                continue
            if record["scope"].startswith("review."):
                if len(review) < self.max_review_sops:
                    review.append(ref)
            elif len(primary) < self.max_primary_sops:
                primary.append(ref)
        return [*primary, *review]

    def _resolve_record(
        self,
        record: dict[str, Any],
        *,
        agent_name: str,
        node_name: str,
        task_type: str,
        output_artifact_type: str,
        policy: dict[str, Any],
    ) -> SOPRef | None:
        scope = record["scope"]
        if scope == "business.task_intake":
            if not _matches_task_intake(
                agent_name=agent_name,
                node_name=node_name,
                task_type=task_type,
            ):
                return None
            reason = "matched task intake or double-check node"
        elif scope == "review.artifact_quality":
            writable_types = policy.get("allowed_artifact_types_write")
            if not output_artifact_type and not writable_types:
                return None
            reason = "matched artifact-producing agent node"
        else:
            reason = f"matched registry scope {scope}"

        return SOPRef(
            sop_id=record["sop_id"],
            version=record["version"],
            title=record["title"],
            scope=scope,
            content_ref=record["content_ref"],
            summary=record["summary"],
            trigger_policy=record["trigger_policy"],
            trigger_reason=reason,
            status=record["status"],
            path=record.get("path"),
            tokens_estimate=_estimate_ref_tokens(record),
        )


def _validate_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentSOPRegistryError("Agent SOP registry records must be objects")
    required = (
        "sop_id",
        "version",
        "title",
        "scope",
        "trigger_policy",
        "content_ref",
        "summary",
        "status",
    )
    missing = [key for key in required if not isinstance(value.get(key), str) or not value[key]]
    if missing:
        raise AgentSOPRegistryError(f"Agent SOP registry record missing fields: {missing}")
    return dict(value)


def _matches_task_intake(*, agent_name: str, node_name: str, task_type: str) -> bool:
    searchable = " ".join([agent_name, node_name, task_type]).lower().replace("-", "_")
    return (
        "task_intake" in searchable
        or "double_check" in searchable
        or "confirmation" in searchable
        or "taskintakeparser" in searchable
    )


def _estimate_ref_tokens(record: dict[str, Any]) -> int:
    text = " ".join(
        str(record.get(key) or "") for key in ("sop_id", "title", "scope", "summary", "content_ref")
    )
    return max(1, len(text) // 4)


def _positive_int(value: Any, *, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default
