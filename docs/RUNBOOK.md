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

   Complete authentication using Codex CLI's official flow. Never paste device codes or tokens into chat, tasks, logs, or repository files. The runner's preflight uses this same directory and writes a private per-task `factory-worker.config.toml` permission profile. To prevent unreviewed Codex extensions/settings from widening the worker, the existing `config.toml` may contain only `projects.<absolute-path>.trust_level` entries; any other key or unsafe permissions cause fail-closed preflight. Do not add MCP servers, hooks, notifications, or custom providers to this dedicated home.

5. Authenticate `gh` as an identity with write access to the target GitHub repository. The publisher independently checks that the configured base branch has at least one required approval and that admin enforcement is enabled. It will refuse unprotected bases.

## Run one task

Use a clean checkout whose `origin` is exactly the configured GitHub repository, with a clean work directory outside that checkout. Task descriptions are untrusted input; do not include secrets. The automatic dispatcher accepts queued low- and medium-risk tasks. Keep high-risk, security, authentication, destructive, production, and policy tasks out of the automatic queue. Tasks must be bounded and have explicit acceptance criteria.

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

For dispatch, the ledger atomically claims the selected low/medium-risk task before preflight; if preflight fails, the claim is released back to `queued`. Automatic claimants record Linux PID plus `/proc` start-time ticks in the `claimed_by` label; this is a liveness signal, not authentication. A later automatic invocation does not recover or start another task while that identity is live or unknown. Only after the owner identity is confirmed absent does the age gate apply; age is measured from the task's latest `updated_at` status transition, so a backward wall-clock adjustment delays recovery rather than enabling it. A `claimed` preflight-stage task is requeued after 15 minutes; `planning`, `implementing`, or `verifying` tasks are blocked after two hours so uncertain publication is not repeated. Legacy automatic claims without start-time identity require manual inspection. The systemd service's cgroup lifetime keeps the timer from overlapping lingering descendants; use the supervised service for unattended execution. After preflight, the runner fetches the latest `origin/<base>`, creates a task worktree, launches Codex with the built-in workspace-write sandbox, rejects changes to protected policy/CI/ownership/deployment paths, symlinks, and worktree Git-pointer changes, runs tests in the isolated Docker verifier, scans changed staged Git blobs for common secret patterns (64 MiB total limit; Git LFS/submodule changes are blocked), creates a coordinator-owned commit, pushes a task branch, and opens a draft PR. Source/test changes may be included in that proposal, but are not trusted until human review. It reads the PR back and checks the exact base/head SHA before marking the task `draft_pr`.

**Autonomy tier.** The dispatcher atomically claims the oldest queued low- or medium-risk task and processes at most one automatic task at a time, without per-task confirmation. A live or unidentifiable automatic claim makes later invocations return `busy`; they cannot recover or select another task. Age thresholds are measured from the task's latest `updated_at` status transition and apply only after the recorded PID plus `/proc` start-time identity is confirmed absent. A preflight claim is requeued after 15 minutes; an execution-stage claim is blocked after two hours. Legacy claims without start-time identity conservatively stall automatic work until manual inspection. The timer's systemd service cgroup prevents it from starting again while descendants remain active; use the supervised service for unattended execution. When activated, the user timer waits five minutes before its first run, then schedules 30 minutes after the oneshot service enters the inactive state; `AccuracySec=1min` allows coalescing. Actual start-to-start spacing is run duration plus the timer delay and possible accuracy coalescing. High-risk tasks remain queued. A preflight failure requeues the same oldest task, so it is retried on the next timer run and may hold later tasks until the global preflight issue is resolved. Each successful run ends at a verified draft PR; all draft PRs require human review before merge, and production deployment remains a separate human action.

```sh
factory task-run-next ./factory-state \
  --repo /path/to/clean/checkout \
  --work-root /path/outside/checkout/factory-runs \
  --repo-slug OWNER/REPO \
  --base main
```

`task-run-next` exits successfully with `status: idle` when no low/medium-risk queued task exists, and `status: busy` whenever any task claim is active or awaiting safe recovery. Manual and automatic claims share one atomic execution slot, so a manual claim blocks automatic dispatch and an automatic claim blocks manual claims. A busy invocation does not claim another task; automatic stale-claim recovery is paused while a manual claim is active. A preflight failure releases the task claim back to `queued`, so a later invocation retries the same oldest item; this can delay later tasks until the global preflight issue is resolved. Execution failures after preflight block the task for review. The direct CLI command above is a manual one-shot and does not receive the service cgroup guarantee; unattended dispatch must use the supervised systemd service/timer.

