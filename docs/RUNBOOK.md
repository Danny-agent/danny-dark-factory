# Operating runbook

## One-time setup

1. Install Python 3.11+, Git, Docker, Codex CLI, and GitHub CLI (`gh`).
2. From a clean checkout, install the project and run its tests:

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -e '.[dev]'
   pytest -q
   ```

3. Build the fixed verifier image. The build context is only `docker/` and the runner never pulls an image at verification time:

   ```sh
   docker build --pull=false --tag danny-dark-factory-verifier:0.1.0 \
     --file docker/verify.Dockerfile docker/
   docker image inspect danny-dark-factory-verifier:0.1.0
   ```

4. Authenticate Codex *only* in the factory's dedicated home. Do not copy the general Codex home or any GitHub credentials into it:

   ```sh
   FACTORY_CODEX_HOME="$HOME/.local/share/danny-dark-factory/codex-home"
   install -d -m 700 "$FACTORY_CODEX_HOME"
   HOME="$FACTORY_CODEX_HOME" CODEX_HOME="$FACTORY_CODEX_HOME" codex login
   HOME="$FACTORY_CODEX_HOME" CODEX_HOME="$FACTORY_CODEX_HOME" codex login status
   ```

   Complete authentication using Codex CLI's official flow. Never paste device codes or tokens into chat, tasks, logs, or repository files. The runner's preflight uses this same directory.

5. Authenticate `gh` as an identity with write access to the target GitHub repository. The publisher independently checks that the configured base branch has at least one required approval and that admin enforcement is enabled. It will refuse unprotected bases.

## Run one task

Use a clean checkout whose `origin` is exactly the configured GitHub repository, with a clean work directory outside that checkout. Task descriptions are untrusted input; do not include secrets. Start with bounded, reversible, low-risk code changes and explicit acceptance tests.

```sh
factory init ./factory-state
factory task-add ./factory-state \
  --title 'Implement one bounded change' \
  --description 'State the change, scope, and constraints.' \
  --risk low \
  --acceptance 'Named tests pass'
factory task-list ./factory-state

factory task-run ./factory-state <TASK_ID> \
  --repo /path/to/clean/checkout \
  --work-root /path/outside/checkout/factory-runs \
  --repo-slug OWNER/REPO \
  --base main
```

The runner performs preflight checks before claiming the task, fetches the latest `origin/<base>`, creates a task worktree, launches Codex with the built-in workspace-write sandbox, rejects protected-path edits, symlinks, and worktree Git-pointer changes, runs tests in the isolated Docker verifier, scans changed staged Git blobs for common secret patterns (64 MiB total limit; Git LFS/submodule changes are blocked), creates a coordinator-owned commit, pushes a task branch, and opens a draft PR. It reads the PR back and checks the exact base/head SHA before marking the task `draft_pr`.

## Inspect and recover

- `factory task-list ./factory-state` shows queue state and complete task records.
- Secret-free run manifests are in `./factory-state/runs/<attempt-id>.json` (mode `0600`).
- Before push/PR creation, the coordinator writes a `publishing` manifest containing the task, attempt, branch, base SHA, commit SHA, and verification evidence. After GitHub readback, it updates the same manifest with the PR URL and `draft_pr` phase, then transitions the queue.
- A preflight failure leaves the task queued. An execution failure before verified PR creation marks it `blocked`; inspect the manifest and GitHub for a pushed branch or partially created PR before retrying.
- If the publisher verified an open draft PR but manifest/state finalization fails, the task is moved to `draft_pr` when possible and the error reports the PR URL. If the ledger remains inconsistent, use that URL plus the `publishing` manifest's branch/commit to reconcile before any retry.
- `main` has no configured required CI checks yet. Local Docker verification is not a substitute for human code-owner review.

## Hard stops

The factory never merges or deploys. Do not use it for high-risk/security-sensitive changes, production credentials, destructive migrations, legal/financial commitments, or instructions that grant it extra authority. The Codex worker is host-side and the sandbox has not received adversarial validation; do not treat this as a hardened multi-tenant service. Review every draft PR and its exact diff before merge.
