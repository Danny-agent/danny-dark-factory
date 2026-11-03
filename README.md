# Danny Dark Factory

A public, human-reviewed software factory. It accepts bounded *low-risk* coding tasks, gives a disposable Git worktree to the Codex CLI using its `workspace-write` sandbox, runs the repository's tests in a network-disabled/read-only Docker verifier, then publishes a **draft pull request**. It never merges or deploys.

The execution path is implemented and locally verified. Live Codex execution and live pull-request publication by the runner have not yet been exercised; use the preflight checks and review every draft PR.

- [Charter](docs/CHARTER.md) — authority boundaries and hard stops.
- [Architecture](docs/ARCHITECTURE.md) — components and data flow.
- [Threat model](docs/THREAT_MODEL.md) — assets, failure cases, and guardrails.
- [Runbook](docs/RUNBOOK.md) — setup, authentication, and operating steps.
- [Research](docs/RESEARCH.md) — evidence and component-selection rationale.
- [Roadmap](docs/ROADMAP.md) — staged adoption plan.
- [Contributing](CONTRIBUTING.md) — development and security rules.

## Quickstart

Requires Python 3.11+, Git, Docker, Codex CLI, and GitHub CLI (`gh`).

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
factory init ./factory-state

# Build the fixed, pinned-Python test image. Build context contains only docker/.
docker build --pull=false --tag danny-dark-factory-verifier:0.1.0 \
  --file docker/verify.Dockerfile docker/
```

Before running work, complete the official Codex CLI login in the dedicated home described in [the runbook](docs/RUNBOOK.md). Do not copy the general Codex home or GitHub credentials into the worker.

```sh
factory task-add ./factory-state \
  --title 'Implement one bounded change' \
  --description 'Describe the requested change and its scope.' \
  --risk low --acceptance 'Named tests pass'
factory task-list ./factory-state

# Replace <TASK_ID> and the paths with real values. The source checkout must be clean.
factory task-run ./factory-state <TASK_ID> \
  --repo /path/to/clean/checkout \
  --work-root /path/outside/the/checkout/factory-runs \
  --repo-slug OWNER/REPO --base main
```

The runner checks that the local `origin` is the configured GitHub repository, refreshes `origin/<base>`, requires a write-capable GitHub identity and a base branch protected by at least one enforced human approval, and leaves a secret-free run manifest in `factory-state/runs/`.

## Guardrails and limits

- Only low-risk queued tasks run automatically. Tasks and repository content are untrusted inputs; the worker prompt says so, but prompt wording is not a security boundary.
- Each attempt gets a disposable worktree. The coordinator owns the commit; workers cannot commit, push, change protected policy/CI/tests/coordinator files, or add symlinks.
- GitHub identity is not passed to the worker: the worker receives a fresh empty `GH_CONFIG_DIR`; publication runs afterward in the coordinator.
- Verification uses a pinned Python base image, no network, a read-only repository mount, dropped capabilities, no-new-privileges, resource limits, and a writable temporary filesystem. A heuristic staged-diff credential scan is defense-in-depth, not a guarantee.
- A failed run is marked `blocked` for operator review. A successful run creates a draft PR only. Human review, merge, and deployment remain outside the factory.
- `main` requires a pull request, code-owner approval, last-push approval, and resolved conversations; force-pushes and branch deletion are disabled. Required CI checks are not configured yet; [the workflow template](docs/workflows/tests.yml.example) still needs the separately authorized `workflow` scope to install.
- The current worker process uses Codex CLI's `workspace-write` sandbox on the host, not a separate VM. Do not treat the current implementation as a hardened multi-tenant security boundary or use it with untrusted/high-impact tasks.
- Task descriptions must not contain secrets or production credentials.
