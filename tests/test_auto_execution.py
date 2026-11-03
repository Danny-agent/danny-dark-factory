from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dark_factory.cli import build_parser, main
from dark_factory.store import QueueError, TaskStore


def test_automatic_owner_identity_detects_pid_reuse(monkeypatch):
    import dark_factory.store as store_module

    monkeypatch.setattr(store_module, "_process_start_time_ticks", lambda pid: 812)

    assert not store_module._process_is_alive(321, 811)
    assert store_module._automatic_owner_identity("factory:auto:321") is None


def test_automatic_owner_identity_matches_current_process():
    import dark_factory.store as store_module

    start_ticks = store_module._process_start_time_ticks(os.getpid())

    assert store_module._process_is_alive(os.getpid(), start_ticks)
    assert not store_module._process_is_alive(os.getpid(), start_ticks + 1)


def test_automatic_selector_runs_queued_low_and_medium_tasks_in_order(tmp_path):
    import dark_factory.store as store_module

    store = TaskStore(tmp_path / "state")
    store.initialize()
    high = store.add("High", "Requires owner decision.", "high", ["reviewed"])
    medium = store.add("Medium", "Bounded change.", "medium", ["tests pass"])
    low = store.add("Low", "Another bounded change.", "low", ["tests pass"])

    selected = store.next_automatic_task()

    assert selected is not None
    assert selected["id"] == medium["id"]
    claimed = store.claim_next_automatic_task("factory:auto")
    assert claimed is not None
    assert claimed["id"] == medium["id"]
    assert claimed["status"] == "claimed"
    assert claimed["claimed_by"] == (
        f"factory:auto:{os.getpid()}:{store_module._process_start_time_ticks(os.getpid())}"
    )
    store.transition(claimed["id"], "blocked", claimed["claimed_by"], claimed["revision"])
    next_task = store.next_automatic_task()
    assert next_task is not None
    assert next_task["id"] == low["id"]
    assert next(task for task in store.list() if task["id"] == high["id"])["status"] == "queued"


