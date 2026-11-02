from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from dark_factory.store import QueueError, TaskStore


def new_store(tmp_path: Path) -> TaskStore:
    store = TaskStore(tmp_path / "state")
    store.initialize()
    return store


def test_add_claim_and_transition_with_audit_events(tmp_path):
    store = new_store(tmp_path)
    task = store.add("Docs fix", "Add source-backed docs.", "low", ["validator passes"])
    claimed = store.claim("worker-a")
    assert claimed["id"] == task["id"]
    assert claimed["status"] == "claimed"
    advanced = store.transition(task["id"], "planning", "worker-a", claimed["revision"])
    assert advanced["status"] == "planning"
    assert [event["to"] for event in advanced["events"]] == ["queued", "claimed", "planning"]


def test_claim_is_atomic_under_concurrent_workers(tmp_path):
    store = new_store(tmp_path)
    store.add("Only one wins", "Claim safely.", "low", ["one claimant"])
    def compete(name: str, root: str, results):
        results.put(TaskStore(root).claim(name))
    ctx = multiprocessing.get_context("fork")
    results = ctx.Queue()
    workers = [ctx.Process(target=compete, args=(f"w{i}", str(store.root), results)) for i in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert all(not worker.is_alive() for worker in workers)
    assert [worker.exitcode for worker in workers] == [0] * len(workers)
    claims = [results.get(timeout=1) for _ in workers]
    assert sum(result is not None for result in claims) == 1


def test_compare_and_swap_rejects_stale_revision(tmp_path):
    store = new_store(tmp_path)
    task = store.add("Stale", "Check CAS.", "low", ["revision checked"])
    store.claim("worker-a")
    with pytest.raises(QueueError, match="stale revision"):
        store.transition(task["id"], "planning", "worker-a", 1)


def test_invalid_transitions_fail_closed(tmp_path):
    store = new_store(tmp_path)
    task = store.add("No skip", "Do not skip verification.", "low", ["policy respected"])
    with pytest.raises(QueueError, match="invalid transition"):
        store.transition(task["id"], "accepted", "worker-a", task["revision"])


def test_corrupt_store_is_not_overwritten(tmp_path):
    store = new_store(tmp_path)
    store.data_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(QueueError, match="Cannot read task ledger"):
        store.add("Must stop", "Do not reset corruption.", "low", ["state preserved"])
    assert store.data_path.read_text(encoding="utf-8") == "{broken"


def test_unknown_risk_and_blank_acceptance_are_rejected(tmp_path):
    store = new_store(tmp_path)
    with pytest.raises(QueueError, match="risk must be"):
        store.add("bad", "desc", "critical", ["ok"])
    with pytest.raises(QueueError, match="must not be empty"):
        store.add("bad", "desc", "low", [" "])


def test_cli_initialization_and_task_lifecycle(tmp_path):
    from dark_factory.cli import main
    state = tmp_path / "cli-state"
    assert main(["init", str(state)]) == 0
    assert main(["task-add", str(state), "--title", "CLI task", "--description", "safe", "--risk", "low", "--acceptance", "test passes"]) == 0
    payload = json.loads((state / "tasks.json").read_text())
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["status"] == "queued"
