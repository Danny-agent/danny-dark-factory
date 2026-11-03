#!/usr/bin/env python3
"""One bounded automatic queue poll for the user systemd timer.

The factory coordinator performs the authoritative task/risk/checkout checks.
This wrapper only validates local configuration and launches one task-run-next.
"""

from __future__ import annotations

import grp
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def _group_has_other_users(gid: int) -> bool:
    try:
        group = grp.getgrgid(gid)
    except KeyError:
        if gid != os.getgid():
            return True
        return any(
            account.pw_gid == gid and account.pw_uid != os.getuid()
            for account in pwd.getpwall()
        )
    usernames = set(group.gr_mem)
    usernames.update(account.pw_name for account in pwd.getpwall() if account.pw_gid == gid)
    for username in usernames:
        try:
            if pwd.getpwnam(username).pw_uid != os.getuid():
                return True
        except KeyError:
            return True
    return False


def _no_symlink_path(path: Path, name: str) -> Path:
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path")
    if ".." in path.parts:
        raise ConfigurationError(f"{name} must not contain parent traversal")
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ConfigurationError(f"{name} path components cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ConfigurationError(f"{name} contains a symlink component")
        if component != absolute and not stat.S_ISDIR(info.st_mode):
            raise ConfigurationError(f"{name} has a non-directory ancestor")
        if info.st_uid not in {0, os.getuid()}:
            raise ConfigurationError(f"{name} has an ancestor owned by another user")
        root_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if not root_sticky and info.st_mode & stat.S_IWOTH:
            raise ConfigurationError(f"{name} has an other-writable path component")
        if not root_sticky and info.st_mode & stat.S_IWGRP and _group_has_other_users(info.st_gid):
            raise ConfigurationError(f"{name} has a group-writable path component shared with another user")
    return absolute


def _require_private_directory(path: Path, name: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ConfigurationError(f"{name} directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_mode & 0o700 != 0o700
    ):
        raise ConfigurationError(f"{name} must be runner-owned and private (mode 0700)")


def _ensure_private_directory_tree(root: Path, target: Path, name: str) -> None:
    if target != root and root not in target.parents:
        raise ConfigurationError(f"{name} must be inside the approved factory data root")
    root.parent.mkdir(parents=True, exist_ok=True)
    _no_symlink_path(root, "approved factory data root")
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ConfigurationError("approved factory data root cannot be created") from exc
    _require_private_directory(root, "approved factory data root")

    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ConfigurationError(f"{name} directory cannot be created") from exc
        _require_private_directory(current, name)


def _validate_private_directory_tree(root: Path, target: Path, name: str) -> None:
    if target != root and root not in target.parents:
        raise ConfigurationError(f"{name} must be inside the approved factory data root")
    _no_symlink_path(root, "approved factory data root")
    current = root
    for part in (None, *target.relative_to(root).parts):
        if part is not None:
            current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ConfigurationError(f"{name} directory cannot be inspected") from exc
        _require_private_directory(current, "approved factory data root" if current == root else name)


def _required_path(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"required setting is missing: {name}")
    return _no_symlink_path(Path(value).expanduser(), name)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _paths_are_separate(paths: tuple[Path, ...]) -> bool:
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path == other or path in other.parents or other in path.parents:
                return False
    return True


def _verify_runner_checkout(repo: Path, expected_sha: str, repo_slug: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ConfigurationError("FACTORY_RUNNER_SHA must be a 40-character lowercase commit SHA")

    def git_output(*arguments: str) -> str:
        try:
            result = subprocess.run(
                ["/usr/bin/git", "-C", str(repo), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env={"HOME": str(Path.home()), "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin", "GIT_OPTIONAL_LOCKS": "0"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigurationError("runner checkout provenance could not be verified") from exc
        if result.returncode != 0:
            raise ConfigurationError("runner checkout provenance could not be verified")
        return result.stdout.strip()

    repo_root = Path(git_output("rev-parse", "--show-toplevel"))
    if repo_root != repo:
        raise ConfigurationError("runner script must execute from the root of its Git checkout")
    if git_output("ls-files", "--error-unmatch", "scripts/auto_run_once.py") != "scripts/auto_run_once.py":
        raise ConfigurationError("runner script must be tracked in the pinned checkout")
    if git_output("rev-parse", "--verify", "HEAD") != expected_sha:
        raise ConfigurationError("runner revision does not match FACTORY_RUNNER_SHA")
    if git_output("status", "--porcelain", "--untracked-files=all"):
        raise ConfigurationError("runner checkout must be clean before dispatch")
    expected_origin = f"https://github.com/{repo_slug}.git"
    if git_output("remote", "get-url", "origin") != expected_origin:
        raise ConfigurationError("runner checkout origin does not match FACTORY_REPO_SLUG")


def _config(*, create_directories: bool = True) -> tuple[Path, Path, Path, str, str, Path]:
    repo = _required_path("FACTORY_REPO")
    state = _required_path("FACTORY_STATE")
    work_root = _required_path("FACTORY_WORK_ROOT")
    codex_home = _required_path("FACTORY_CODEX_HOME")
    repo_slug = os.environ.get("FACTORY_REPO_SLUG", "").strip()
    runner_sha = os.environ.get("FACTORY_RUNNER_SHA", "").strip()
    base = os.environ.get("FACTORY_BASE", "main").strip()

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_slug):
        raise ConfigurationError("FACTORY_REPO_SLUG must be OWNER/REPO")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", base) or base.startswith("-") or ".." in base:
        raise ConfigurationError("FACTORY_BASE is invalid")

    home = _no_symlink_path(Path.home(), "home directory")
    projects_root = _no_symlink_path(home / "projects", "approved projects root")
    state_root = _no_symlink_path(home / ".local" / "share" / "danny-dark-factory", "approved factory data root")
    script_path = _no_symlink_path(Path(os.path.abspath(__file__)), "runner script")
    script_repo = script_path.parent.parent
    if repo != script_repo or not _is_within(repo, projects_root):
        raise ConfigurationError("FACTORY_REPO must match this runner under ~/projects")
    if not (repo / "src" / "dark_factory" / "cli.py").is_file():
        raise ConfigurationError("FACTORY_REPO does not contain the factory CLI source")
    if not all(state_root in path.parents for path in (state, work_root, codex_home)):
        raise ConfigurationError("state, work, and Codex paths must be strict descendants of the approved factory data root")
    if not _paths_are_separate((repo, state, work_root, codex_home)):
        raise ConfigurationError("repo, state, work, and Codex paths must be disjoint")
    if create_directories:
        _ensure_private_directory_tree(state_root, state, "factory state")
        _ensure_private_directory_tree(state_root, work_root, "factory work root")
        _ensure_private_directory_tree(state_root, codex_home.parent, "Codex home parent")
    else:
        _validate_private_directory_tree(state_root, state, "factory state")
        _validate_private_directory_tree(state_root, work_root, "factory work root")
        _validate_private_directory_tree(state_root, codex_home.parent, "Codex home parent")
    _verify_runner_checkout(repo, runner_sha, repo_slug)
    return repo, state, work_root, repo_slug, base, codex_home


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _process_group_has_live_members(pgid: int) -> bool:
    proc_root = Path("/proc")
    try:
        entries = tuple(proc_root.iterdir())
    except OSError:
        return _process_group_exists(pgid)
    inaccessible = False
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            state = fields[0]
            process_group = int(fields[2])
        except FileNotFoundError:
            continue
        except (OSError, IndexError, ValueError):
            inaccessible = True
            continue
        if process_group == pgid and state not in {"Z", "X"}:
            return True
    return inaccessible and _process_group_exists(pgid)


def _terminate_dispatch_group(process: subprocess.Popen, *, grace_s: float = 3.0) -> bool:
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass

    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and _process_group_has_live_members(pgid):
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    if _process_group_has_live_members(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass

    kill_deadline = time.monotonic() + 3
    while time.monotonic() < kill_deadline and _process_group_has_live_members(pgid):
        time.sleep(0.05)
    return not _process_group_has_live_members(pgid)


def _run_dispatcher(command: list[str], *, env: dict[str, str], cwd: Path, timeout_s: int) -> int:
    try:
        process = subprocess.Popen(
            command,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"factory-auto-run: could not start dispatcher: {exc}", file=sys.stderr)
        return 127

    try:
        process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if not _terminate_dispatch_group(process):
            print("factory-auto-run: process group still has live members after SIGKILL", file=sys.stderr)
        print("factory-auto-run: invocation exceeded the configured time limit", file=sys.stderr)
        return 124
    return process.returncode


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    check_only = arguments == ["--check-config"]
    if arguments and not check_only:
        print("usage: auto_run_once.py [--check-config]", file=sys.stderr)
        return 2
    try:
        repo, state, work_root, repo_slug, base, codex_home = _config(create_directories=not check_only)
    except ConfigurationError as exc:
        print(f"factory-auto-run: {exc}", file=sys.stderr)
        return 2
    if check_only:
        print("factory-auto-run: configuration valid; no task dispatched")
        return 0

    try:
        username = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        username = str(os.getuid())
    home = Path.home()
    env = {
        "HOME": str(home),
        "USER": username,
        "LOGNAME": username,
        "PATH": os.pathsep.join((str(home / ".local" / "bin"), "/usr/local/bin", "/usr/bin", "/bin")),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(repo / "src"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [
        sys.executable,
        "-m",
        "dark_factory.cli",
        "task-run-next",
        str(state),
        "--repo",
        str(repo),
        "--work-root",
        str(work_root),
        "--repo-slug",
        repo_slug,
        "--base",
        base,
        "--codex-home",
        str(codex_home),
        "--agent-timeout",
        str(25 * 60),
        "--verification-timeout",
        "300",
    ]
    return _run_dispatcher(command, env=env, cwd=repo, timeout_s=44 * 60)


if __name__ == "__main__":
    raise SystemExit(main())
