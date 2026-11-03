from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from dark_factory.execution import ExecutionError, GitHubPublisher, VerificationResult


def install_fake_commands(tmp_path: Path, monkeypatch, head_sha: str) -> tuple[Path, Path, str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    commit_sha = "a" * 40
    base_sha = "b" * 40
    git_capture = tmp_path / "git-push.json"
    gh_capture = tmp_path / "gh-create.json"
    git = bin_dir / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args=sys.argv[1:]\n"
        "if args[-2:] == ['get-url', 'origin']:\n    print(os.environ['FAKE_REMOTE_URL'])\n"
        f"else: pathlib.Path({str(git_capture)!r}).write_text(json.dumps(args))\n",
        encoding="utf-8",
    )
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args=sys.argv[1:]\n"
        "if args[:2] == ['pr', 'create']:\n"
        f" pathlib.Path({str(gh_capture)!r}).write_text(json.dumps(args))\n"
        " print('https://github.com/demo/project/pull/12')\n"
        "elif args[:1] == ['api']:\n"
        " print(os.environ['FAKE_PULL_JSON'])\n"
        "else: raise SystemExit(9)\n",
        encoding="utf-8",
    )
    git.chmod(0o700)
    gh.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    pr = {
        "state": "open",
        "draft": True,
        "base": {"ref": "main", "sha": base_sha},
        "head": {"ref": "factory/12345678-abcdef12", "sha": head_sha, "repo": {"full_name": "demo/project"}},
    }
    monkeypatch.setenv("FAKE_PULL_JSON", json.dumps(pr))
    return git_capture, gh_capture, commit_sha, base_sha


def test_github_publisher_requires_exact_base_and_head_commit(tmp_path, monkeypatch):
    git_capture, gh_capture, commit_sha, base_sha = install_fake_commands(tmp_path, monkeypatch, "f" * 40)
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    task = {"id": "12345678-aaaa-bbbb-cccc-dddddddddddd", "description": "private task details"}
    publisher = GitHubPublisher("demo/project", base="main")

    with pytest.raises(ExecutionError, match="exact commit"):
        publisher.publish(
            workspace,
            "factory/12345678-abcdef12",
            task,
            commit_sha,
            VerificationResult(True, "2 tests passed"),
            base_sha,
        )

    assert git_capture.exists()
    args = json.loads(gh_capture.read_text(encoding="utf-8"))
    assert "private task details" not in " ".join(args)
    assert "--draft" in args
    assert "--base" in args and args[args.index("--base") + 1] == "main"


def test_publisher_rejects_pull_request_head_from_a_different_repository(tmp_path, monkeypatch):
    _, _, commit_sha, base_sha = install_fake_commands(tmp_path, monkeypatch, "a" * 40)
    pr = json.loads(os.environ["FAKE_PULL_JSON"])
    pr["head"]["repo"]["full_name"] = "fork-owner/project"
    monkeypatch.setenv("FAKE_PULL_JSON", json.dumps(pr))
    workspace = tmp_path / "worktree"
    workspace.mkdir()

    with pytest.raises(ExecutionError, match="head repository"):
        GitHubPublisher("demo/project").publish(
            workspace,
            "factory/12345678-abcdef12",
            {"id": "12345678-aaaa-bbbb-cccc-dddddddddddd"},
            commit_sha,
            VerificationResult(True, "2 tests passed"),
            base_sha,
        )


def test_publisher_rejects_an_origin_remote_for_another_repository(tmp_path, monkeypatch):
    install_fake_commands(tmp_path, monkeypatch, "a" * 40)
    monkeypatch.setenv("FAKE_REMOTE_URL", "https://github.com/other/project.git")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    with pytest.raises(ExecutionError, match="origin remote"):
        GitHubPublisher("demo/project").preflight_repo(workspace)
