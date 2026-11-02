# Architecture

## Current implementation (Phase 0)

The prototype is a local Python CLI and JSON state store. It creates tasks, lists them, atomically claims eligible work, and validates state transitions. It does not run arbitrary commands, invoke a model, call Jev, or write to GitHub. This provides a small executable place to test task identity, idempotency, and state rules before adding agent authority.

## Target flow

```text
Human specification → intake/queue → deterministic policy → bounded coordinator
   → ephemeral per-task worker/worktree → tests + security checks + independent verifier
   → evidence-backed draft PR → human merge/deploy → outcome telemetry
```

### Components and boundaries

- **Task ledger:** canonical task IDs, immutable intent, acceptance criteria, risk tier, revision, status, timestamps. The repository and durable task backend—not model conversation history—own status. A task's `claimed_by` field is an operator-supplied label, not authentication or authorization.
- **Coordinator:** atomic claims, leases/fencing, retries, stop switch, concurrency and spend/time budgets. Start with a local runner/GitHub Actions; add Temporal only when observed durability requirements warrant it.
- **Worker adapter:** OpenHands or a CLI-based coding agent may be evaluated; one adapter at a time. Each task gets a disposable workspace with narrow filesystem/network permissions.
- **Verification:** required deterministic checks plus independent review/evidence. CI result must correspond to exact commit and environment. Tests/policy files are protected.
- **Decision model (optional Jev):** closed-set routing/risk/evidence classification only. First run shadow-only; no permission, merge, deployment, or policy-waiver authority.
- **Handoff:** draft PR includes requirements, diff summary, exact commit, checks, logs/artifacts, uncertainty, and remaining risks. Human owns merge and deployment.
- **Observability:** traces and run records are secret-free and bind each result to task ID, attempt ID, commit SHA, tool/model version, and verification environment.

## Authority model

Policy is enforced outside the model loop. Workers never receive owner credentials, repository-admin permissions, or production secrets. A worker cannot change its charter, verification gates, CI permissions, or protected paths and then treat its own altered checks as passing. Every privileged operation has its own authorization boundary.

## Why not install a large orchestrator immediately?

The source review suggests the queue, policy, evidence, and review boundary matter more than adding agent count. Avoid duplicated task state, retry logic, and competing control planes. The initial CLI validates a narrow foundation; each later service is added only after a measurable requirement and tested integration boundary.