def test_automatic_and_manual_claims_share_one_active_task_slot(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    manual_task = store.add("Manual", "Run manually.", "high", ["reviewed"])
    automatic_task = store.add("Automatic", "Run automatically.", "low", ["tests pass"])

    manually_claimed = store.claim_task(manual_task["id"], "operator")
    assert manually_claimed["status"] == "claimed"
    assert store.claim_next_automatic_task("factory:auto") is None
    assert next(item for item in store.list() if item["id"] == automatic_task["id"])["status"] == "queued"

    store.transition(manually_claimed["id"], "queued", "operator", manually_claimed["revision"])
    automatically_claimed = store.claim_next_automatic_task("factory:auto")
    assert automatically_claimed is not None
    assert automatically_claimed["id"] == automatic_task["id"]

    remaining = store.add("Still queued", "Do not claim during an active automatic run.", "low", ["tests pass"])
    with pytest.raises(QueueError, match="another task has an active claim"):
        store.claim_task(remaining["id"], "operator")
    assert store.claim("operator") is None
    assert next(item for item in store.list() if item["id"] == remaining["id"])["status"] == "queued"


def test_automatic_selector_uses_creation_time_not_storage_order(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    oldest = store.add("Oldest", "First authorized task.", "medium", ["tests pass"])
    newer = store.add("Newer", "Second authorized task.", "low", ["tests pass"])
    payload = json.loads(store.data_path.read_text(encoding="utf-8"))
    oldest_record = next(task for task in payload["tasks"] if task["id"] == oldest["id"])
    newer_record = next(task for task in payload["tasks"] if task["id"] == newer["id"])
    oldest_record["created_at"] = "2026-01-01T00:00:00+00:00"
    oldest_record["updated_at"] = oldest_record["created_at"]
    oldest_record["events"][0]["at"] = oldest_record["created_at"]
    newer_record["created_at"] = "2026-01-02T00:00:00+00:00"
    newer_record["updated_at"] = newer_record["created_at"]
    newer_record["events"][0]["at"] = newer_record["created_at"]
    payload["tasks"] = [newer_record, oldest_record]
    store.data_path.write_text(json.dumps(payload), encoding="utf-8")

    selected = store.next_automatic_task()
    assert selected is not None
    assert selected["id"] == oldest["id"]
    claimed = store.claim_next_automatic_task("factory:auto")
    assert claimed is not None
    assert claimed["id"] == oldest["id"]
    assert claimed["status"] == "claimed"
    next_task = store.next_automatic_task()
    assert next_task is not None
    assert next_task["id"] == newer["id"]


def test_automatic_selector_uses_storage_order_for_equal_timestamps(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    first = store.add("First", "First submitted.", "low", ["tests pass"])
    second = store.add("Second", "Second submitted.", "medium", ["tests pass"])
    payload = json.loads(store.data_path.read_text(encoding="utf-8"))
    equal_time = "2026-01-01T00:00:00+00:00"
    for task in payload["tasks"]:
        task["created_at"] = equal_time
        task["events"][0]["at"] = equal_time
    payload["tasks"] = list(reversed(payload["tasks"]))
    store.data_path.write_text(json.dumps(payload), encoding="utf-8")

    claimed = store.claim_next_automatic_task("factory:auto")

    assert claimed is not None
    assert claimed["id"] == second["id"]
    assert claimed["id"] != first["id"]


def test_automatic_claim_requeues_abandoned_preflight_claim(tmp_path, monkeypatch):
    import dark_factory.store as store_module

    monkeypatch.setattr(store_module, "_process_is_alive", lambda pid, start_ticks: False)
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("Recover claim", "Recover only an abandoned preflight claim.", "low", ["tests pass"])
    original = store.claim_next_automatic_task("factory:auto")
    assert original is not None

    before_threshold = datetime.fromisoformat(original["updated_at"]) + timedelta(minutes=15, seconds=-1)
    assert store.claim_next_automatic_task("factory:auto", now=before_threshold) is None
    assert next(item for item in store.list() if item["id"] == task["id"])["status"] == "claimed"

    at_threshold = datetime.fromisoformat(original["updated_at"]) + timedelta(minutes=15)
    recovered = store.claim_next_automatic_task("factory:auto", now=at_threshold)

    assert recovered is not None
    assert recovered["id"] == task["id"]
    assert recovered["status"] == "claimed"
    assert [event["to"] for event in recovered["events"]] == ["queued", "claimed", "queued", "claimed"]


def test_automatic_claim_blocks_stale_execution_and_advances_queue(tmp_path, monkeypatch):
    import dark_factory.store as store_module

    monkeypatch.setattr(store_module, "_process_is_alive", lambda pid, start_ticks: False)
    store = TaskStore(tmp_path / "state")
    store.initialize()
    stale = store.add("Stale run", "Do not retry an interrupted publication path.", "low", ["tests pass"])
    next_task = store.add("Next run", "Continue with the next eligible task.", "medium", ["tests pass"])
    claimed = store.claim_next_automatic_task("factory:auto")
    assert claimed is not None
    planning = store.transition(stale["id"], "planning", claimed["claimed_by"], claimed["revision"])
    running = store.transition(stale["id"], "implementing", planning["claimed_by"], planning["revision"])

    before_threshold = datetime.fromisoformat(running["updated_at"]) + timedelta(hours=2, seconds=-1)
    assert store.claim_next_automatic_task("factory:auto", now=before_threshold) is None
    assert next(item for item in store.list() if item["id"] == stale["id"])["status"] == "implementing"

    at_threshold = datetime.fromisoformat(running["updated_at"]) + timedelta(hours=2)
    selected = store.claim_next_automatic_task("factory:auto", now=at_threshold)

    records = {item["id"]: item for item in store.list()}
    assert records[stale["id"]]["status"] == "blocked"
    assert records[stale["id"]]["claimed_by"] is None
    assert records[stale["id"]]["events"][-1]["actor"] == "factory:auto-recovery"
    assert selected is not None
    assert selected["id"] == next_task["id"]
    assert selected["status"] == "claimed"


def test_live_preflight_claim_is_not_recovered_or_advanced(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    active = store.add("Long preflight", "Remain claimed while its coordinator is alive.", "low", ["tests pass"])
    later = store.add("Later task", "Wait for the active automatic run.", "medium", ["tests pass"])
    claimed = store.claim_next_automatic_task("factory:auto")
    assert claimed is not None and claimed["id"] == active["id"]

    selected = store.claim_next_automatic_task(
        "factory:auto", now=datetime.now(timezone.utc) + timedelta(minutes=16)
    )

    records = {item["id"]: item for item in store.list()}
    assert selected is None
    assert records[active["id"]]["status"] == "claimed"
    assert records[later["id"]]["status"] == "queued"


def test_live_execution_claim_is_not_blocked_or_advanced(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    active = store.add("Long execution", "Remain active while its coordinator is alive.", "low", ["tests pass"])
    later = store.add("Later task", "Wait for the active automatic run.", "medium", ["tests pass"])
    claimed = store.claim_next_automatic_task("factory:auto")
    assert claimed is not None and claimed["id"] == active["id"]
    planning = store.transition(active["id"], "planning", claimed["claimed_by"], claimed["revision"])
    store.transition(active["id"], "implementing", planning["claimed_by"], planning["revision"])

    selected = store.claim_next_automatic_task(
        "factory:auto", now=datetime.now(timezone.utc) + timedelta(hours=3)
    )

    records = {item["id"]: item for item in store.list()}
    assert selected is None
    assert records[active["id"]]["status"] == "implementing"
    assert records[later["id"]]["status"] == "queued"


@pytest.mark.parametrize(
    ("blocking_claimant", "live_pid"),
    [("factory:auto:200:1", 200), ("factory:auto", None)],
    ids=["live-owner", "unknown-owner"],
)
def test_automatic_recovery_waits_until_all_automatic_owners_are_safe(
    tmp_path, monkeypatch, blocking_claimant, live_pid
):
    import dark_factory.store as store_module

    monkeypatch.setattr(
        store_module, "_process_is_alive", lambda pid, start_ticks: live_pid is not None and pid == live_pid
    )
    store = TaskStore(tmp_path / "state")
    store.initialize()
    stale = store.add("Abandoned claim", "Wait until every automatic owner is safe to recover.", "low", ["tests pass"])
    live = store.add("Live claim", "Do not mutate recovery state while another worker lives.", "medium", ["tests pass"])
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    with store._locked():
        data = store._read()
        for item, actor in zip(
            data["tasks"], ("factory:auto:100:1", blocking_claimant), strict=True
        ):
            store._transition_record(item, "claimed", actor)
            item["claimed_by"] = actor
            item["updated_at"] = old
            item["events"][-1]["at"] = old
        store._write(data)

    selected = store.claim_next_automatic_task("factory:auto", now=datetime.now(timezone.utc))

    records = {item["id"]: item for item in store.list()}
    assert selected is None
    assert records[stale["id"]]["status"] == "claimed"
    assert records[stale["id"]]["claimed_by"] == "factory:auto:100:1"
    assert records[stale["id"]]["events"][-1]["to"] == "claimed"
    assert records[live["id"]]["status"] == "claimed"
    assert records[live["id"]]["claimed_by"] == blocking_claimant


def test_automatic_recovery_does_not_release_manual_claims(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("Manual claim", "Keep a human claim under human control.", "low", ["reviewed"])
    claimed = store.claim("operator")
    assert claimed is not None

    result = store.claim_next_automatic_task(
        "factory:auto", now=datetime.now(timezone.utc) + timedelta(days=1)
    )

    assert result is None
    current = next(item for item in store.list() if item["id"] == task["id"])
    assert current["status"] == "claimed"
    assert current["claimed_by"] == "operator"


def test_legacy_automatic_claim_without_pid_fails_closed(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    active = store.add("Legacy claim", "Do not infer a dead owner from a timestamp alone.", "low", ["tests pass"])
    later = store.add("Later task", "Do not overlap an unknown automatic owner.", "medium", ["tests pass"])
    claimed = store.claim("factory:auto")
    assert claimed is not None and claimed["id"] == active["id"]

    selected = store.claim_next_automatic_task(
        "factory:auto", now=datetime.now(timezone.utc) + timedelta(days=1)
    )

    records = {item["id"]: item for item in store.list()}
    assert selected is None
    assert records[active["id"]]["status"] == "claimed"
    assert records[later["id"]]["status"] == "queued"


def test_cli_reports_busy_for_live_automatic_claim(tmp_path, capsys):
    state = tmp_path / "state"
    store = TaskStore(state)
    store.initialize()
    task = store.add("Active", "Do not overlap this run.", "low", ["tests pass"])
    claimed = store.claim_next_automatic_task("factory:auto")
    assert claimed is not None and claimed["id"] == task["id"]

    result = main([
        "task-run-next", str(state),
        "--repo", str(tmp_path / "repo"),
        "--work-root", str(tmp_path / "work"),
        "--repo-slug", "Danny-agent/danny-dark-factory",
    ])

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "busy"
    assert store.list()[0]["status"] == "claimed"


def test_separate_processes_cannot_claim_the_same_single_task(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("One task", "Only one dispatcher may claim it.", "low", ["tests pass"])
    gate = store.root / "start-dispatchers"
    code = chr(10).join([
        "import sys, time",
        "from pathlib import Path",
        "from dark_factory.store import TaskStore",
        "root = Path(sys.argv[1])",
        "Path(sys.argv[2]).touch()",
        "gate = Path(sys.argv[3])",
        "deadline = time.monotonic() + 10",
        "while not gate.exists() and time.monotonic() < deadline: time.sleep(0.001)",
        "assert gate.exists()",
        "item = TaskStore(root).claim_next_automatic_task('factory:auto')",
        "print(item['id'] if item else 'idle')",
    ])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(store.root), str(store.root / f"ready-{index}"), str(gate)],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    ready_paths = [store.root / f"ready-{index}" for index in range(2)]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not all(path.exists() for path in ready_paths):
        time.sleep(0.005)
    assert all(path.exists() for path in ready_paths)
    gate.touch()
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        outputs.append(stdout.strip())

    assert sorted(outputs) == sorted([task["id"], "idle"])


def test_high_risk_task_is_not_automatically_selected(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("High", "Requires owner decision.", "high", ["reviewed"])

    assert store.next_automatic_task() is None
    with pytest.raises(QueueError, match="risk is not eligible"):
        store.claim_task(task["id"], "factory", allowed_risks=frozenset({"low", "medium"}))
    assert store.list()[0]["status"] == "queued"


def test_task_run_next_cli_requires_explicit_isolation_and_repository_paths():
    args = build_parser().parse_args([
        "task-run-next", "/state",
        "--repo", "/repo", "--work-root", "/isolated/runs",
        "--repo-slug", "Danny-agent/danny-dark-factory", "--base", "main",
        "--codex-home", "/isolated/codex-home",
    ])

    assert args.command == "task-run-next"
    assert args.repo.name == "repo"
    assert args.work_root.as_posix() == "/isolated/runs"
    assert args.repo_slug == "Danny-agent/danny-dark-factory"
    assert args.base == "main"
    assert args.codex_home.name == "codex-home"
