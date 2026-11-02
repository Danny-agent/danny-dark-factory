"""Conservative local CLI; no agent execution in this release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import QueueError, TaskStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description="Dark Factory task ledger (metadata only)")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="initialize local factory state")
    init.add_argument("state_dir", type=Path)
    add = sub.add_parser("task-add", help="submit a bounded task")
    add.add_argument("state_dir", type=Path)
    add.add_argument("--title", required=True)
    add.add_argument("--description", required=True)
    add.add_argument("--risk", choices=["low", "medium", "high"], required=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TaskStore(args.state_dir)
    try:
        if args.command == "init":
            store.initialize()
            print(f"initialized state at {store.root}")
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
                print("no queued tasks")
                return 0
            print(json.dumps(task, indent=2))
            return 0
        if args.command == "task-transition":
            task = store.transition(args.task_id, args.target, args.actor, args.expected_revision)
            print(json.dumps(task, indent=2))
            return 0
        return 2
    except QueueError as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
