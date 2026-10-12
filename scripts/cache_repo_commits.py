#!/usr/bin/env python3
"""Cache one repository's commit metadata and patch information as CSV.

The cache lives outside the source repository. It is intentionally independent
of source/fork comparison: later analysis can match commit IDs and patch IDs
across per-repository CSV files.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CACHE_VERSION = "commit-cache/v1"
csv.field_size_limit(sys.maxsize)

CACHE_FIELDS = [
    "repository",
    "branch",
    "branch_tip_sha",
    "commit_id",
    "parent_ids",
    "is_merge",
    "author_name",
    "author_email",
    "author_date",
    "committer_name",
    "committer_email",
    "committer_date",
    "subject",
    "message",
    "patch_id",
    "patch_status",
    "files_changed",
    "additions",
    "deletions",
    "binary_files_changed",
    "file_changes",
    "cache_version",
    "scan_scope",
    "cached_commit_count",
    "scanned_at",
    "error",
]


@dataclass
class CommitRow:
    repository: str
    branch: str
    branch_tip_sha: str
    commit_id: str
    parent_ids: str
    is_merge: bool
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str
    subject: str
    message: str
    patch_id: str
    patch_status: str
    files_changed: int | str
    additions: int | str
    deletions: int | str
    binary_files_changed: int | str
    file_changes: str
    cache_version: str
    scan_scope: str
    cached_commit_count: int
    scanned_at: str
    error: str = ""


class GitCommandError(RuntimeError):
    def __init__(self, command: list[str], output: str) -> None:
        self.command = command
        self.output = output.strip()
        super().__init__(f"Git command failed: {' '.join(command)}: {self.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cache a repository's commit data into one CSV file.")
    parser.add_argument("--repo", required=True, help="Repository OWNER/NAME")
    parser.add_argument("--branch", default="main", help="Branch to scan (default: main)")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/tsp-fork-analysis/commits"),
        help="Cache directory outside the code repository",
    )
    parser.add_argument("--git-timeout", type=float, default=300, help="Git command timeout in seconds")
    parser.add_argument("--max-commits", type=int, help="Only cache the newest N commits; useful for a smoke test")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing matching cache")
    return parser.parse_args()


def run_git(command: list[str], cwd: Path, timeout: float, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError(command, str(exc)) from exc
    if result.returncode != 0:
        raise GitCommandError(command, result.stdout)
    return result.stdout


def cache_filename(repo: str) -> str:
    return repo.replace("/", "__") + ".csv"


def cache_matches(path: Path, repo: str, branch: str, tip_sha: str) -> bool:
    if not path.exists():
        return False
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, csv.Error):
        return False
    if not rows:
        return False
    row = rows[0]
    try:
        cached_count = int(row.get("cached_commit_count", ""))
    except (TypeError, ValueError):
        return False
    return (
        row.get("repository") == repo
        and row.get("branch") == branch
        and row.get("branch_tip_sha") == tip_sha
        and row.get("cache_version") == CACHE_VERSION
        and row.get("scan_scope") == "full"
        and cached_count == len(rows)
    )


def parse_metadata(repo_dir: Path, commit_id: str, timeout: float) -> dict[str, Any]:
    value = run_git(
        [
            "git",
            "show",
            "-s",
            "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%P%x00%s%x00%B",
            commit_id,
        ],
        repo_dir,
        timeout,
    ).rstrip("\n")
    fields = value.split("\x00", 8)
    if len(fields) != 9:
        raise GitCommandError(["git", "show", commit_id], "无法解析 commit 元数据")
    author_name, author_email, author_date, committer_name, committer_email, committer_date, parents, subject, message = fields
    parent_ids = parents.split() if parents else []
    return {
        "author_name": author_name,
        "author_email": author_email,
        "author_date": author_date,
        "committer_name": committer_name,
        "committer_email": committer_email,
        "committer_date": committer_date,
        "parent_ids": parent_ids,
        "subject": subject,
        "message": message.rstrip("\n"),
    }


def parse_numstat(repo_dir: Path, commit_id: str, timeout: float) -> tuple[dict[str, tuple[int | None, int | None]], int]:
    output = run_git(
        ["git", "diff-tree", "--root", "--numstat", "-r", "--no-renames", commit_id],
        repo_dir,
        timeout,
    )
    stats: dict[str, tuple[int | None, int | None]] = {}
    binary_count = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions, deletions = parts[0], parts[1]
        path = "\t".join(parts[2:])
        if additions == "-" or deletions == "-":
            stats[path] = (None, None)
            binary_count += 1
        else:
            stats[path] = (int(additions), int(deletions))
    return stats, binary_count


def parse_statuses(repo_dir: Path, commit_id: str, timeout: float) -> dict[str, str]:
    output = run_git(
        ["git", "diff-tree", "--root", "--name-status", "-r", "--no-renames", commit_id],
        repo_dir,
        timeout,
    )
    statuses: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            statuses[parts[1]] = parts[0]
    return statuses


def calculate_patch_id(repo_dir: Path, commit_id: str, timeout: float) -> str:
    patch = run_git(
        ["git", "diff-tree", "--root", "--no-ext-diff", "--no-renames", "-p", "-r", commit_id],
        repo_dir,
        timeout,
    )
    patch_id_output = run_git(["git", "patch-id", "--stable"], repo_dir, timeout, patch)
    return patch_id_output.split()[0] if patch_id_output.split() else ""


def inspect_commit(repo_dir: Path, repo: str, branch: str, tip_sha: str, commit_id: str, scanned_at: str, timeout: float) -> CommitRow:
    metadata = parse_metadata(repo_dir, commit_id, timeout)
    parents = metadata["parent_ids"]
    is_merge = len(parents) > 1
    if is_merge:
        return CommitRow(
            repository=repo,
            branch=branch,
            branch_tip_sha=tip_sha,
            commit_id=commit_id,
            parent_ids=json.dumps(parents, ensure_ascii=False),
            is_merge=True,
            author_name=metadata["author_name"],
            author_email=metadata["author_email"],
            author_date=metadata["author_date"],
            committer_name=metadata["committer_name"],
            committer_email=metadata["committer_email"],
            committer_date=metadata["committer_date"],
            subject=metadata["subject"],
            message=metadata["message"],
            patch_id="",
            patch_status="merge_skipped",
            files_changed="",
            additions="",
            deletions="",
            binary_files_changed="",
            file_changes="[]",
            cache_version=CACHE_VERSION,
            scan_scope="",
            cached_commit_count=0,
            scanned_at=scanned_at,
        )

    try:
        numstat, binary_count = parse_numstat(repo_dir, commit_id, timeout)
        statuses = parse_statuses(repo_dir, commit_id, timeout)
        file_changes = []
        additions = 0
        deletions = 0
        for path, (added, deleted) in numstat.items():
            if added is not None:
                additions += added
                deletions += deleted or 0
            file_changes.append(
                {
                    "path": path,
                    "status": statuses.get(path, "unknown"),
                    "additions": added,
                    "deletions": deleted,
                    "is_binary": added is None or deleted is None,
                }
            )
        patch_id = calculate_patch_id(repo_dir, commit_id, timeout)
        patch_status = "ok" if patch_id else "empty"
        error = ""
    except GitCommandError as exc:
        numstat = {}
        binary_count = 0
        additions = 0
        deletions = 0
        file_changes = []
        patch_id = ""
        patch_status = "error"
        error = str(exc)

    return CommitRow(
        repository=repo,
        branch=branch,
        branch_tip_sha=tip_sha,
        commit_id=commit_id,
        parent_ids=json.dumps(parents, ensure_ascii=False),
        is_merge=False,
        author_name=metadata["author_name"],
        author_email=metadata["author_email"],
        author_date=metadata["author_date"],
        committer_name=metadata["committer_name"],
        committer_email=metadata["committer_email"],
        committer_date=metadata["committer_date"],
        subject=metadata["subject"],
        message=metadata["message"],
        patch_id=patch_id,
        patch_status=patch_status,
        files_changed=len(file_changes),
        additions=additions,
        deletions=deletions,
        binary_files_changed=binary_count,
        file_changes=json.dumps(file_changes, ensure_ascii=False),
        cache_version=CACHE_VERSION,
        scan_scope="",
        cached_commit_count=0,
        scanned_at=scanned_at,
        error=error,
    )


def write_cache(path: Path, rows: list[CommitRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CACHE_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    if args.max_commits is not None and args.max_commits <= 0:
        print("错误: --max-commits 必须为正整数", file=sys.stderr)
        return 1

    cache_path = args.cache_dir / cache_filename(args.repo)
    temporary_context: tempfile.TemporaryDirectory[str] | None = None
    try:
        temporary_context = tempfile.TemporaryDirectory(prefix="repo-commit-cache-")
        repo_dir = Path(temporary_context.name)
        run_git(["git", "init", "--bare", "--quiet"], repo_dir, args.git_timeout)
        remote_url = f"https://github.com/{args.repo}.git"
        ref = "refs/cache/branch"
        print(f"fetch {args.repo}:{args.branch}", file=sys.stderr)
        run_git(["git", "fetch", "--no-tags", remote_url, f"{args.branch}:{ref}"], repo_dir, args.git_timeout)
        tip_sha = run_git(["git", "rev-parse", ref], repo_dir, args.git_timeout).strip()

        if cache_matches(cache_path, args.repo, args.branch, tip_sha) and not args.force:
            print(f"cache exists: {cache_path}", file=sys.stderr)
            return 0

        commit_ids = run_git(["git", "rev-list", "--date-order", ref], repo_dir, args.git_timeout).splitlines()
        commit_ids = [commit_id.strip() for commit_id in commit_ids if commit_id.strip()]
        if args.max_commits is not None:
            commit_ids = commit_ids[: args.max_commits]
        scanned_at = datetime.now(timezone.utc).isoformat()
        scan_scope = "partial" if args.max_commits is not None else "full"
        cached_commit_count = len(commit_ids)
        rows: list[CommitRow] = []
        for index, commit_id in enumerate(commit_ids, 1):
            print(f"commit {index}/{len(commit_ids)} {commit_id}", file=sys.stderr)
            rows.append(inspect_commit(repo_dir, args.repo, args.branch, tip_sha, commit_id, scanned_at, args.git_timeout))
        for row in rows:
            row.scan_scope = scan_scope
            row.cached_commit_count = cached_commit_count
        write_cache(cache_path, rows)
        print(f"wrote {len(rows)} rows: {cache_path}", file=sys.stderr)
    except (GitCommandError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary_context:
            temporary_context.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
