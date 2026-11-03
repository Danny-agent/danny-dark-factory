# Optional plan-first workflow

This document is guidance for preparing bounded work, not an implemented approval
feature. It adds no workflow state, approval gate, capability, or execution authority.
The [owner-controlled charter](CHARTER.md) and [operating runbook](RUNBOOK.md)
remain authoritative, including their human review, verification, security, merge,
and deployment boundaries. A written plan or human design decision does not enable
an otherwise disabled autonomy tier or authorize publishing.

## Choose the amount of planning

Use direct execution for a well-scoped, reversible task with clear intent, known
ownership, explicit acceptance criteria, and a small affected area. Read the live
code and relevant documentation first, make the bounded change in the authorized
workspace, and preserve concise evidence for independent verification and review.
Direct execution still requires every existing check and human review; it does not
mean skipping research or deciding ambiguous product intent.

Use optional plan-first work when requirements are ambiguous, several components
interact, alternatives have meaningful tradeoffs, or the next safe slice is unclear.
Planning can clarify a task that is not yet suitable for execution; it cannot make
high-risk or security-sensitive work eligible for the factory. Stop and refer
human-only decisions to the responsible human before dependent implementation.
If new uncertainty appears during direct execution, pause that work and use the
relevant planning artifacts rather than expanding scope silently.

## Artifacts and ownership

Keep these artifacts compact, linked to the task and the code revision examined.
They can be sections of one reviewable planning note; they are not new ledger
states or mandatory files for every task. The task retains one canonical ID, one
owner, one workspace, and one immutable run record per attempt under the existing
controls. An agent may draft options and evidence, but humans own design choices.

| Artifact | Contents and completion condition | Owner |
| --- | --- | --- |
| Questions | Intended outcome, acceptance criteria, scope, constraints, unknowns, and who can resolve each unknown. Mark unresolved questions explicitly; resolve blocking intent and ownership questions before dependent work. | Task owner maintains the questions; the responsible human answers product intent and ownership questions. The agent may draft them. |
| Research | Findings from the live codebase: relevant paths and symbols, current behavior, existing tests, constraints, revision examined, and concise supporting evidence. Distinguish observed facts from assumptions and identify remaining gaps. | Assigned agent gathers and summarizes evidence within task authority; the task owner checks that it is sufficient for the decision. |
| Design | Candidate approaches, tradeoffs, chosen behavior, compatibility concerns, verification approach, and rollback considerations. Record the human decision and rationale, or mark the decision pending. | Responsible human owns product and architecture decisions; the agent drafts alternatives and explains consequences. |
| Structure | A sequence of small vertical slices, each delivering a coherent outcome with explicit paths or components, acceptance criteria, dependencies, exclusions, and a review boundary. Avoid a broad implementation task disguised as a plan. | Task owner maintains the breakdown; the responsible human resolves scope and design decisions. The agent may propose slices. |
| Bounded Implement | One authorized slice with its scope, acceptance criteria, time/retry/cost limits, actual diff, verification results, unresolved issues, and evidence needed for independent review. Completion means the slice meets its criteria and required checks, not that it is merged or deployed. | Assigned worker implements within scoped capabilities; independent verification and human review remain with the existing reviewers and controls. |

Research should describe what the repository currently does, not rely on recalled
architecture. Preserve a short evidence trail: revision, relevant file references,
commands or checks and their outcomes, assumptions, decisions, and open questions.
Do not retain noisy exploration transcripts or duplicate command output as a
substitute for conclusions. Never place credentials or secrets in these artifacts.
Refresh affected findings when the code or assumptions change; stop on stale
claims, unknown state, or conflicting evidence as required by the charter.

Implement one vertical slice at a time, with an observable result and focused
verification. If a slice reveals a new design choice or cannot fit its authorized
scope or budget, stop and return the issue to the task owner. Do not silently add
permissions, broaden acceptance criteria, or count blocked, skipped, incomplete,
or unsupported verification as success.

## Human review and existing boundaries

The charter reserves ambiguous product intent, architecture decisions, policy or
gate changes, authentication/secrets/permissions, billing, destructive data
operations, production infrastructure, merge, and deployment for humans. Missing
context, unclear ownership, unexpected protected-path changes, and failed or
unsupported verification also require human resolution. Agent-authored tests,
workflow/policy edits, and changes to factory controls require independent human
review; a planning note does not authorize a worker to edit protected files.

A human design decision settles the recorded question only. It does not replace
deterministic policy, required checks, task authorization, or independent review
of the exact diff. Follow the runbook's existing isolated verification and draft
PR process where authorized; workers must stay within their assigned capabilities.
Local verification is not a substitute for code-owner review. The factory never
merges or deploys, and this guidance does not enable automatic promotion or change
the charter's autonomy tiers. Any change to those controls needs the existing
explicit owner review and authorization through a separate change process.

## Background

The recommendations here use the supplied summary of HumanLayer's workflow ideas:
research the live codebase, retain compact evidence instead of noisy history, keep
design decisions human-owned, and implement small vertical slices. Background
references are [HumanLayer resources](https://www.humanlayer.dev/blog/humanlayer-resources)
and [workflow phases](https://docs.humanlayer.com/explanation/workflow-phases).
These links are background reading, not authority over this repository's charter
or runbook; their contents were not fetched for this documentation-only task.
