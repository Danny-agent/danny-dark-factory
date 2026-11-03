"""Task CLI with automatic low/medium-risk execution ending at a draft PR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .execution import CodexAgent, DockerPytestVerifier, ExecutionError, GitHubPublisher, TaskExecutor
from .store import ACTIVE_TASK_STATES, AUTOMATIC_ACTOR, QueueError, TaskStore


def _add_execution_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repo", type=Path, required=True, help="clean local checkout to modify")
    command.add_argument("--work-root", type=Path, required=True, help="private directory outside the checkout")
    command.add_argument("--repo-slug", required=True, help="GitHub OWNER/REPO for the draft PR")
    command.add_argument("--base", default="main", help="GitHub base branch (default: main)")
    command.add_argument(
        "--codex-home", type=Path,
        default=Path.home() / ".local/share/danny-dark-factory/codex-home",
        help="isolated Codex CLI auth home; never reuse the host's general credential home",
    )
    command.add_argument("--agent-timeout", type=int, default=1800)
    command.add_argument("--verification-image", default="danny-dark-factory-verifier:0.1.0")
    command.add_argument("--verification-timeout", type=int, default=300)


def _create_runner(args: argparse.Namespace, store: TaskStore, *, actor: str = "factory") -> TaskExecutor:
    return TaskExecutor(
        store=store,
        repo_root=args.repo,
        work_root=args.work_root,
        agent=CodexAgent(timeout_s=args.agent_timeout, codex_home=args.codex_home, repo_root=args.repo),
        verifier=DockerPytestVerifier(
            image=args.verification_image,
            timeout_s=args.verification_timeout,
        ),
        publisher=GitHubPublisher(args.repo_slug, base=args.base),
        base_ref=f"origin/{args.base}",
        actor=actor,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Automatically process queued low/medium-risk coding tasks to human-reviewed draft PRs",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="initialize local factory state")
    init.add_argument("state_dir", type=Path)
    add = sub.add_parser("task-add", help="submit a bounded task")
    add.add_argument("state_dir", type=Path)
    add.add_argument("--title", required=True)
    add.add_argument("--description", required=True)
    add.add_argument(
        "--risk", choices=["low", "medium", "high"], required=True,
        help="low/medium tasks are auto-dispatched; high-risk tasks remain manual",
    )
    add.add_argument("--acceptance", action="append", required=True)
    listing = sub.add_parser("task-list", help="list tasks")
    listing.add_argument("state_dir", type=Path)
    claim = sub.add_parser("task-claim", help="atomically claim the next queued task")
    claim.add_argument("state_dir", type=Path)
    claim.add_argument("--actor", required=True)
    transition = sub.add_parser("task-transition", help="advance a task using compare-and-swap revision")
    transition.add_argument("state_dir", type=Path)
    transition.add_argument("task_id")
    transition.add_argument("target")
    transition.add_argument("--actor", required=True)
    transition.add_argument("--expected-revision", type=int, required=True)

    run = sub.add_parser("task-run", help="execute one low/medium-risk task by ID to a draft PR")
    run.add_argument("state_dir", type=Path)
    run.add_argument("task_id")
    _add_execution_options(run)

    run_next = sub.add_parser(
        "task-run-next",
        help="automatically execute the oldest queued low/medium-risk task; one task per invocation",
    )
    run_next.add_argument("state_dir", type=Path)
    _add_execution_options(run_next)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TaskStore(args.state_dir)
    try:
        if args.command == "init":
            store.initialize()
            print(f"initialized state at {store.root}")
            return 0
        if args.command in {"task-run", "task-run-next"}:
            store.initialize()
            actor = "factory:auto" if args.command == "task-run-next" else "factory"
            runner = _create_runner(args, store, actor=actor)
            result = runner.execute(args.task_id) if args.command == "task-run" else runner.execute_next()
            if result is None:
                busy = any(item["status"] in ACTIVE_TASK_STATES for item in store.list())
                if busy:
                    print(json.dumps({"status": "busy", "message": "a task claim is active or awaiting safe recovery"}))
                else:
                    print(json.dumps({"status": "idle", "message": "no queued low/medium-risk tasks"}))
                return 0
            print(json.dumps(result.__dict__, indent=2))
            return 0
        if args.command == "task-add":
            task = store.add(args.title, args.description, args.risk, args.acceptance)
            print(json.dumps(task, indent=2))
            return 0
        if args.command == "task-list":
            print(json.dumps(store.list(), indent=2))
            return 0
        if args.command == "task-claim":
            task = store.claim(args.actor)
            if task is None:
                busy = any(item["status"] in ACTIVE_TASK_STATES for item in store.list())
                if busy:
                    print(json.dumps({"status": "busy", "message": "a task claim is already active"}))
                else:
                    print("no queued tasks")
                return 0
            print(json.dumps(task, indent=2))
            return 0
        if args.command == "task-transition":
            task = store.transition(args.task_id, args.target, args.actor, args.expected_revision)
            print(json.dumps(task, indent=2))
            return 0
        return 2
    except (QueueError, ExecutionError) as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