Install the timer only after the exact implementation PR has received its required code-owner approval and has been merged to `main`. Danny does not change a draft to ready, approve, or merge this PR. The commands below verify a merge, a current-head approval by configured owner `pc099`, the exact-commit `.github/CODEOWNERS` rule that assigns all paths to `pc099`, and branch protection requiring code-owner review with admin enforcement. The setup deliberately requires `origin/main` to equal this exact reviewed merge commit at installation; if `main` has advanced, stop and re-review/update the pin rather than bypassing the check. The unit also enforces a locally recorded `FACTORY_RUNNER_SHA` on every invocation: Git `HEAD` must equal that pin, the checkout must be clean, and `origin` must match the repository URL. The systemd unit uses `ExitType=cgroup` and `KillMode=control-group` (requires systemd 250+): the timer treats lingering descendants as part of the active service, and the 45-minute systemd runtime limit / explicit service stop terminates the unit cgroup after the stop grace period. The wrapper's own dispatcher deadline is 44 minutes; on timeout it sends `SIGTERM` to its process group, waits three seconds, then sends `SIGKILL` if live group members remain, waits for live members to exit, and warns if any remain. Zombie members are excluded from liveness checks because they cannot execute; the service cgroup is the stronger boundary for descendants that create new sessions. The dedicated checkout is fixed at `$HOME/projects/danny-dark-factory-runner` because the unit template pins `ExecStart` to `%h/projects/danny-dark-factory-runner`; do not change `RUNNER_ROOT` alone. The example environment file contains literal `/home/hermes` paths; replace those path prefixes if this account's home differs (systemd does not expand `$HOME` in `EnvironmentFile` values). Keep state/work/Codex directories as disjoint strict descendants of `~/.local/share/danny-dark-factory/`; the environment file contains paths and repository identifiers only.

