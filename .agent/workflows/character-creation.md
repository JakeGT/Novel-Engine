---
description: Implement or revise the character creation vertical slice
---

# Character creation workflow

Before changing this workflow, read `AGENTS.md`, `plan/03-domain-and-temporal-model.md`, `plan/04-ai-and-retrieval.md`, and `plan/12-current-gap-analysis.md`.

The intended product flow is:

1. Create a project-scoped character anchor with a stable opaque ID.
2. Save the author's initial bio as authored source material.
3. Assemble world context using explicit links, governed retrieval, and visibility rules.
4. Generate a typed question sheet through the AI provider port, or use a deterministic fake in tests.
5. Capture answers without mutating canon.
6. Extract a proposed set of appearance, personality, state, belief, goal, relationship, and backstory changes.
7. Resolve temporary references against existing entities.
8. Validate the proposal against schema, project, temporal, and knowledge invariants.
9. Let the author accept, amend, or reject operations.
10. Commit accepted operations through the mutation journal and canonical commit service.

Model output is a proposal, not a graph transaction. Preserve false beliefs as beliefs, reuse an existing entity only when resolution is unambiguous, and require evidence/provenance for accepted changes.

When working against the current prototype, add characterization tests before changing response shapes. Known gaps are tracked under `FND-008` in `plan/10-backlog.md`.
