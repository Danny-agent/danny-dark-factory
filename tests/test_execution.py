from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dark_factory.execution import (
    AgentResult,
    ExecutionError,
    TaskExecutor,
    VerificationResult,
)
from dark_factory.store import TaskStore


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


def test_medium_risk_task_is_not_claimed_or_executed(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path, risk="medium")

    with pytest.raises(ExecutionError, match="only low-risk"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "queued"
    assert calls == []
    assert publisher.published == []


def test_codex_worker_uses_isolated_identity_and_workspace_write_sandbox(tmp_path, monkeypatch):
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

    agent = CodexAgent(executable=str(executable), codex_home=codex_home)
    result = agent.run({"id": "task", "title": "safe", "description": "bounded", "acceptance": ["passes"]}, workspace)

    invocation = json.loads(capture.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert invocation["env"]["HOME"] == str(codex_home)
    assert invocation["env"]["CODEX_HOME"] == str(codex_home)
    assert all(key not in invocation["env"] for key in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"))
    assert invocation["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert invocation["env"]["GH_CONFIG_DIR"] != str(Path.home() / ".config" / "gh")
    assert "--sandbox" in invocation["argv"]
    assert "workspace-write" in invocation["argv"]
    assert "--ephemeral" in invocation["argv"]
    assert "--ignore-user-config" in invocation["argv"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in invocation["argv"]
    assert "UNTRUSTED TASK JSON" in invocation["prompt"]
    assert '"description": "bounded"' in invocation["prompt"]


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


def test_worker_cannot_change_the_test_suite(tmp_path):
    runner, store, task, calls, publisher = make_runner(tmp_path)

    class TestEditingAgent(WritingAgent):
        def run(self, task, workspace):
            result = super().run(task, workspace)
            (workspace / "tests").mkdir()
            (workspace / "tests" / "test_bypass.py").write_text("def test_passes(): pass\n", encoding="utf-8")
            return result

    runner.agent = TestEditingAgent(calls)
    with pytest.raises(ExecutionError, match="protected policy"):
        runner.execute(task["id"])

    assert store.list()[0]["status"] == "blocked"
    assert calls == ["preflight", "agent"]
    assert publisher.published == []


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


def test_codex_home_rejects_reachable_group_writable_ancestor(tmp_path, monkeypatch):
    from dark_factory.execution import CodexAgent

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
