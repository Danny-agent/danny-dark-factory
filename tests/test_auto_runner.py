from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dark_factory.store import TaskStore


REPO = Path(__file__).resolve().parents[1]


def runner_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    repo = home / "projects" / "danny-dark-factory-runner"
    repo.mkdir(parents=True)
    shutil.copytree(REPO / "src", repo / "src")
    script = repo / "scripts" / "auto_run_once.py"
    script.parent.mkdir()
    shutil.copy2(REPO / "scripts" / "auto_run_once.py", script)
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Runner Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "runner-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/Danny-agent/danny-dark-factory.git"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src", "scripts"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "test runner"], check=True)
    data_root = home / ".local" / "share" / "danny-dark-factory"
    return home, repo, data_root, script


def runner_env(home: Path, repo: Path, data_root: Path, *, state: Path | None = None) -> dict[str, str]:
    runner_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "FACTORY_REPO": str(repo),
        "FACTORY_STATE": str(state or data_root / "self-hosting-state"),
        "FACTORY_WORK_ROOT": str(data_root / "self-hosting-worktrees"),
        "FACTORY_CODEX_HOME": str(data_root / "codex-home"),
        "FACTORY_REPO_SLUG": "Danny-agent/danny-dark-factory",
        "FACTORY_RUNNER_SHA": runner_sha,
        "FACTORY_BASE": "main",
    }


