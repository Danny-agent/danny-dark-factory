from __future__ import annotations

import copy
import json

import pytest

from dark_factory.store import QueueError, TaskStore


def test_structurally_invalid_task_records_fail_closed(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    valid = store.add("Schema test", "Reject malformed persisted task data.", "low", ["valid record accepted"])
    malformed = [
        None,
        {"id": valid["id"]},
        {**valid, "status": "unknown"},
        {**valid, "acceptance": [None]},
        {**valid, "events": [None]},
        {**valid, "revision": True},
        {**valid, "claimed_by": 123},
        {**valid, "unexpected": "ignored"},
        {**valid, "events": [{**valid["events"][0], "unexpected": "ignored"}]},
    ]

    for bad_record in malformed:
        ledger = {"schema_version": 1, "tasks": [copy.deepcopy(bad_record)]}
        store.data_path.write_text(json.dumps(ledger), encoding="utf-8")
        with pytest.raises(QueueError, match="Invalid task record"):
            store.list()
        with pytest.raises(QueueError, match="Invalid task record"):
            store.claim("worker")


def test_malformed_task_transition_history_fails_closed(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("History test", "Keep the persisted event chain valid.", "low", ["history checked"])
    task["events"][0]["actor"] = None
    store.data_path.write_text(json.dumps({"schema_version": 1, "tasks": [task]}), encoding="utf-8")
    with pytest.raises(QueueError, match="Invalid task record"):
        store.list()


def test_invalid_event_source_is_reported_as_queue_error(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("Source check", "Reject an invalid event source cleanly.", "low", ["controlled error"])
    task["status"] = "planning"
    task["revision"] = 2
    task["claimed_by"] = "worker"
    task["events"].append({"from": "unknown", "to": "planning", "actor": "worker", "at": task["updated_at"]})
    store.data_path.write_text(json.dumps({"schema_version": 1, "tasks": [task]}), encoding="utf-8")
    with pytest.raises(QueueError, match="invalid source status"):
        store.list()


def test_active_claimant_matches_last_transition_actor(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    store.add("Claim consistency", "Keep claim ownership consistent with history.", "low", ["claim matches event"])
    task = store.claim("worker-a")
    assert task is not None
    task["claimed_by"] = "worker-b"
    store.data_path.write_text(json.dumps({"schema_version": 1, "tasks": [task]}), encoding="utf-8")
    with pytest.raises(QueueError, match="claimant does not match the last event actor"):
        store.list()


def test_naive_task_timestamps_are_rejected(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("Timestamp check", "Timestamps must be timezone-aware.", "low", ["UTC ordering"])
    task["created_at"] = "2026-01-01T00:00:00"
    task["updated_at"] = task["created_at"]
    task["events"][0]["at"] = task["created_at"]
    store.data_path.write_text(json.dumps({"schema_version": 1, "tasks": [task]}), encoding="utf-8")

    with pytest.raises(QueueError, match="timezone-aware"):
        store.list()
