# Roadmap

## Phase 0 — policy and local queue (in progress)

- [x] Create repository, invite `pc099` with write access, and publish it with owner approval.
- [x] Define owner-controlled charter, target architecture, and threat model.
- [x] Implement local task ledger with atomic claim/transition behavior and tests.
- [x] Protect `main` with code-owner review and no force-push/deletion; CI status checks remain to be added.
- [x] Push the foundation, research, and hardening changes to `main`.
- [ ] Owner accepts the pending contributor invitation.
- [ ] Add the CI workflow after the owner-authorized workflow scope is available.

## Phase 1 — human-triggered isolated draft PR

- Choose one bounded task class and one agent runtime after a disposable-repo spike.
- Use per-task branch/worktree/container; no shared writable checkout.
- Verify exact commit SHA and run evidence; stop at draft PR.
- No Jev decisions controlling execution; optional shadow-only evaluation.

## Phase 2 — controlled task intake

- GitHub issue/label intake, atomic claims, bounded concurrency and retries.
- Durable run records, stop switch, recovery reconciliation, and back-pressure.
- Separate worker and verifier; protect CI/policy/tests from worker modifications.

## Phase 3 — decision model evaluation (optional)

- Compare deterministic rules, a baseline classifier, and Jev on labeled historical tasks.
- Measure false-accept/false-reject rates and calibration by task class.
- Shadow mode first; route uncertainty to human; no privilege escalation from scores.
- Confirm service hosting, privacy, terms, data retention, and cost constraints before use.

## Phase 4 — narrow promotion

Only consider owner-approved, reversible, low-risk classes after local evidence, verified rollback, and enforceable server-side rules. Human merge/deployment remains default. No autonomous production deployment without a separate risk decision.
