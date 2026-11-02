from __future__ import annotations

import pytest

from dark_factory.store import QueueError, TaskStore


def test_claim_owner_can_complete_bounded_lifecycle(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("Lifecycle", "Advance through the draft PR only.", "low", ["tests pass"])
    actor = "worker-a"
    task = store.claim(actor)
    assert task is not None
    for status in ("planning", "implementing", "verifying", "draft_pr"):
        task = store.transition(task["id"], status, actor, task["revision"])
    assert task["status"] == "draft_pr"
    assert task["claimed_by"] is None
    task = store.transition(task["id"], "accepted", "reviewer", task["revision"])
    assert task["status"] == "accepted"


def test_wrong_actor_cannot_advance_claimed_task(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    store.add("Owner check", "Only the claimant may continue.", "low", ["owner checked"])
    task = store.claim("worker-a")
    assert task is not None
    with pytest.raises(QueueError, match="claimed by another actor"):
        store.transition(task["id"], "planning", "worker-b", task["revision"])


def test_non_object_json_store_fails_closed(tmp_path):
    store = TaskStore(tmp_path / "state")
    store.initialize()
    store.data_path.write_text("[]", encoding="utf-8")
    with pytest.raises(QueueError, match="Unsupported or corrupt"):
        store.list()