```bash
set -euo pipefail
RUNNER_ROOT="$HOME/projects/danny-dark-factory-runner"
# Keep this exact path: the reviewed unit template uses this %h-relative checkout.
test "$RUNNER_ROOT" = "$HOME/projects/danny-dark-factory-runner"
grep -Fqx 'ExecStart=/usr/bin/python3 %h/projects/danny-dark-factory-runner/scripts/auto_run_once.py' deploy/systemd/danny-dark-factory-auto-run.service
mkdir -p "$HOME/projects" "$HOME/.config/systemd/user"
install -d -m 700 "$HOME/.config/danny-dark-factory"
test ! -e "$RUNNER_ROOT"
git clone --branch main https://github.com/Danny-agent/danny-dark-factory.git "$RUNNER_ROOT"
cd "$RUNNER_ROOT"
test "$(pwd -P)" = "$RUNNER_ROOT"
test "$(git rev-parse --show-toplevel)" = "$RUNNER_ROOT"
test "$(git remote get-url origin)" = "https://github.com/Danny-agent/danny-dark-factory.git"

# Read back the exact merged PR, its reviewed head, and the owner approval.
PR_NUMBER=REPLACE_WITH_VERIFIED_MERGED_PR_NUMBER
case "$PR_NUMBER" in ''|*[!0-9]*) echo "Set PR_NUMBER to the merged PR's numeric ID" >&2; exit 2;; esac
PR_HEAD_SHA="$(gh pr view "$PR_NUMBER" --repo Danny-agent/danny-dark-factory \
  --json state,mergedAt,baseRefName,headRefOid,mergeCommit,reviewDecision \
  --jq 'select(.state == "MERGED" and .mergedAt != null and .baseRefName == "main" and .reviewDecision == "APPROVED") | .headRefOid')"
VERIFIED_MERGE_SHA="$(gh pr view "$PR_NUMBER" --repo Danny-agent/danny-dark-factory \
  --json state,mergedAt,baseRefName,headRefOid,mergeCommit,reviewDecision \
  --jq 'select(.state == "MERGED" and .mergedAt != null and .baseRefName == "main" and .reviewDecision == "APPROVED") | .mergeCommit.oid')"
[[ "$PR_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$VERIFIED_MERGE_SHA" =~ ^[0-9a-f]{40}$ ]]
OWNER_REVIEW_ROWS="$(gh api --paginate "repos/Danny-agent/danny-dark-factory/pulls/$PR_NUMBER/reviews" \
  --jq '.[] | select(.user.login == "pc099") | [.state, .commit_id, .submitted_at, (.id|tostring)] | @tsv')"
export PR_HEAD_SHA
OWNER_APPROVED="$(printf '%s\n' "$OWNER_REVIEW_ROWS" \
  | python3 -c 'import csv, os, sys; rows=[r for r in csv.reader(sys.stdin, delimiter="\t") if len(r)==4]; latest_owner_review=max(rows, key=lambda r: (r[2], int(r[3])), default=None); print("true" if latest_owner_review and latest_owner_review[0] == "APPROVED" and latest_owner_review[1] == os.environ["PR_HEAD_SHA"] else "false")')"
unset PR_HEAD_SHA
test "$OWNER_APPROVED" = true
CODEOWNERS_B64="$(gh api "repos/Danny-agent/danny-dark-factory/contents/.github/CODEOWNERS?ref=$VERIFIED_MERGE_SHA" --jq .content)"
export CODEOWNERS_B64
OWNER_RULES_VALID="$(python3 -c 'import base64, os; rows=[line.split() for line in base64.b64decode(os.environ["CODEOWNERS_B64"]).decode().splitlines() if line.strip() and not line.lstrip().startswith("#")]; print("true" if rows == [["*", "@pc099"]] else "false")')"
unset CODEOWNERS_B64
test "$OWNER_RULES_VALID" = true
BRANCH_PROTECTION="$(gh api repos/Danny-agent/danny-dark-factory/branches/main/protection \
  --jq 'if .enforce_admins.enabled == true and (.required_pull_request_reviews.required_approving_review_count // 0) >= 1 and .required_pull_request_reviews.require_code_owner_reviews == true then "review-gated" else empty end')"
test "$BRANCH_PROTECTION" = "review-gated"

git fetch --no-tags origin main
# Deliberately fail closed if main advanced after this exact merge; re-review and update the pin rather than silently using stale code.
test "$(git rev-parse origin/main)" = "$VERIFIED_MERGE_SHA"
git switch --detach "$VERIFIED_MERGE_SHA"
test "$(git rev-parse HEAD)" = "$VERIFIED_MERGE_SHA"
test -z "$(git status --porcelain --untracked-files=all)"

cp deploy/systemd/auto-run.env.example "$HOME/.config/danny-dark-factory/auto-run.env"
ENV_FILE="$HOME/.config/danny-dark-factory/auto-run.env"
chmod 600 "$ENV_FILE"
# Edit path values for this account and set FACTORY_RUNNER_SHA to exactly $VERIFIED_MERGE_SHA; do not put credentials in this file.
${EDITOR:-vi} "$ENV_FILE"
test "$(stat -c '%a' "$ENV_FILE")" = 600
grep -Fqx "FACTORY_REPO=$RUNNER_ROOT" "$ENV_FILE"
grep -Fqx "FACTORY_STATE=$HOME/.local/share/danny-dark-factory/self-hosting-state" "$ENV_FILE"
grep -Fqx "FACTORY_WORK_ROOT=$HOME/.local/share/danny-dark-factory/self-hosting-worktrees" "$ENV_FILE"
grep -Fqx "FACTORY_CODEX_HOME=$HOME/.local/share/danny-dark-factory/codex-home" "$ENV_FILE"
grep -Fqx "FACTORY_REPO_SLUG=Danny-agent/danny-dark-factory" "$ENV_FILE"
grep -Fqx "FACTORY_RUNNER_SHA=$VERIFIED_MERGE_SHA" "$ENV_FILE"
grep -Fqx "FACTORY_BASE=main" "$ENV_FILE"
cp deploy/systemd/danny-dark-factory-auto-run.service "$HOME/.config/systemd/user/"
cp deploy/systemd/danny-dark-factory-auto-run.timer "$HOME/.config/systemd/user/"
systemd-analyze --user verify \
  "$HOME/.config/systemd/user/danny-dark-factory-auto-run.service" \
  "$HOME/.config/systemd/user/danny-dark-factory-auto-run.timer"
systemctl --user daemon-reload
systemctl --user show --property=ExecStart --value danny-dark-factory-auto-run.service \
  | grep -F "$RUNNER_ROOT/scripts/auto_run_once.py"

# Validate paths/modes without invoking the dispatcher or touching the task queue.
FACTORY_REPO="$RUNNER_ROOT" \
FACTORY_STATE="$HOME/.local/share/danny-dark-factory/self-hosting-state" \
FACTORY_WORK_ROOT="$HOME/.local/share/danny-dark-factory/self-hosting-worktrees" \
FACTORY_CODEX_HOME="$HOME/.local/share/danny-dark-factory/codex-home" \
FACTORY_REPO_SLUG="Danny-agent/danny-dark-factory" \
FACTORY_RUNNER_SHA="$VERIFIED_MERGE_SHA" \
FACTORY_BASE=main \
  /usr/bin/python3 "$RUNNER_ROOT/scripts/auto_run_once.py" --check-config
```