def test_group_lookup_without_current_primary_gid_is_checked_against_accounts(monkeypatch):
    script = REPO / "scripts" / "auto_run_once.py"
    spec = importlib.util.spec_from_file_location("auto_run_once_group_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def missing_group(gid):
        raise KeyError(gid)

    monkeypatch.setattr(module.grp, "getgrgid", missing_group)
    monkeypatch.setattr(
        module.pwd,
        "getpwall",
        lambda: [SimpleNamespace(pw_gid=os.getgid(), pw_uid=os.getuid())],
    )

    assert module._group_has_other_users(os.getgid()) is False
    assert module._group_has_other_users(os.getgid() + 1) is True

    monkeypatch.setattr(
        module.pwd,
        "getpwall",
        lambda: [SimpleNamespace(pw_gid=os.getgid(), pw_uid=os.getuid() + 1)],
    )
    assert module._group_has_other_users(os.getgid()) is True


def test_runner_rejects_group_or_world_writable_dispatcher_script(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    script.chmod(0o666)

    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "runner script" in result.stderr.lower()


def test_runbook_config_check_sets_all_required_environment_values():
    runbook = (REPO / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    marker = "# Validate paths/modes without invoking the dispatcher or touching the task queue."
    command = runbook.split(marker, 1)[1].split("```", 1)[0]

    for name in (
        "FACTORY_REPO",
        "FACTORY_STATE",
        "FACTORY_WORK_ROOT",
        "FACTORY_CODEX_HOME",
        "FACTORY_REPO_SLUG",
        "FACTORY_RUNNER_SHA",
        "FACTORY_BASE",
    ):
        assert f"{name}=" in command


def test_runbook_requires_latest_codeowner_approval_on_current_head():
    runbook = (REPO / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")

    assert "headRefOid" in runbook
    assert "latest_owner_review" in runbook
    assert "submitted_at" in runbook
    assert 'latest_owner_review[0] == "APPROVED"' in runbook
    assert 'latest_owner_review[1] == os.environ["PR_HEAD_SHA"]' in runbook
    assert "contents/.github/CODEOWNERS?ref=$VERIFIED_MERGE_SHA" in runbook
    assert 'rows == [["*", "@pc099"]]' in runbook
    assert "FACTORY_RUNNER_SHA" in runbook
    assert ".enforce_admins.enabled == true" in runbook
    assert ".required_pull_request_reviews.require_code_owner_reviews == true" in runbook


def test_service_tracks_and_kills_the_full_process_cgroup():
    service = (REPO / "deploy" / "systemd" / "danny-dark-factory-auto-run.service").read_text(
        encoding="utf-8"
    )

    assert "ExitType=cgroup" in service
    assert "KillMode=control-group" in service
    assert "RuntimeMaxSec=45min" in service
    assert "TimeoutStopSec=30s" in service


def test_runner_timeout_kills_dispatch_process_group(tmp_path):
    script = REPO / "scripts" / "auto_run_once.py"
    spec = importlib.util.spec_from_file_location("auto_run_once_timeout_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pid_file = tmp_path / "child.pid"
    terminated_file = tmp_path / "child.terminated"
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import os, signal, sys, time\n"
        "pid_file, marker = sys.argv[1:]\n"
        "def stop(*_):\n open(marker, 'w').close(); raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "open(pid_file, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], *sys.argv[2:]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not __import__('os').path.exists(sys.argv[2]): time.sleep(0.01)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    try:
        result = module._run_dispatcher(
            [sys.executable, str(parent_script), str(child_script), str(pid_file), str(terminated_file)],
            env=os.environ.copy(),
            cwd=tmp_path,
            timeout_s=1,
        )
        assert result == 124
        assert terminated_file.exists(), "timeout must signal the full child process group"
    finally:
        if pid_file.exists() and not terminated_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def test_runner_timeout_kills_descendant_ignoring_sigterm(tmp_path):
    script = REPO / "scripts" / "auto_run_once.py"
    spec = importlib.util.spec_from_file_location("auto_run_once_sigkill_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pid_file = tmp_path / "stubborn-child.pid"
    child_script = tmp_path / "stubborn-child.py"
    child_script.write_text(
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(sys.argv[1], 'w').write(f'{os.getpid()} {os.getpgrp()}')\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    parent_script = tmp_path / "stubborn-parent.py"
    parent_script.write_text(
        "import os, subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not os.path.exists(sys.argv[2]): time.sleep(0.01)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    child_pid = None
    try:
        result = module._run_dispatcher(
            [sys.executable, str(parent_script), str(child_script), str(pid_file)],
            env=os.environ.copy(),
            cwd=tmp_path,
            timeout_s=1,
        )
        assert result == 124
        assert pid_file.exists()
        pid_and_group = pid_file.read_text(encoding="utf-8").split()
        child_pid, process_group_id = int(pid_and_group[0]), int(pid_and_group[1])
        deadline = time.monotonic() + 2
        state = "unknown"
        while time.monotonic() < deadline:
            try:
                stat_text = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                state = stat_text.rsplit(")", 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                state = None
            if state in {None, "Z"}:
                break
            time.sleep(0.05)
        assert state in {None, "Z"}, "timeout must kill descendants even if the leader exits on SIGTERM"
        assert not module._process_group_has_live_members(process_group_id)
    finally:
        if child_pid is not None:
            try:
                stat_text = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                state = stat_text.rsplit(")", 1)[1].split()[0]
                if state != "Z":
                    os.kill(child_pid, signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                pass


def test_runner_invokes_one_shot_cli_and_is_idle_when_queue_empty(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert '"status": "idle"' in result.stdout
    assert "error" not in result.stderr.lower()
    assert stat.S_IMODE(data_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_root / "self-hosting-state").stat().st_mode) == 0o700
    assert stat.S_IMODE((data_root / "self-hosting-worktrees").stat().st_mode) == 0o700


def test_runner_leaves_high_risk_task_queued_and_idle(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    state = data_root / "self-hosting-state"
    data_root.mkdir(parents=True, mode=0o700)
    os.chmod(data_root, 0o700)
    store = TaskStore(state)
    store.initialize()
    os.chmod(state, 0o700)
    high = store.add("High risk", "Requires human authority.", "high", ["reviewed"])

    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root, state=state),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert '"status": "idle"' in result.stdout
    assert store.list()[0]["id"] == high["id"]
    assert store.list()[0]["status"] == "queued"


@pytest.mark.parametrize("root_name", ["projects", "data"])
def test_runner_rejects_symlinked_approved_root(tmp_path, root_name):
    home, repo, data_root, script = runner_layout(tmp_path)
    if root_name == "projects":
        root = home / "projects"
        target = home / "real-projects"
    else:
        root = data_root
        root.mkdir(parents=True, exist_ok=True)
        target = home / "real-factory-data"
    root.rename(target)
    root.symlink_to(target, target_is_directory=True)

    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "symlink" in result.stderr.lower()


@pytest.mark.parametrize("setting", ["FACTORY_REPO", "FACTORY_STATE", "FACTORY_WORK_ROOT", "FACTORY_CODEX_HOME"])
def test_runner_rejects_symlinked_ancestor_for_configured_paths(tmp_path, setting):
    home, repo, data_root, script = runner_layout(tmp_path)
    data_root.mkdir(parents=True, exist_ok=True)
    env = runner_env(home, repo, data_root)

    if setting == "FACTORY_REPO":
        link = home / "projects-link"
        link.symlink_to(home / "projects", target_is_directory=True)
        env[setting] = str(link / repo.name)
    else:
        link = home / "factory-data-link"
        link.symlink_to(data_root, target_is_directory=True)
        paths = {
            "FACTORY_STATE": "self-hosting-state",
            "FACTORY_WORK_ROOT": "self-hosting-worktrees",
            "FACTORY_CODEX_HOME": "codex-home",
        }
        env[setting] = str(link / paths[setting])

    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "symlink" in result.stderr.lower()


def test_runner_rejects_state_equal_to_approved_data_root(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root, state=data_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "strict descendants" in result.stderr.lower()


def test_runner_rejects_state_outside_approved_factory_data_root(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root, state=repo / "nested-state"),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "strict descendants of the approved factory data root" in result.stderr


def test_runner_rejects_codex_home_overlapping_state_directory(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    state = data_root / "self-hosting-state"
    env = runner_env(home, repo, data_root, state=state)
    env["FACTORY_CODEX_HOME"] = str(state / "codex-home")

    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "must be disjoint" in result.stderr


@pytest.mark.parametrize("target", ["data-root", "state", "work"])
def test_runner_rejects_group_or_world_writable_private_directories(tmp_path, target):
    home, repo, data_root, script = runner_layout(tmp_path)
    data_root.mkdir(parents=True, mode=0o700)
    os.chmod(data_root, 0o700)
    if target == "data-root":
        os.chmod(data_root, 0o770)
    else:
        directory = data_root / ("self-hosting-state" if target == "state" else "self-hosting-worktrees")
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o770)

    result = subprocess.run(
        [sys.executable, str(script)],
        env=runner_env(home, repo, data_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "private" in result.stderr.lower()


def test_runner_check_config_rejects_runner_revision_mismatch(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    env = runner_env(home, repo, data_root)
    env["FACTORY_RUNNER_SHA"] = "0" * 40

    result = subprocess.run(
        [sys.executable, str(script), "--check-config"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "runner revision" in result.stderr.lower()


def test_runner_check_config_rejects_dirty_runner_checkout(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    env = runner_env(home, repo, data_root)
    script.write_text(script.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), "--check-config"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "runner checkout must be clean" in result.stderr.lower()


def test_runner_check_config_rejects_wrong_origin(tmp_path):
    home, repo, data_root, script = runner_layout(tmp_path)
    env = runner_env(home, repo, data_root)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", "https://github.com/other/repo.git"],
        check=True,
    )

    result = subprocess.run(
        [sys.executable, str(script), "--check-config"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "origin does not match" in result.stderr.lower()


def test_runner_invokes_once_with_a_minimal_environment(tmp_path, monkeypatch):
    home, repo, data_root, script = runner_layout(tmp_path)
    spec = importlib.util.spec_from_file_location("auto_run_once_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = runner_env(home, repo, data_root)
    env.update({
        "GH_TOKEN": "dummy-secret-token",
        "GITHUB_TOKEN": "dummy-github-token",
        "HTTP_PROXY": "http://proxy.invalid:8080",
        "GIT_CONFIG_COUNT": "1",
        "PYTHONPATH": "/untrusted/pythonpath",
    })
    monkeypatch.setattr(module.os, "environ", env)
    monkeypatch.setattr(module, "_verify_runner_checkout", lambda *args: None)
    captured = {}

    class FakeProcess:
        pid = 12345
        returncode = 0

        def communicate(self, *, timeout):
            captured["timeout"] = timeout
            return "", ""

    def fake_popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return FakeProcess()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    assert module.main([]) == 0
    assert captured["command"].count("task-run-next") == 1
    assert captured["command"][captured["command"].index("--agent-timeout") + 1] == "1500"
    assert captured["command"][captured["command"].index("--verification-timeout") + 1] == "300"
    assert captured["timeout"] == 44 * 60
    assert captured["cwd"] == repo
    assert captured["start_new_session"] is True
    assert captured["stdout"] is None
    assert captured["stderr"] is None
    assert captured["env"]["HOME"] == str(home)
    assert captured["env"]["PYTHONPATH"] == str(repo / "src")
    assert captured["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert set(captured["env"]) == {
        "HOME", "USER", "LOGNAME", "PATH", "LANG", "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE"
    }


def test_runner_check_config_validates_without_dispatching(tmp_path, monkeypatch, capsys):
    home, repo, data_root, script = runner_layout(tmp_path)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("auto_run_once_check_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "environ", runner_env(home, repo, data_root))

    def fail_if_dispatched(*args, **kwargs):
        raise AssertionError("configuration check must not launch task-run-next")

    monkeypatch.setattr(module, "_run_dispatcher", fail_if_dispatched)

    assert module.main(["--check-config"]) == 0
    assert "configuration valid; no task dispatched" in capsys.readouterr().out
    assert not data_root.exists()
