# Architecture

## Current implementation (execution slice)

The Python CLI and JSON ledger accept and list tasks, atomically claim an exact task ID, and validate state transitions. `factory task-run` now wires a bounded Codex CLI worker, a disposable Git worktree, a separate Docker pytest verifier, and a GitHub draft-PR publisher. The executor rejects non-low-risk tasks, dirty source checkouts, protected-path edits, symlinks, malformed diffs, and heuristic credential-pattern matches. It refreshes `origin/<base>`, verifies the configured GitHub remote, checks write permission and server-enforced human review, then creates and reads back an exact-commit draft PR.

The worker is launched with Codex CLI's `workspace-write` sandbox, a dedicated `CODEX_HOME`, a restricted process environment, and a fresh empty `GH_CONFIG_DIR`. The verifier is a distinct container with `--network=none`, a read-only checkout, dropped capabilities, no-new-privileges, resource limits, and writable temporary storage. Coordinator-owned Git commits and publication happen only after verification.

The public repository does not yet have active CI checks. The runner has not yet completed a live authenticated Codex task or created a PR end-to-end; the relevant local test suite and real Docker verifier have passed, and live GitHub preflight should be checked before use. The worker is not a hardened VM boundary: its host-side sandbox, network controls, and credential exposure have not received adversarial validation.

## Flow

```text
Human specification → metadata queue → deterministic preflight/policy → exact-ID claim
   → disposable Git worktree → Codex CLI workspace-write sandbox
   → protected-path, symlink, whitespace, and heuristic secret checks
   → network-disabled Docker pytest verifier → coordinator-owned commit
   → exact-SHA draft PR → human review/merge/deploy
```

## Components and boundaries

- **Task ledger:** canonical task IDs, immutable intent, acceptance criteria, risk tier, revision, status, timestamps. The repository and durable task backend—not model conversation history—own status. A task's `claimed_by` field is an operator-supplied label, not authentication or authorization.
- **Coordinator:** preflights worker, verifier, GitHub permissions, branch protection, source cleanliness, remote identity, and base ref before claiming. It owns the commit, publication, manifest, and state transitions. Crash recovery, leases, kill switch, concurrency limits, and spend budgets remain future work.
- **Worker adapter:** Codex CLI only for now. Each task gets a disposable worktree and a dedicated Codex home. The worker does not receive GitHub tokens or inherited host API-token environment variables. The Codex authentication material itself is still sensitive; the sandbox is not claimed as a hardened multi-tenant boundary.
- **Verification:** pytest runs in a fixed local Docker image with no network and a read-only checkout. Tests and policy files are protected from worker changes. The result is bound to the base and exact commit SHA in the manifest and draft-PR readback.
- **Publication:** the publisher requires GitHub write access and at least one enforced human approval (including admins) on the base branch. It pushes only a verified task branch and creates a draft PR. It cannot merge or deploy.
- **Decision model (optional Jev):** closed-set routing/risk/evidence classification only. First run shadow-only; no permission, merge, deployment, or policy-waiver authority.
- **Observability:** secret-free run manifests bind task ID, attempt ID, branch, base SHA, commit SHA, draft-PR URL, verification summary, and completion time. Manifests use restrictive local file permissions.

## Authority model

Policy is enforced outside the model loop. The worker cannot change the coordinator, task ledger, CLI, tests, CI workflows, charter, or threat model and then treat its own altered checks as passing. GitHub credentials remain in the post-verification publisher process, not the worker. The worker still uses its dedicated Codex identity; treat that auth material as sensitive. Every privileged operation has its own authorization boundary.

## Why not install a large orchestrator immediately?

The source review suggests the queue, policy, evidence, and review boundary matter more than adding agent count. Avoid duplicated task state, retry logic, and competing control planes. Add durable coordination or multiple workers only after measured requirements justify them.
