from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from dark_factory.execution import (
    AgentResult,
    ExecutionError,
    TaskExecutor,
    VerificationResult,
    _run,
)
from dark_factory.store import TaskStore


def test_run_rejects_unbounded_child_output(monkeypatch):
    import dark_factory.execution as execution_module

    monkeypatch.setattr(execution_module, "MAX_COMMAND_OUTPUT_CHARS", 128)
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000); sys.stderr.write('y' * 1000)"]

    with pytest.raises(ExecutionError, match="output exceeded"):
        _run(command, timeout=10)


def test_run_terminates_infinite_output_when_cap_is_reached(monkeypatch):
    import dark_factory.execution as execution_module

    monkeypatch.setattr(execution_module, "MAX_COMMAND_OUTPUT_CHARS", 128)
    start = time.monotonic()
    command = [sys.executable, "-c", "import os; exec(\"while True: os.write(1, b'x' * 4096)\")"]

    with pytest.raises(ExecutionError, match="output exceeded"):
        _run(command, timeout=2)

    assert time.monotonic() - start < 2


def test_run_timeout_covers_blocked_stdin_write(tmp_path):
    pid_file = tmp_path / "stdin-child.pid"
    child_code = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)"
    harness_code = "\n".join(
        [
            "import sys",
            "from dark_factory.execution import _run, ExecutionError",
            f"child_code = {child_code!r}",
            "try:",
            "    _run([sys.executable, '-c', child_code, sys.argv[1]], timeout=1, stdin='x' * 2_000_000)",
            "except ExecutionError as exc:",
            "    print(str(exc))",
        ]
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    harness = subprocess.Popen(
        [sys.executable, "-c", harness_code, str(pid_file)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not pid_file.exists() and harness.poll() is None:
            time.sleep(0.02)
        if pid_file.exists():
            child_pid = int(pid_file.read_text(encoding="ascii"))
        stdout, stderr = harness.communicate(timeout=4)
        assert harness.returncode == 0, stderr
        assert "timed out after 1s" in stdout
    finally:
        for pid in (child_pid, harness.pid):
            if pid is not None:
                try:
                    os.killpg(pid, 9)
                except ProcessLookupError:
                    pass
        if harness.poll() is None:
            harness.wait(timeout=2)


def test_run_closes_inherited_output_pipes_by_stopping_descendants():
    child_code = "import time; time.sleep(30)"
    parent_code = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child_code!r}]); print('done')"

    result = _run([sys.executable, "-c", parent_code], timeout=10)

    assert result.returncode == 0
    assert result.stdout == "done\n"


def test_run_preserves_small_child_output():
    result = _run([sys.executable, "-c", "print('ready')"], timeout=10)

    assert result.returncode == 0
    assert result.stdout == "ready\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def new_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "--initial-branch=main")
    git(path, "config", "user.name", "Factory Test")
    git(path, "config", "user.email", "factory-test@example.invalid")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


class WritingAgent:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def preflight(self) -> None:
        self.calls.append("preflight")

    def run(self, task: dict, workspace: Path) -> AgentResult:
        self.calls.append("agent")
        (workspace / "result.txt").write_text("implemented\n", encoding="utf-8")
        return AgentResult(exit_code=0, summary="changed one file")


class PassingVerifier:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def verify(self, workspace: Path) -> VerificationResult:
        self.calls.append("verify")
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "implemented\n"
        return VerificationResult(passed=True, summary="tests passed")


class RecordingPublisher:
    def __init__(self, calls: list[str]):
        self.calls = calls
        self.published = []

    def publish(self, workspace: Path, branch: str, task: dict, commit_sha: str, verification: VerificationResult, base_sha: str) -> str:
        self.calls.append("publish")
        self.published.append((branch, task["id"], commit_sha))
        return "https://github.com/example/repo/pull/7"


