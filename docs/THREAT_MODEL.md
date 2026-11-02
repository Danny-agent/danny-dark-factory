# Threat model (initial)

## Assets

Source code, GitHub write access, owner identity, build/test integrity, task evidence, secrets, production systems, and reviewer attention.

## Threats

- Prompt injection in issues, repository files, comments, webpages, dependencies, or tool output.
- Worker modifies tests, policy, CI, or its own constraints to create false-green verification.
- Duplicate/stale worker claims, retry storms, lost state, incorrect completion, or stale code/environment validation.
- Secret disclosure through environment, mounted files, logs, artifacts, or network access.
- Malicious or compromised dependency/agent/runtime; unsafe shell and destructive operations.
- Review overload, rubber-stamping, or output metrics hiding escaped defects.

## Initial controls

- Phase 0 does not execute task instructions or launch agents.
- The repository is private and `pc099` has a pending GitHub write invitation. The owner has not accepted it yet (invite ID 341123189).
- GitHub rejected branch-protection access for this private repository's plan (requires Pro or repository visibility change). The repository remains private; no policy/visibility workaround was attempted. **Human review is documented but not yet enforced by server-side branch rules.**
- No CI workflow was pushed because the authorized GitHub token lacks the `workflow` scope; an owner-reviewable setup template is checked in at `docs/workflows/tests.yml.example`.
- No secrets in tasks; no prod credentials or host-wide mounts for future workers.
- Local state writes are atomic and transitions validate expected revision.
- Future worker runs require disposable isolation, restricted network/filesystem, task-scoped credentials, hard time/cost limits, independently controlled tests, and an out-of-band kill switch.

## Explicit non-claims

Python filesystem state and a container are not a security sandbox against a same-user hostile process. Passing tests is not proof of correctness. The initial prototype is not yet production-safe for agent execution. Before enabling workers, add and adversarially test the actual isolation boundary and GitHub authorization model.
