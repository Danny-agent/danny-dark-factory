# Threat model

## Assets

Source code, GitHub write access, Codex account material, build/test integrity, task evidence, secrets, production systems, and reviewer attention.

## Threats

- Prompt injection in task descriptions, issues, repository files, comments, dependencies, or tool output.
- Worker modifies tests, policy, CI, or its own constraints to create false-green verification.
- Duplicate/stale worker claims, retries, lost state, stale base refs, or incorrect completion.
- Secret disclosure through environment variables, Codex auth files, workspace contents, logs, artifacts, or network access.
- Malicious or compromised dependency/agent/runtime; unsafe shell, symlink escapes, or destructive operations.
- Review overload, rubber-stamping, or output metrics hiding escaped defects.
- A Docker/kernel or Codex sandbox escape affecting the shared host.

## Current controls

- The queue validates metadata and state transitions. Exact task IDs are atomically claimed; only low-risk queued tasks enter execution. Actor/claimant strings are labels, not authentication or authorization.
- The worker prompt treats task JSON as untrusted. The worker gets a disposable Git worktree, Codex CLI `workspace-write` sandbox, dedicated `CODEX_HOME` prepared through component-wise `O_NOFOLLOW` directory handles (root/runner-owned ancestors; mode `0700`), reduced environment, and a fresh empty `GH_CONFIG_DIR`. GitHub tokens and inherited host API-token variables are not passed to it.
- The coordinator rejects dirty source checkouts, mismatched GitHub origins, unavailable base refs, protected-path changes, worktree Git-pointer changes, worker-created symlinks, whitespace failures, submodule/LFS changes, and common credential patterns in staged Git blobs (maximum 64 MiB total). Credential scanning is heuristic and cannot guarantee that a diff is secret-free.
- The independent verifier uses a pinned Python base image, local image only (`--pull=never`), no network, read-only source mount, dropped capabilities, no-new-privileges, PID/CPU/memory limits, and a temporary filesystem.
- Before claiming work, preflight checks Codex login in the dedicated home, verifier-image availability, GitHub write permission, and base-branch protection. Failures leave the task queued. Later execution failures block the task for operator review.
- Coordinator-owned commits occur after verification. The publisher pushes only a verified branch and creates a draft PR, checks the exact base/head SHA readback, and never merges or deploys.
- Manifests are local, secret-free by design, and written with restrictive file permissions.
- `main` is protected: PR required, one code-owner approval, stale approvals dismissed, last-push approval and conversation resolution required, admin enforcement, and force-push/branch deletion blocked. No required CI checks are configured. `pc099`'s write invitation is still pending acceptance; the account's active permission remains read until accepted.
- The workflow file remains a checked-in template; the authorized GitHub CLI grant still needs the `workflow` scope before installation.

## Explicit non-claims

- The host-side Codex sandbox and network restrictions have not been adversarially validated. The worker uses a dedicated Codex identity whose authentication material is sensitive; do not assume it is unreadable to hostile code.
- Docker shares the host kernel. The container is a useful test-isolation boundary, not a proof against kernel/runtime escapes or hostile code in a multi-tenant production setting.
- Passing tests, heuristic secret scanning, and a draft PR are not proof of correctness or safety. Human review is mandatory.
- The live Codex task path and authenticated draft-PR publisher have not yet been exercised end-to-end; tests use fake CLI/GitHub commands for those integrations.
- This implementation is not yet a hardened multi-tenant service: there is no remote authentication layer, durable lease/recovery protocol, automated cost accounting, required CI check, or deployment authority.
