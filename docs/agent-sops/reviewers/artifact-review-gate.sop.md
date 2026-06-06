# Artifact Review Gate SOP

Version: 0.1.0  
Scope: `review.artifact_quality`  
Trigger: `always`

## Purpose

Review one artifact at a time and return `pass`, `needs_fix`, or `needs_human` for LangGraph conditional routing.

## Required Inputs

- current artifact summary and `content_ref`
- artifact schema
- evidence refs and source refs
- frozen `CanonicalTaskBrief` allowed fields
- reviewer policy and token budget

## Reviewers

- `SchemaValidator`: deterministic schema, enum, type, and required-field checks.
- `EvidenceConsistencyReviewer`: conclusion support from `evidence_refs` and `source_refs`.
- `PracticalityReviewer`: personalization, value proposition, action clarity, recruiter tone, compliance risk, brevity.
- `ContextBudgetReviewer`: rejects full history, full state, all AgentRuns, all artifacts, all memories, or overlarge SOP injection.
- `SafetyReviewer`: escalates privacy, outreach, recommendation, or side-effect boundary violations.

## Routing

```text
pass -> next_node
needs_fix -> repair_node_for_this_artifact
needs_human -> interrupt_human_approval
second needs_fix -> interrupt_human_approval
```

## Constraints

- MUST NOT review the whole conversation as a global chat review.
- MUST NOT send `needs_fix` back to the full graph.
- MUST NOT rerun unrelated upstream nodes.
- MUST NOT read raw prompts, full ContextPack from other Agents, or full chat history.

