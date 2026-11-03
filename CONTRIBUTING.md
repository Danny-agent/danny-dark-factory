# Contributing

This repository contains a local task ledger plus a human-triggered execution path that stops at a draft pull request. The live authenticated Codex and GitHub publishing integrations have not yet been exercised end-to-end; do not treat this as a production or multi-tenant service.

## Local development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q

docker build --pull=false --tag danny-dark-factory-verifier:0.1.0 \
  --file docker/verify.Dockerfile docker/
```

Contributions should include tests for lifecycle, exact-base behavior, isolation flags, and failure handling; update the threat model for new capabilities; and keep externally sourced claims linked/cited in `docs/RESEARCH.md`.

## Security and review rules

- Never add credentials, private keys, access tokens, or production secrets to the repository, issues, fixtures, or logs.
- Do not grant worker tokens repository administration, secrets access, branch-protection edits, merge, release, or deployment permissions.
- The Codex worker uses a generated `factory-worker` permission profile: task-workspace-only writes, read-only Git metadata, disabled sandbox networking/web search, per-run `HOME`, and an allowlisted environment. Local probes cover direct and `/proc` auth-file reads, Git metadata writes, Docker/systemd sockets, network-interface access, and task-workspace writes; they are not proof against sandbox, runtime, or kernel escape. Keep the dedicated Codex auth material sensitive and require human review; do not claim a hardened multi-tenant boundary.
- The Docker verifier is network-disabled and uses a read-only source mount; do not treat shared-kernel containers as protection against a kernel escape.
- The runner rejects edits to tests, CI, coordinator, charter, threat model, and project configuration. Changes to those controls require separate human-authored review.
- Every factory PR must identify its task, list acceptance criteria and test evidence, and remain draft until a human review/merge decision.
- We measure accepted, correct work and defects/rework—not agent activity, lines, or PR count.
