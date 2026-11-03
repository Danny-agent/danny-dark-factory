"""Local task ledger for the dark factory foundation.

This module only manages metadata. It does not execute task descriptions.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

STATUSES = {"queued", "claimed", "planning", "implementing", "verifying", "draft_pr", "accepted", "rejected", "blocked"}
ACTIVE_TASK_STATES = frozenset({"claimed", "planning", "implementing", "verifying"})
AUTOMATIC_TASK_RISKS = frozenset({"low", "medium"})
AUTOMATIC_ACTOR = "factory:auto"
AUTOMATIC_ACTOR_PREFIX = f"{AUTOMATIC_ACTOR}:"
AUTOMATIC_CLAIM_RECOVERY_AFTER = timedelta(minutes=15)
AUTOMATIC_RUN_RECOVERY_AFTER = timedelta(hours=2)
AUTOMATIC_RECOVERY_ACTOR = "factory:auto-recovery"


def _automatic_owner_identity(actor: str | None) -> tuple[int, int] | None:
    if not isinstance(actor, str) or not actor.startswith(AUTOMATIC_ACTOR_PREFIX):
        return None
    parts = actor[len(AUTOMATIC_ACTOR_PREFIX):].split(":")
    if len(parts) != 2 or not all(part.isdecimal() for part in parts):
        return None
    pid, start_ticks = (int(part) for part in parts)
    if not 0 < pid <= 2**31 - 1 or start_ticks <= 0:
        return None
    return pid, start_ticks


def _process_start_time_ticks(pid: int) -> int:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    try:
        fields_after_comm = raw.rsplit(")", 1)[1].split()
        start_ticks = int(fields_after_comm[19])
    except (IndexError, ValueError) as exc:
        raise QueueError("cannot parse Linux process start time") from exc
    if start_ticks <= 0:
        raise QueueError("Linux process start time is invalid")
    return start_ticks


def _current_process_identity() -> tuple[int, int]:
    return os.getpid(), _process_start_time_ticks(os.getpid())


def _process_is_alive(pid: int, start_ticks: int) -> bool:
    try:
        return _process_start_time_ticks(pid) == start_ticks
    except FileNotFoundError:
        return False
    except (OSError, QueueError, UnicodeError):
        # Unknown probes are treated as live; false positives delay recovery.
        return True


TRANSITIONS = {
    "queued": {"claimed", "rejected", "blocked"},
    "claimed": {"planning", "implementing", "blocked", "queued"},
    "planning": {"implementing", "blocked", "queued"},
    "implementing": {"verifying", "blocked", "queued"},
    "verifying": {"draft_pr", "implementing", "blocked", "rejected"},
    "draft_pr": {"accepted", "rejected", "implementing", "blocked"},
    "accepted": set(),
    "rejected": set(),
    "blocked": {"queued", "rejected"},
}


class QueueError(Exception):
    """Raised for invalid, stale, or corrupt queue operations."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStore:
    """File-backed JSON task ledger with process-safe serialized writes."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.data_path = self.root / "tasks.json"
        self.lock_path = self.root / ".queue.lock"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if not self.data_path.exists():
                self._write({"schema_version": 1, "tasks": []})
            else:
                self._read()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> dict:
        try:
            data = json.loads(self.data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueueError(f"Cannot read task ledger safely: {exc}") from exc
        if (
            not isinstance(data, dict)
            or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1
            or not isinstance(data.get("tasks"), list)
        ):
            raise QueueError("Unsupported or corrupt task ledger schema")
        if len(data["tasks"]) > 10000:
            raise QueueError("Task ledger exceeds the 10000-record safety limit")
        seen_ids: set[str] = set()
        for index, task in enumerate(data["tasks"]):
            try:
                self._validate_task(task)
            except QueueError as exc:
                raise QueueError(f"Invalid task record at index {index}: {exc}") from exc
            if task["id"] in seen_ids:
                raise QueueError(f"Invalid task record at index {index}: duplicate task id")
            seen_ids.add(task["id"])
        return data

    def _validate_task(self, task: object) -> None:
        if not isinstance(task, dict):
            raise QueueError("record must be an object")
        required = {
            "id", "title", "description", "risk", "acceptance", "status", "revision",
            "created_at", "updated_at", "claimed_by", "events",
        }
        if not required.issubset(task):
            raise QueueError("required fields are missing")
        if set(task) != required:
            raise QueueError("record contains unknown fields")
        try:
            task_id = task["id"]
            if not isinstance(task_id, str) or str(uuid.UUID(task_id)) != task_id:
                raise QueueError("id must be a canonical UUID")
            for field, limit in (("title", 180), ("description", 4000)):
                value = task[field]
                if not isinstance(value, str):
                    raise QueueError(f"{field} must be text")
                self._validate_text(value, field, limit)
            if task["risk"] not in {"low", "medium", "high"}:
                raise QueueError("risk is invalid")
            if task["status"] not in STATUSES:
                raise QueueError("status is invalid")
            if type(task["revision"]) is not int or task["revision"] < 1:
                raise QueueError("revision must be a positive integer")
            acceptance = task["acceptance"]
            if not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 20:
                raise QueueError("acceptance must contain 1–20 criteria")
            for criterion in acceptance:
                if not isinstance(criterion, str):
                    raise QueueError("acceptance criteria must be text")
                self._validate_text(criterion, "acceptance criterion", 300)
            for field in ("created_at", "updated_at"):
                if not isinstance(task[field], str):
                    raise QueueError(f"{field} must be an ISO timestamp")
                timestamp = datetime.fromisoformat(task[field])
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise QueueError(f"{field} must be timezone-aware")
            claimed_by = task["claimed_by"]
            if claimed_by is not None:
                if not isinstance(claimed_by, str):
                    raise QueueError("claimed_by must be text or null")
                self._validate_text(claimed_by, "claimed_by", 100)
            active = {"claimed", "planning", "implementing", "verifying"}
            if (task["status"] in active) != (claimed_by is not None):
                raise QueueError("claimant does not match task status")
            events = task["events"]
            if not isinstance(events, list) or not 1 <= len(events) <= 1000:
                raise QueueError("events must contain 1–1000 records")
            if len(events) != task["revision"]:
                raise QueueError("revision does not match event count")
            previous = None
            for index, event in enumerate(events):
                if not isinstance(event, dict) or set(event) != {"from", "to", "at", "actor"}:
                    raise QueueError(f"event {index} has missing or unknown fields")
                source, target = event["from"], event["to"]
                if index == 0:
                    if source is not None or target != "queued":
                        raise QueueError("first event must create a queued task")
                else:
                    if not isinstance(source, str) or source not in STATUSES or source != previous:
                        raise QueueError(f"event {index} has an invalid source status")
                    if target not in TRANSITIONS[source]:
                        raise QueueError(f"event {index} breaks the transition history")
                if target not in STATUSES:
                    raise QueueError(f"event {index} has an unknown status")
                for field in ("actor", "at"):
                    if not isinstance(event[field], str) or not event[field].strip():
                        raise QueueError(f"event {index} {field} must be non-empty text")
                event_time = datetime.fromisoformat(event["at"])
                if event_time.tzinfo is None or event_time.utcoffset() is None:
                    raise QueueError(f"event {index} at must be timezone-aware")
                previous = target
            if previous != task["status"]:
                raise QueueError("last event does not match task status")
            if task["status"] in active and claimed_by != events[-1]["actor"]:
                raise QueueError("claimant does not match the last event actor")
        except (KeyError, TypeError, ValueError) as exc:
            raise QueueError("fields have invalid types or values") from exc

    def _write(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".tasks-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.data_path)
            dirfd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _validate_text(value: str, field: str, limit: int) -> str:
        value = value.strip()
        if not value:
            raise QueueError(f"{field} must not be empty")
        if len(value) > limit:
            raise QueueError(f"{field} exceeds {limit} characters")
        if "\x00" in value:
            raise QueueError(f"{field} contains a NUL byte")
        return value

    def add(self, title: str, description: str, risk: str, acceptance: list[str]) -> dict:
        title = self._validate_text(title, "title", 180)
        description = self._validate_text(description, "description", 4000)
        if risk not in {"low", "medium", "high"}:
            raise QueueError("risk must be low, medium, or high")
        if not acceptance or len(acceptance) > 20:
            raise QueueError("provide 1–20 acceptance criteria")
        criteria = [self._validate_text(item, "acceptance criterion", 300) for item in acceptance]
        task = {
            "id": str(uuid.uuid4()),
            "title": title,
            "description": description,
            "risk": risk,
            "acceptance": criteria,
            "status": "queued",
            "revision": 1,
            "created_at": _now(),
            "updated_at": _now(),
            "claimed_by": None,
            "events": [{"from": None, "to": "queued", "at": _now(), "actor": "submitter"}],
        }
        with self._locked():
            data = self._read()
            data["tasks"].append(task)
            self._write(data)
        return task

    def list(self) -> list[dict]:
        with self._locked():
            return list(self._read()["tasks"])

    @staticmethod
    def _automatic_task(tasks: list[dict]) -> dict | None:
        candidates = [
            (datetime.fromisoformat(item["created_at"]).astimezone(timezone.utc), index, item)
            for index, item in enumerate(tasks)
            if item["status"] == "queued" and item["risk"] in AUTOMATIC_TASK_RISKS
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]

    def next_automatic_task(self) -> dict | None:
        """Return the oldest queued low/medium-risk task without claiming it."""
        with self._locked():
            task = self._automatic_task(self._read()["tasks"])
            return dict(task) if task is not None else None

    def claim_next_automatic_task(self, actor: str, *, now: datetime | None = None) -> dict | None:
        """Recover stale automatic runs, then claim one oldest eligible task atomically."""
        actor = self._validate_text(actor, "actor", 100)
        current_identity = _current_process_identity()
        if actor == AUTOMATIC_ACTOR:
            actor = f"{AUTOMATIC_ACTOR_PREFIX}{current_identity[0]}:{current_identity[1]}"
        if _automatic_owner_identity(actor) != current_identity:
            raise QueueError("automatic claimant must identify the current process")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise QueueError("automatic recovery clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        with self._locked():
            data = self._read()
            if any(
                item["status"] in ACTIVE_TASK_STATES
                and item["claimed_by"] != AUTOMATIC_ACTOR
                and not (item["claimed_by"] or "").startswith(AUTOMATIC_ACTOR_PREFIX)
                for item in data["tasks"]
            ):
                return None
            recoverable: list[tuple[dict, str]] = []
            for item in data["tasks"]:
                if item["status"] not in ACTIVE_TASK_STATES:
                    continue
                claimant = item["claimed_by"]
                if claimant != AUTOMATIC_ACTOR and not claimant.startswith(AUTOMATIC_ACTOR_PREFIX):
                    continue
                owner_identity = _automatic_owner_identity(claimant)
                if owner_identity is None or _process_is_alive(*owner_identity):
                    return None
                updated_at = datetime.fromisoformat(item["updated_at"]).astimezone(timezone.utc)
                age = now - updated_at
                if item["status"] == "claimed":
                    target = "queued" if age >= AUTOMATIC_CLAIM_RECOVERY_AFTER else None
                else:
                    target = "blocked" if age >= AUTOMATIC_RUN_RECOVERY_AFTER else None
                if target is None:
                    return None
                recoverable.append((item, target))

            for item, target in recoverable:
                self._transition_record(item, target, AUTOMATIC_RECOVERY_ACTOR)
                item["claimed_by"] = None
            recovered = bool(recoverable)
            task = self._automatic_task(data["tasks"])
            if task is None:
                if recovered:
                    self._write(data)
                return None
            self._transition_record(task, "claimed", actor)
            task["claimed_by"] = actor
            self._write(data)
            return dict(task)

    def claim(self, actor: str) -> dict | None:
        actor = self._validate_text(actor, "actor", 100)
        with self._locked():
            data = self._read()
            if any(item["status"] in ACTIVE_TASK_STATES for item in data["tasks"]):
                return None
            task = next((item for item in data["tasks"] if item["status"] == "queued"), None)
            if task is None:
                return None
            self._transition_record(task, "claimed", actor)
            task["claimed_by"] = actor
            self._write(data)
            return task

    def claim_task(
        self, task_id: str, actor: str, allowed_risks: frozenset[str] | None = None
    ) -> dict:
        """Atomically claim exactly one queued task by ID, optionally enforcing risk."""
        task_id = self._validate_text(task_id, "task_id", 36)
        actor = self._validate_text(actor, "actor", 100)
        with self._locked():
            data = self._read()
            if any(item["status"] in ACTIVE_TASK_STATES for item in data["tasks"]):
                raise QueueError("another task has an active claim")
            task = next((item for item in data["tasks"] if item["id"] == task_id), None)
            if task is None:
                raise QueueError(f"task not found: {task_id}")
            if task["status"] != "queued":
                raise QueueError(f"task is not queued: {task_id}")
            if allowed_risks is not None and task["risk"] not in allowed_risks:
                raise QueueError(f"task risk is not eligible for automatic execution: {task_id}")
            self._transition_record(task, "claimed", actor)
            task["claimed_by"] = actor
            self._write(data)
            return task

    def transition(self, task_id: str, target: str, actor: str, expected_revision: int) -> dict:
        if target not in STATUSES:
            raise QueueError(f"unknown status: {target}")
        actor = self._validate_text(actor, "actor", 100)
        with self._locked():
            data = self._read()
            task = next((item for item in data["tasks"] if item["id"] == task_id), None)
            if task is None:
                raise QueueError(f"task not found: {task_id}")
            if task["revision"] != expected_revision:
                raise QueueError(f"stale revision: expected {expected_revision}, current {task['revision']}")
            if target not in TRANSITIONS[task["status"]]:
                raise QueueError(f"invalid transition: {task['status']} -> {target}")
            if task["claimed_by"] is not None and task["claimed_by"] != actor:
                raise QueueError("task is claimed by another actor")
            self._transition_record(task, target, actor)
            if target in {"queued", "draft_pr", "accepted", "rejected", "blocked"}:
                task["claimed_by"] = None
            self._write(data)
            return task

    @staticmethod
    def _transition_record(task: dict, target: str, actor: str) -> None:
        previous = task["status"]
        task["status"] = target
        task["revision"] += 1
        task["updated_at"] = _now()
        task["events"].append({"from": previous, "to": target, "at": _now(), "actor": actor})
