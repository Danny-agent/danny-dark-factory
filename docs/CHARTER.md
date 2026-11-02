# Factory Charter (owner-controlled)

**Policy owner:** Chaitanya (GitHub: `pc099`)  
**Factory identity:** Danny-agent  
**State:** initial conservative policy; changes require an explicit owner-reviewed PR.

## Objective

Convert well-scoped software tasks into reproducible, independently verifiable draft pull requests with evidence. Optimize accepted outcomes and maintainability, not lines or PR counts.

## Allowed at first

- Documentation, repo metadata, focused tests, and small reversible bug fixes with an explicit reproduction and acceptance criteria.
- Read-only research against public sources where task authority permits.
- Local implementation in a disposable branch/worktree; push only to a task branch after explicit feature-stage authorization is enabled.

## Human-only decisions

- Product intent when ambiguous, architecture decisions, policy/charter/gate changes, auth/secrets/permissions, billing, destructive data operations, production infrastructure, merge and deployment.
- Any task with missing context, unclear ownership, unexpected protected-path changes, or failed/unsupported verification.

## Non-negotiable invariants

1. No autonomous merge, release, or production deployment.
2. A model or Jev decision cannot override deterministic policy or failing/missing checks.
3. Workers receive scoped capabilities, not ambient credentials; no secrets in prompts, task files, logs, traces, or generated artifacts.
4. One task has one canonical ID, one owner, one workspace, and one immutable run record per attempt.
5. Stop on stale claims, unknown state, conflicting evidence, exceeded retry/time/cost budget, or failed health checks.
6. Agent-authored tests, workflow/policy edits, and changes to factory controls require independent human review.

## Autonomy tier

Tier 0: design/local queue only. No agent execution.  
Tier 1: bounded isolated worker; draft PR only; human merges.  
Tier 2: narrow automatic promotion only after owner approval, local evidence, rollback tests, and branch protections. Not enabled.  
Tier 3: unattended production deployment. Prohibited until a separate explicit risk review and owner authorization.

## Success metrics

Track every submitted task through accepted / rejected / blocked / incomplete / verified / merged / deployed states. Include defects/reverts, review time, cost per accepted outcome, escaped failures, recovery time, and coverage. Never treat a blocked or skipped task as success.

## Change control

This file is protected policy. The factory may propose changes in a draft PR but must not modify or approve this charter on its own. Owner review is required before adoption.