**Optional final opt-in — do not run until the implementation is reviewed and merged, configuration checks pass, and you intend automatic task execution.** The timer waits five minutes after activation for its first run, then schedules 30 minutes after the oneshot service becomes inactive; `AccuracySec=1min` may coalesce starts. The first run will execute the oldest eligible low/medium-risk task without a confirmation prompt.

```sh
systemctl --user enable --now danny-dark-factory-auto-run.timer
systemctl --user status danny-dark-factory-auto-run.timer
systemctl --user list-timers danny-dark-factory-auto-run.timer
```

Inspect `journalctl --user -u danny-dark-factory-auto-run.service`. Stop future starts with `systemctl --user disable --now danny-dark-factory-auto-run.timer`; stop an active task separately with `systemctl --user stop danny-dark-factory-auto-run.service`. Recovery age is measured from the latest task `updated_at` transition and requires the recorded Linux PID plus `/proc` start-time identity to be absent: preflight claims are requeued after 15 minutes and execution-stage tasks are blocked after two hours. A backward wall-clock adjustment delays recovery; a forward jump can classify a dead-owner claim early. A live or unidentifiable automatic claim blocks subsequent automatic runs; legacy claims without start-time identity fail closed and require manual inspection. The service cgroup keeps the timer from overlapping descendants; direct CLI invocations do not have that guarantee. Inspect the queue, manifest, worktree, and GitHub state before manually retrying a blocked task. The timer cannot merge or deploy; it only invokes `task-run-next`.

## Inspect and recover

- `factory task-list ./factory-state` shows queue state and complete task records.
- Secret-free run manifests are in `./factory-state/runs/<attempt-id>.json` (mode `0600`).
- Before push/PR creation, the coordinator writes a `publishing` manifest containing the task, attempt, branch, base SHA, commit SHA, and verification evidence. After GitHub readback, it updates the same manifest with the PR URL and `draft_pr` phase, then transitions the queue.
- A preflight failure requeues the task; the next invocation retries the same oldest eligible item and can delay later tasks until the global preflight issue is resolved. An execution failure after preflight marks it `blocked`; inspect the manifest and GitHub for a pushed branch or partially created PR before retrying.
- If the publisher verified an open draft PR but manifest/state finalization fails, the task is moved to `draft_pr` when possible and the error reports the PR URL. If the ledger remains inconsistent, use that URL plus the `publishing` manifest's branch/commit to reconcile before any retry.
- **Manual recovery of legacy or abandoned claims:** disable future starts (`systemctl --user disable --now danny-dark-factory-auto-run.timer`) and stop the service (`systemctl --user stop danny-dark-factory-auto-run.service`) before changing ledger state. Read `factory task-list ./factory-state`; verify no associated runner/worker is still active, then inspect the task's manifest, worktree, branch, and GitHub state for partial publication. Do not infer that an owner is dead from age alone when a legacy claim has no PID/start-time identity. For a `claimed`, `planning`, or `implementing` task that is safe to retry, set `TASK_ID`, `CLAIMED_BY`, and `REVISION` from the current ledger record, then run: `factory task-transition ./factory-state "$TASK_ID" queued --actor "$CLAIMED_BY" --expected-revision "$REVISION"`. For a `verifying` task, first transition it to `blocked` with the current claimant and revision; reconcile any publication state, then explicitly requeue the blocked task using its new revision and an operator actor. Read back `task-list` and verify the event/revision/status before any manual retry; leave the timer disabled until separately opting in again. These CLI transitions are local ledger operations, not an authentication boundary.
- `main` has no configured required CI checks yet. Local Docker verification is not a substitute for human code-owner review.

## Hard stops

The factory never merges or deploys. Do not use it for high-risk/security-sensitive changes, production credentials, destructive migrations, legal/financial commitments, or instructions that grant it extra authority. The worker profile has passed focused local denial/allow probes, but those do not validate escape resistance; `/proc/1/environ` remained readable inside the sandbox. Do not treat the host-side Codex/bubblewrap/seccomp layer as a hardened multi-tenant boundary. Review every draft PR and its exact diff before merge. Keep the timer disabled until the implementation is reviewed and merged, then enable it only with explicit owner opt-in.
