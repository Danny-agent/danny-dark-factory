# Roadmap

## Phase 0 — policy and local queue

- [x] Create repository, invite `pc099` with write access, and publish it with owner approval.
- [x] Define owner-controlled charter, architecture, and threat model.
- [x] Implement local task ledger with atomic claim/transition behavior and tests.
- [x] Protect `main` with code-owner review and no force-push/deletion; CI status checks remain to be added.
- [x] Push foundation, research, and hardening changes to `main`.
- [ ] Owner accepts the pending contributor invitation.
- [ ] Install CI workflow after the authorized GitHub `workflow` scope is available.

## Phase 1 — human-triggered isolated draft PR (implemented locally; review/integration pending)

- [x] Add exact-ID queue execution, disposable Git worktrees, Codex CLI adapter, deterministic policy checks, and coordinator-owned commits.
- [x] Add network-disabled, read-only Docker pytest verifier and pinned Python base image.
- [x] Require GitHub write access and server-enforced human approval on the target base; verify exact PR base/head SHA; never merge or deploy.
- [x] Verify the local suite on the host and with the real Docker verifier.
- [ ] Merge the code through the protected review path; live Codex task execution and authenticated PR publication remain unverified.
- [ ] Adversarially validate the host-side Codex sandbox, credential visibility, and network behavior before using untrusted tasks.
- [ ] Add required CI and reconciliation/recovery behavior before routine operation.

## Phase 2 — controlled task intake and durability

- GitHub issue/label intake, remote authentication, durable leases, crash recovery, stop switch, bounded concurrency, retries, and spend/time budgets.
- Protect CI/policy/tests from worker modifications and require exact-commit CI evidence.
- Human review and merge/deployment remain mandatory.

## Phase 3 — decision model evaluation (optional)

- Compare deterministic rules, a baseline classifier, and Jev on labeled historical tasks.
- Measure false-accept/false-reject rates and calibration by task class.
- Shadow mode first; route uncertainty to a human; no privilege escalation from scores.
- Confirm service hosting, privacy, terms, data retention, and cost constraints before use.

## Phase 4 — narrow promotion

Only consider owner-approved, reversible, low-risk classes after local evidence, verified rollback, and enforceable server-side rules. Human merge/deployment remains the default. No autonomous production deployment without a separate risk decision.
