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
- The repository is public per the owner's explicit choice; it contains source/research content only, with no production credentials or autonomous runner.
- `main` is protected: pull request required, one code-owner approval, stale approvals dismissed, last-push approval and conversation resolution required, and force-push/branch deletion blocked. No required CI status checks are configured yet.
- The owner `pc099` has an unexpired write invitation; acceptance is not independently verified. The `CODEOWNERS` rule requires that account's review.
- The workflow file remains a checked-in template at `docs/workflows/tests.yml.example`; the authorized GitHub CLI grant still needs the `workflow` scope before it can be installed.
- No secrets in tasks; no production credentials or host-wide mounts for future workers.
- Local state writes are atomic and transitions validate expected revision. The CLI-supplied `actor` and `claimed_by` values are labels, not authenticated identities; this prototype is not a multi-user authorization boundary.
- Future worker runs require disposable isolation, restricted network/filesystem, task-scoped credentials, hard time/cost limits, independently controlled tests, and an out-of-band kill switch.

## Explicit non-claims

Python filesystem state and a container are not a security sandbox against a same-user hostile process. Passing tests is not proof of correctness. The initial prototype is not yet production-safe for agent execution. Before enabling workers, add and adversarially test the actual isolation boundary and GitHub authorization model.
