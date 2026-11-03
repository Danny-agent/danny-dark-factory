# Danny Dark Factory

A human-reviewed software factory. It accepts bounded low/medium-risk coding tasks, gives a disposable Git worktree to the Codex CLI with its generated `factory-worker` permission profile, runs repository tests in a network-disabled/read-only Docker verifier, then publishes a **draft pull request**. When enabled, the user timer waits five minutes after activation before its first run, then schedules 30 minutes after the oneshot service becomes inactive (`AccuracySec=1min` permits coalescing). Manual and automatic claims share one atomic execution slot. Automatic stale recovery requires the recorded coordinator PID plus `/proc` start-time identity to be confirmed absent as well as the age threshold (15 minutes for preflight claims; two hours for execution-stage claims); manual claims are never auto-recovered. Legacy automatic claims without a start-time identity require manual inspection. Every timer invocation checks the pinned runner SHA, clean checkout, and Git origin. High-risk tasks remain queued. Every draft requires human review before merge; the factory never auto-merges or deploys.

The baseline executor completed a docs-only task end-to-end. The new timer-driven automatic dispatcher has not been exercised end-to-end; the timer must only be enabled from a reviewed, merged runner checkout.

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

- Low/medium-risk queued tasks are automatically selected one at a time; high-risk tasks remain queued. Task and repository content are untrusted; prompt wording is not a security boundary.
- Each attempt gets a disposable worktree. The coordinator owns the commit; workers cannot commit, push, edit protected policy/CI/ownership/verifier/deployment files, or add symlinks. Workers may propose source, test, and factory-code changes; a green test result is not independent of test edits, so human review is mandatory.
- GitHub identity is not passed to the worker: the worker receives a fresh empty `GH_CONFIG_DIR`; publication runs afterward in the coordinator.
- The included user-level systemd timer waits five minutes after activation for its first run, then schedules 30 minutes after the oneshot service becomes inactive (`AccuracySec=1min` permits coalescing). It processes one eligible task per run when enabled. Disable the timer to prevent future starts; stop the service separately to abort an active run. It stores paths only, no credentials.
- Verification uses a pinned Python base image, no network, a read-only repository mount, dropped capabilities, no-new-privileges, resource limits, and a writable temporary filesystem. A heuristic staged-diff credential scan is defense-in-depth, not a guarantee.
- A preflight failure requeues the task; a failed execution after preflight is marked `blocked` for operator review. Automatic claims record Linux PID plus `/proc` start-time ticks in `claimed_by`; that identity is a conservative liveness signal, not authentication. A live or unidentifiable automatic owner blocks later dispatches; legacy claims without start-time identity require manual inspection. The systemd service's cgroup lifetime prevents timer overlap with lingering descendants. Recovery age is measured from the task's latest `updated_at` transition (15 minutes for preflight claims; two hours for execution stages); a backward wall-clock adjustment delays recovery. Successful runs stop at a verified draft PR; every draft requires human review before merge; deployment stays manual.
- `main` requires a pull request, code-owner approval, last-push approval, and resolved conversations; force-pushes and branch deletion are disabled. Required CI checks are not configured yet; [the workflow template](docs/workflows/tests.yml.example) still needs the separately authorized `workflow` scope to install.
- The worker uses Codex CLI's host-side `factory-worker` permission profile: deny-by-default filesystem access outside its task workspace, read-only repository Git metadata, disabled network and web search, per-run `HOME`, and a minimal environment. The outer Codex CLI still authenticates through the dedicated `CODEX_HOME`; focused sandbox probes blocked reads of that auth file (including via `/proc/1/root`), Git-metadata writes, Docker/systemd sockets, and network-interface inspection while allowing workspace writes and Git status. These checks are evidence for specific controls, not proof against Codex/bubblewrap/seccomp or kernel escapes. The sandbox helper's `/proc/1/environ` was readable, so the minimal environment is important; same-UID and escape resistance remain unproven. Keep human review mandatory and do not treat this as a hardened multi-tenant boundary or run adversarial/high-impact tasks.
- Task descriptions must not contain secrets or production credentials.
