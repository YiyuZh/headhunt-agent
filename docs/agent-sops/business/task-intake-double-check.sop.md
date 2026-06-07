# Task Intake Double Check SOP

Version: 0.1.0  
Scope: `business.task_intake`  
Trigger: `always`

## Purpose

Convert Feishu messages, card fields, attachment summaries, or API payloads into a sourced `CanonicalTaskBrief`, then require user double check before graph dispatch.

## Required Inputs

- `interaction_id` or `request_id`
- `channel_context`
- user-provided task fields
- attachment refs or source refs, when present

## Steps

1. Extract candidate fields into `facts`, `missing_fields`, and `assumptions`.
2. Attach `field_source` and `confidence` to every key fact.
3. Put any inference without source into `assumptions`, not `facts`.
4. Build preview card for Feishu double check.
5. On approve, freeze `CanonicalTaskBrief.version`.
6. On edit, create a new version and send another confirmation card.
7. On reject or expiry, stop the task and do not dispatch graph.

## Constraints

- MUST NOT start `headhunter_war_room_graph` before approve.
- MUST NOT overwrite a frozen `CanonicalTaskBrief` in place.
- MUST NOT pass raw Feishu chat history to downstream Agent nodes.
- SHOULD surface low-confidence fields in the confirmation card.

## Output

- `CanonicalTaskBrief`
- `TaskDoubleCheckState`
- `UserCorrectionMemoryProposal` when user edits or rejects with a useful correction