def make_runner(tmp_path: Path, risk: str = "low", verifier=None):
    calls: list[str] = []
    repo = new_repo(tmp_path / "repo")
    store = TaskStore(tmp_path / "state")
    store.initialize()
    task = store.add("Implement bounded change", "Write the requested file.", risk, ["tests pass"])
    publisher = RecordingPublisher(calls)
    runner = TaskExecutor(
        store=store,
        repo_root=repo,
        work_root=tmp_path / "runs",
        agent=WritingAgent(calls),
        verifier=verifier or PassingVerifier(calls),
        publisher=publisher,
        base_ref="main",
    )
    return runner, store, task, calls, publisher


def test_low_risk_task_runs_tests_then_publishes_a_draft_pr(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    result = runner.execute(task["id"])

    assert result.status == "draft_pr"
    assert result.pull_request_url == "https://github.com/example/repo/pull/7"
    assert len(result.commit_sha) == 40
    assert calls == ["preflight", "agent", "verify", "publish"]
    assert store.list()[0]["status"] == "draft_pr"
    assert publisher.published[0][0].startswith("factory/")
    assert git(tmp_path / "repo", "show", f"{result.branch}:result.txt") == "implemented"
    assert git(tmp_path / "repo", "worktree", "list", "--porcelain").count("worktree ") == 1


def test_high_risk_task_is_not_claimed_or_executed(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path, risk="high")

    with pytest.raises(ExecutionError, match="only low- or medium-risk"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "queued"
    assert calls == []
    assert publisher.published == []


def test_medium_risk_task_runs_through_verified_draft_pr(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path, risk="medium")

    result = runner.execute(task["id"])

    assert result.status == "draft_pr"
    assert store.list()[0]["status"] == "draft_pr"
    assert calls == ["preflight", "agent", "verify", "publish"]
    assert publisher.published[0][1] == task["id"]


def test_verification_failure_blocks_task_without_publication(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path, risk="medium")

    class FailedVerifier:
        def verify(self, workspace):
            calls.append("verify")
            return VerificationResult(False, "tests failed")

    runner.verifier = FailedVerifier()

    with pytest.raises(ExecutionError, match="containerized verification failed"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert calls == ["preflight", "agent", "verify"]
    assert publisher.published == []


def test_concurrent_auto_dispatchers_do_not_overlap_active_run(tmp_path):
    runner_one, store, first, calls_one, publisher_one = make_runner(tmp_path, risk="medium")
    second = store.add("Second task", "Wait for the active dispatch.", "low", ["tests pass"])
    entered = threading.Event()
    release = threading.Event()
    calls_two: list[str] = []

    class BlockingAgent(WritingAgent):
        def preflight(self):
            self.calls.append("preflight")
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("test did not release the active preflight")

    runner_one.agent = BlockingAgent(calls_one)
    runner_one.verifier = PassingVerifier(calls_one)
    publisher_two = RecordingPublisher(calls_two)
    runner_two = TaskExecutor(
        store=store,
        repo_root=runner_one.repo_root,
        work_root=runner_one.work_root,
        agent=WritingAgent(calls_two),
        verifier=PassingVerifier(calls_two),
        publisher=publisher_two,
        base_ref=runner_one.base_ref,
        actor=runner_one.actor,
        protected_paths=runner_one.protected_paths,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(runner_one.execute_next)
        assert entered.wait(timeout=10)
        try:
            assert pool.submit(runner_two.execute_next).result(timeout=5) is None
        finally:
            release.set()
        first_result = first_future.result(timeout=30)

    records = {task["id"]: task for task in store.list()}
    assert first_result is not None and first_result.task_id == first["id"]
    assert records[first["id"]]["status"] == "draft_pr"
    assert records[second["id"]]["status"] == "queued"
    assert calls_one == ["preflight", "agent", "verify", "publish"]
    assert calls_two == []
    assert len(publisher_one.published) == 1
    assert publisher_two.published == []


def test_automatic_preflight_failure_releases_claimed_task(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class ClaimCheckingVerifier:
        def preflight(self):
            assert store.list()[0]["status"] == "claimed"
            raise ExecutionError("verification image unavailable")

        def verify(self, workspace: Path) -> VerificationResult:
            raise AssertionError("verification must not run after failed preflight")

    runner.verifier = ClaimCheckingVerifier()

    with pytest.raises(ExecutionError, match="verification image unavailable"):
        runner.execute_next()

    record = store.list()[0]
    assert record["id"] == task["id"]
    assert record["status"] == "queued"
    assert [event["to"] for event in record["events"]] == ["queued", "claimed", "queued"]
    assert calls == ["preflight"]
    assert publisher.published == []


def test_manual_execution_claims_before_preflight_and_requeues_on_failure(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class ClaimCheckingAgent(WritingAgent):
        def preflight(self):
            self.calls.append("preflight")
            assert store.list()[0]["status"] == "claimed"
            raise ExecutionError("manual preflight unavailable")

    runner.agent = ClaimCheckingAgent(calls)
    with pytest.raises(ExecutionError, match="manual preflight unavailable"):
        runner.execute(task["id"])

    record = store.list()[0]
    assert record["id"] == task["id"]
    assert record["status"] == "queued"
    assert [event["to"] for event in record["events"]] == ["queued", "claimed", "queued"]
    assert calls == ["preflight"]
    assert publisher.published == []


def test_automatic_dispatcher_runs_one_task_per_invocation_in_order(tmp_path):
    runner, store, first, calls, publisher = make_runner(tmp_path, risk="medium")
    second = store.add("Second task", "Do this next.", "low", ["tests pass"])

    first_result = runner.execute_next()

    assert first_result is not None
    assert first_result.task_id == first["id"]
    assert first_result.status == "draft_pr"
    statuses = {task["id"]: task["status"] for task in store.list()}
    assert statuses[first["id"]] == "draft_pr"
    assert statuses[second["id"]] == "queued"

    second_result = runner.execute_next()

    assert second_result is not None
    assert second_result.task_id == second["id"]
    assert second_result.status == "draft_pr"
    assert runner.execute_next() is None
    assert len(calls) == 8
    assert calls == ["preflight", "agent", "verify", "publish"] * 2
    assert len(publisher.published) == 2


def test_codex_worker_rejects_unapproved_codex_home_settings(tmp_path):
    from dark_factory.execution import CodexAgent, ExecutionError

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('[mcp_servers.evil]\ncommand = "nope"\n', encoding="utf-8")
    config.chmod(0o600)
    agent = CodexAgent(executable="/bin/true", codex_home=codex_home)

    with pytest.raises(ExecutionError, match="unapproved Codex configuration"):
        agent._base_env()


def test_codex_worker_uses_isolated_identity_and_credential_denied_profile(tmp_path, monkeypatch):
    from dark_factory.execution import CodexAgent

    codex_home = tmp_path / "factory-codex-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    capture = tmp_path / "capture.json"
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if sys.argv[1:3] == ['login', 'status']:\n    raise SystemExit(0)\n"
        f"open({str(capture)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'env': dict(os.environ), 'prompt': sys.stdin.read()}}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "must-not-enter-worker")

    repo_root = tmp_path / "factory-repo"
    git_metadata = repo_root / ".git"
    git_metadata.mkdir(parents=True)
    git_metadata.chmod(0o755)
    agent = CodexAgent(executable=str(executable), codex_home=codex_home, repo_root=repo_root)
    result = agent.run({"id": "task", "title": "safe", "description": "bounded", "acceptance": ["passes"]}, workspace)

    invocation = json.loads(capture.read_text(encoding="utf-8"))
    profile = (codex_home / "factory-worker.config.toml").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert invocation["env"]["CODEX_HOME"] == str(codex_home)
    assert invocation["env"]["HOME"].startswith(str(workspace))
    assert all(key not in invocation["env"] for key in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"))
    assert invocation["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert invocation["env"]["GH_CONFIG_DIR"] != str(Path.home() / ".config" / "gh")
    assert "--profile" in invocation["argv"]
    assert invocation["argv"][invocation["argv"].index("--profile") + 1] == "factory-worker"
    assert "--sandbox" not in invocation["argv"]
    assert "--strict-config" in invocation["argv"]
    assert "--ephemeral" in invocation["argv"]
    assert "--ignore-user-config" not in invocation["argv"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in invocation["argv"]
    assert 'default_permissions = "factory-worker"' in profile
    assert 'web_search = "disabled"' in profile
    assert '":root" = "deny"' in profile
    assert '":minimal" = "read"' in profile
    assert f'"{repo_root / ".git"}" = "read"' in profile
    assert f'[projects."{workspace.resolve()}"]\ntrust_level = "untrusted"' in profile
    assert '":workspace_roots"]\n"." = "write"' in profile
    assert '".git" = "read"' in profile
    assert "enabled = false" in profile
    assert '"/var/run/docker.sock" = "deny"' in profile
    assert "UNTRUSTED TASK JSON" in invocation["prompt"]
    assert '"description": "bounded"' in invocation["prompt"]
    assert "configured containerized verification suite" in invocation["prompt"]
    assert "must pass independent verification" not in invocation["prompt"]
    assert (codex_home / "factory-worker.config.toml").stat().st_mode & 0o777 == 0o600


def test_codex_profile_resolves_linked_worktree_git_metadata_read_only(tmp_path):
    from dark_factory.execution import CodexAgent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    common_dir = tmp_path / "source" / ".git"
    common_dir.mkdir(parents=True)
    common_dir.chmod(0o755)
    gitdir = common_dir / "worktrees" / "factory-task"
    gitdir.mkdir(parents=True)
    gitdir.chmod(0o755)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (workspace / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    agent = CodexAgent(codex_home=tmp_path / "codex-home", repo_root=workspace)

    resolved = agent._trusted_git_common_dir()

    assert resolved == common_dir.resolve()
    assert f'"{resolved}" = "read"' in agent._worker_profile_text()


def test_codex_profile_rejects_git_metadata_outside_runner_parent(tmp_path):
    from dark_factory.execution import CodexAgent, ExecutionError

    workspace = tmp_path / "runner" / "workspace"
    workspace.mkdir(parents=True)
    common_dir = tmp_path / "other" / ".git"
    common_dir.mkdir(parents=True)
    common_dir.chmod(0o755)
    gitdir = common_dir / "worktrees" / "factory-task"
    gitdir.mkdir(parents=True)
    gitdir.chmod(0o755)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (workspace / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    agent = CodexAgent(codex_home=tmp_path / "codex-home", repo_root=workspace)

    with pytest.raises(ExecutionError, match="outside the approved repository boundary"):
        agent._trusted_git_common_dir()


def test_verifier_runs_pytest_networkless_and_read_only(tmp_path, monkeypatch):
    from dark_factory.execution import DockerPytestVerifier

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "docker-args.txt"
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        f"pathlib.Path({str(capture)!r}).write_text('\\n'.join(sys.argv[1:]))\n"
        "print('1 passed in 0.01s')\n",
        encoding="utf-8",
    )
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    workspace = tmp_path / "repo with spaces"
    workspace.mkdir()

    result = DockerPytestVerifier().verify(workspace)

    args = capture.read_text(encoding="utf-8").splitlines()
    assert result.passed is True
    assert result.summary == "1 tests passed"
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert f"{workspace}:/workspace:ro" in args
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in args


def test_worker_cannot_change_charter_policy_file(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class TestEditingAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            (workspace / "docs").mkdir()
            (workspace / "docs" / "CHARTER.md").write_text("def test_passes(): pass\n", encoding="utf-8")
            return result

    runner.agent = TestEditingAgent(calls)
    with pytest.raises(ExecutionError, match="protected policy"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert calls == ["preflight", "agent"]
    assert publisher.published == []


@pytest.mark.parametrize("protected_path", ["docs/CODEOWNERS", "CODEOWNERS", ".github/CODEOWNERS"])
def test_worker_cannot_change_codeowners_files(tmp_path, protected_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class OwnershipEditingAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            path = workspace / protected_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("owner changed", encoding="utf-8")
            return result

    runner.agent = OwnershipEditingAgent(calls)
    with pytest.raises(ExecutionError, match="protected policy"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert calls == ["preflight", "agent"]
    assert publisher.published == []


def test_worker_can_propose_coordinator_source_only_as_draft_pr(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class CoordinatorChangeAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            source = workspace / "src" / "dark_factory" / "cli.py"
            source.parent.mkdir(parents=True)
            source.write_text("# proposed coordinator change\\n", encoding="utf-8")
            return result

    runner.agent = CoordinatorChangeAgent(calls)
    result = runner.execute(task["id"])

    assert result.status == "draft_pr"
    assert store.list()[0]["status"] == "draft_pr"
    assert publisher.published
    assert calls == ["preflight", "agent", "verify", "publish"]


def test_worker_can_propose_test_changes_in_a_reviewable_draft_pr(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class TestAddingAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            tests = workspace / "tests"
            tests.mkdir()
            (tests / "test_addition.py").write_text("def test_added(): assert True\n", encoding="utf-8")
            return result

    class TestAwareVerifier(PassingVerifier):
        def verify(self, workspace):
            assert (workspace / "tests" / "test_addition.py").is_file()
            return super().verify(workspace)

    runner.agent = TestAddingAgent(calls)
    runner.verifier = TestAwareVerifier(calls)
    result = runner.execute(task["id"])

    assert result.status == "draft_pr"
    assert store.list()[0]["status"] == "draft_pr"
    assert publisher.published
    assert calls == ["preflight", "agent", "verify", "publish"]



def test_secret_like_diff_is_blocked_before_publication(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class SecretWritingAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            prefix = "api" + "_key = "
            fake_value = "not-real-" + "test-value-0000"
            (workspace / "settings.py").write_text(prefix + '"' + fake_value + '"\n', encoding="utf-8")
            return result

    runner.agent = SecretWritingAgent(calls)
    with pytest.raises(ExecutionError, match="credential pattern"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert calls == ["preflight", "agent", "verify"]
    assert publisher.published == []


def test_binary_secret_like_staged_blob_is_blocked_before_publication(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class BinarySecretWritingAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            target = workspace / "assets" / "opaque.bin"
            target.parent.mkdir()
            target.write_bytes(b"\x00ghp_" + b"A" * 30)
            return result

    runner.agent = BinarySecretWritingAgent(calls)
    with pytest.raises(ExecutionError, match="credential pattern"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert calls == ["preflight", "agent", "verify"]
    assert publisher.published == []



def test_task_run_cli_requires_explicit_repository_and_isolation_paths():
    from dark_factory.cli import build_parser

    args = build_parser().parse_args([
        "task-run", "/state", "12345678-1234-1234-1234-123456789abc",
        "--repo", "/repo", "--work-root", "/isolated/runs",
        "--repo-slug", "Danny-agent/danny-dark-factory", "--base", "main",
        "--codex-home", "/isolated/codex-home",
    ])

    assert args.command == "task-run"
    assert args.repo == Path("/repo")
    assert args.work_root == Path("/isolated/runs")
    assert args.repo_slug == "Danny-agent/danny-dark-factory"
    assert args.base == "main"
    assert args.codex_home == Path("/isolated/codex-home")


def test_preflight_failure_leaves_task_queued(tmp_path):
    calls: list[str] = []

    class MissingVerifier:
        def preflight(self):
            calls.append("verifier-preflight")
            raise ExecutionError("verification image unavailable")

        def verify(self, workspace):
            calls.append("verify")
            return VerificationResult(True, "should not run")

    verifier = MissingVerifier()
    runner, store, task, runner_calls, publisher = make_runner(tmp_path, verifier=verifier)

    with pytest.raises(ExecutionError, match="verification image unavailable"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "queued"
    assert runner_calls == ["preflight"]
    assert calls == ["verifier-preflight"]
    assert publisher.published == []


def test_docker_verifier_requires_the_local_image_before_claim(tmp_path, monkeypatch):
    from dark_factory.execution import DockerPytestVerifier

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n", encoding="utf-8")
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")

    with pytest.raises(ExecutionError, match="verification image"):
        DockerPytestVerifier().preflight()


def test_publisher_requires_write_access_and_enforced_human_review(tmp_path, monkeypatch):
    from dark_factory.execution import GitHubPublisher

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    protection = json.dumps({
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "enforce_admins": {"enabled": True},
    })
    gh.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "args=sys.argv[1:]\n"
        "if args[:2] == ['auth', 'status']:\n    raise SystemExit(0)\n"
        "if args[:2] == ['repo', 'view']:\n    print('ADMIN'); raise SystemExit(0)\n"
        f"if args[:1] == ['api']:\n    print({protection!r}); raise SystemExit(0)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")

    GitHubPublisher("demo/project").preflight()


def test_remote_base_is_fetched_before_the_worker_starts(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)
    repo = tmp_path / "repo"
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "--set-upstream", "origin", "main")
    git(repo, "fetch", "origin")

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(bare), str(other)], check=True, capture_output=True)
    git(other, "config", "user.name", "Remote Contributor")
    git(other, "config", "user.email", "remote@example.invalid")
    (other / "README.md").write_text("new base\n", encoding="utf-8")
    git(other, "add", "README.md")
    git(other, "commit", "-m", "advance remote base")
    git(other, "push", "origin", "main")
    latest_base = git(other, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "origin/main") != latest_base
    runner.base_ref = "origin/main"

    result = runner.execute(task["id"])

    assert git(repo, "show", "-s", "--format=%P", result.commit_sha) == latest_base
    assert store.list()[0]["status"] == "draft_pr"


def test_worker_symlink_is_rejected_before_verification_and_publication(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of repository", encoding="utf-8")

    class SymlinkAgent:
        def preflight(self):
            pass

        def run(self, task, workspace):
            (workspace / "result.txt").write_text("implemented\n", encoding="utf-8")
            (workspace / "escape.txt").symlink_to(outside)
            return AgentResult(0, "created link")

    runner.agent = SymlinkAgent()

    with pytest.raises(ExecutionError, match="symlink"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert publisher.published == []
    assert "verify" not in calls


def test_worker_cannot_redirect_worktree_git_metadata(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class RedirectGitAgent:
        def preflight(self):
            pass

        def run(self, task, workspace):
            (workspace / ".git").write_text("gitdir: /outside/repository/.git\\n", encoding="utf-8")
            (workspace / "result.txt").write_text("implemented\\n", encoding="utf-8")
            return AgentResult(0, "redirected worktree metadata")

    runner.agent = RedirectGitAgent()

    with pytest.raises(ExecutionError, match="Git metadata"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert "verify" not in calls
    assert publisher.published == []


def test_codex_home_rejects_symlinked_parent(tmp_path):
    from dark_factory.execution import CodexAgent

    target = tmp_path / "private-target"
    target.mkdir()
    link = tmp_path / "codex-parent"
    link.symlink_to(target, target_is_directory=True)
    agent = CodexAgent(executable="/bin/true", codex_home=link / "dedicated-home")

    with pytest.raises(ExecutionError, match="symlink"):
        agent._base_env()


def test_codex_home_rejects_ancestor_owned_by_another_user(tmp_path, monkeypatch):
    from dark_factory.execution import CodexAgent

    parent = tmp_path / "parent"
    parent.mkdir()
    original_open = os.open
    original_fstat = os.fstat
    opened: dict[int, str] = {}

    def tracking_open(name, flags, mode=0o777, *, dir_fd=None):
        fd = original_open(name, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None:
            opened[fd] = str(name)
        return fd

    def simulated_foreign_owner(fd):
        info = original_fstat(fd)
        values = list(info)
        if opened.get(fd) == "parent":
            values[4] = os.getuid() + 1
        return os.stat_result(values)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fstat", simulated_foreign_owner)
    agent = CodexAgent(executable="/bin/true", codex_home=parent / "codex-home")

    with pytest.raises(ExecutionError, match="ancestor ownership"):
        agent._base_env()


def test_codex_home_group_lookup_handles_unlisted_primary_gid(monkeypatch):
    import dark_factory.execution as execution

    def missing_group(gid):
        raise KeyError(gid)

    monkeypatch.setattr(execution.grp, "getgrgid", missing_group)
    monkeypatch.setattr(
        execution.pwd,
        "getpwall",
        lambda: [SimpleNamespace(pw_gid=os.getgid(), pw_uid=os.getuid())],
    )

    assert execution._group_has_other_users(os.getgid()) is False
    assert execution._group_has_other_users(os.getgid() + 1) is True


def test_codex_home_rejects_reachable_group_writable_ancestor(tmp_path, monkeypatch):
    import dark_factory.execution as execution
    from dark_factory.execution import CodexAgent

    monkeypatch.setattr(execution, "_group_has_other_users", lambda gid: True)

    parent = tmp_path / "parent"
    parent.mkdir()
    home = parent / "codex-home"
    original_open = os.open
    original_fstat = os.fstat
    opened: dict[int, str] = {}

    def tracking_open(name, flags, mode=0o777, *, dir_fd=None):
        fd = original_open(name, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None:
            opened[fd] = str(name)
        return fd

    def simulated_shared_permissions(fd):
        info = original_fstat(fd)
        name = opened.get(fd)
        mode = int(info.st_mode)
        if name == "parent":
            mode = (mode & ~0o070) | 0o020
        elif name is not None:
            mode = (mode & ~0o070) | stat.S_IXGRP
        values = list(info)
        values[0] = mode
        return os.stat_result(values)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fstat", simulated_shared_permissions)
    agent = CodexAgent(executable="/bin/true", codex_home=home)

    with pytest.raises(ExecutionError, match="ancestor"):
        agent._base_env()

    assert not home.exists()


def test_codex_home_allows_private_group_writable_ancestor(tmp_path, monkeypatch):
    import dark_factory.execution as execution
    from dark_factory.execution import CodexAgent

    monkeypatch.setattr(execution, "_group_has_other_users", lambda gid: False)
    parent = tmp_path / "parent"
    parent.mkdir()
    home = parent / "codex-home"
    original_open = os.open
    original_fstat = os.fstat
    opened: dict[int, str] = {}

    def tracking_open(name, flags, mode=0o777, *, dir_fd=None):
        fd = original_open(name, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None:
            opened[fd] = str(name)
        return fd

    def simulated_private_group_permissions(fd):
        info = original_fstat(fd)
        name = opened.get(fd)
        mode = int(info.st_mode)
        if name == "parent":
            mode = (mode & ~0o070) | 0o030
        elif name is not None:
            mode = (mode & ~0o070) | stat.S_IXGRP
        values = list(info)
        values[0] = mode
        return os.stat_result(values)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fstat", simulated_private_group_permissions)
    agent = CodexAgent(executable="/bin/true", codex_home=home)

    agent._base_env()

    assert home.stat().st_mode & 0o777 == 0o700


def test_codex_home_refuses_symlink_swap_during_secure_open(tmp_path, monkeypatch):
    from dark_factory.execution import CodexAgent

    target = tmp_path / "target"
    target.mkdir()
    os.chmod(target, 0o755)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    original_open = os.open
    original_chmod = os.chmod
    swapped = False

    def swap_home_to_symlink():
        nonlocal swapped
        os.rmdir(codex_home)
        codex_home.symlink_to(target, target_is_directory=True)
        swapped = True

    def racing_open(name, flags, mode=0o777, *, dir_fd=None):
        if name == codex_home.name and not swapped:
            swap_home_to_symlink()
        return original_open(name, flags, mode, dir_fd=dir_fd)

    def racing_chmod(path, mode, *, dir_fd=None, follow_symlinks=True):
        if Path(path) == codex_home and not swapped:
            swap_home_to_symlink()
        kwargs = {}
        if dir_fd is not None:
            kwargs["dir_fd"] = dir_fd
        if not follow_symlinks:
            kwargs["follow_symlinks"] = False
        return original_chmod(path, mode, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    monkeypatch.setattr(os, "chmod", racing_chmod)
    agent = CodexAgent(executable="/bin/true", codex_home=codex_home)

    try:
        agent._base_env()
    except ExecutionError:
        pass

    assert swapped
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_existing_checkout_symlink_is_rejected_before_worker_execution(tmp_path):
    runner, store, task, _, publisher = make_runner(tmp_path)
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    git(repo, "add", "escape")
    git(repo, "commit", "-m", "add symlink")
    ran = []

    class TouchThroughLink:
        def preflight(self):
            pass

        def run(self, task, workspace):
            ran.append(True)
            (workspace / "escape" / "outside.txt").write_text("outside", encoding="utf-8")
            return AgentResult(0, "wrote through symlink")

    runner.agent = TouchThroughLink()

    with pytest.raises(ExecutionError, match="symlink"):
        runner.execute(task["id"])

    assert ran == []
    assert not (outside / "outside.txt").exists()
    assert store.list()[0]["status"] == "blocked"
    assert publisher.published == []


def test_coordinator_commit_does_not_execute_repository_hooks(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)
    repo = tmp_path / "repo"
    marker = tmp_path / "hook-fired.txt"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)

    result = runner.execute(task["id"])

    assert result.status == "draft_pr"
    assert not marker.exists()


@pytest.mark.parametrize("path", [".gitattributes", ".gitmodules"])
def test_worker_cannot_change_git_execution_metadata(tmp_path, path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class GitMetadataAgent:
        def preflight(self):
            pass

        def run(self, task, workspace):
            (workspace / path).write_text("metadata\n", encoding="utf-8")
            (workspace / "result.txt").write_text("implemented\n", encoding="utf-8")
            return AgentResult(0, "changed metadata")

    runner.agent = GitMetadataAgent()

    with pytest.raises(ExecutionError, match="protected policy"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert publisher.published == []


def test_publish_intent_is_persisted_before_external_publication(tmp_path):
    runner, store, task, _, _ = make_runner(tmp_path)
    url = "https://github.com/example/repo/pull/7"

    class ManifestCheckingPublisher:
        def publish(self, workspace, branch, task, commit_sha, verification, base_sha):
            manifests = list((store.root / "runs").glob("*.json"))
            assert len(manifests) == 1
            intent = json.loads(manifests[0].read_text(encoding="utf-8"))
            assert intent["phase"] == "publishing"
            assert intent["commit_sha"] == commit_sha
            assert intent["base_sha"] == base_sha
            return url

    runner.publisher = ManifestCheckingPublisher()
    result = runner.execute(task["id"])

    manifest_path = next((store.root / "runs").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.status == "draft_pr"
    assert manifest["phase"] == "draft_pr"
    assert manifest["pull_request_url"] == url


def test_manifest_failure_after_publication_still_records_draft_pr_state(tmp_path):
    runner, store, task, _, _ = make_runner(tmp_path)
    original_record = runner._record_manifest
    writes = 0

    def fail_second_write(attempt_id, values):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated manifest failure")
        original_record(attempt_id, values)

    runner._record_manifest = fail_second_write

    with pytest.raises(ExecutionError, match="draft PR exists at https://github.com/example/repo/pull/7"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "draft_pr"
    manifests = list((store.root / "runs").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["phase"] == "publishing"
