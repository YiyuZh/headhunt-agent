# PRD Executable Quality Checklist

- [ ] SOPRegistry has file layout, trigger policy, and resolve audit.
- [ ] TaskIntakeParser fields have `field_source` and `confidence`.
- [ ] Unsourced inference goes to `assumptions`, not facts.
- [ ] Double Check approve freezes `CanonicalTaskBrief.version`.
- [ ] Downstream Agents only read frozen brief and refs.
- [ ] ReviewGate is artifact-level and uses conditional edges.
- [ ] `needs_fix` returns only to the artifact repair node once.
- [ ] MemoryGateway has tenant/guild/user/project/requisition/candidate scope filter.
- [ ] Current company facts require SearchGateway source_refs.
- [ ] Resume/JD keyword extraction uses current artifacts as primary facts.

