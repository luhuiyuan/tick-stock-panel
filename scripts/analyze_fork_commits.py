#!/usr/bin/env python3
"""Analyze commit ID differences between a source repository and a fork.

This script intentionally does not require or test for a common ancestor. It
compares the complete set of commits reachable from each selected branch:

    fork-only  = fork commit IDs - source commit IDs
    source-only = source commit IDs - fork commit IDs

For every commit in either set, it records message/author/date, merge status,
changed files, and line additions/deletions. The set differences are not
claimed to be semantically authored changes when histories were rewritten or
are unrelated; they are commit-ID set differences by design.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass
class CommitChange:
    commit_id: str
    side: str
    author_name: str
    author_email: str
    author_date: str
    commit_message: str
    is_merge: bool
    parent_count: int
    files_changed: int
    additions: int
    deletions: int
    changed_files: list[str]
    error: str = ""


class GitCommandError(RuntimeError):
    def __init__(self, command: list[str], output: str) -> None:
        self.command = command
        self.output = output.strip()
        super().__init__(f"Git command failed: {' '.join(command)}: {self.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare source/fork reachable commit ID sets and inspect commit changes."
    )
    parser.add_argument("--source", required=True, help="Source repository OWNER/NAME")
    parser.add_argument("--fork", required=True, help="Fork repository OWNER/NAME")
    parser.add_argument("--source-branch", default="main", help="Source branch (default: main)")
    parser.add_argument("--fork-branch", default="main", help="Fork branch (default: main)")
    parser.add_argument("--format", choices=("json", "csv", "table"), default="json")
    parser.add_argument("--output", type=Path, help="Output file; default is stdout")
    parser.add_argument("--work-dir", type=Path, help="Keep temporary Git directory here")
    parser.add_argument("--git-timeout", type=float, default=300, help="Git command timeout in seconds")
    parser.add_argument(
        "--max-commits",
        type=int,
        help="Analyze at most this many commits per side after ID-set calculation",
    )
    return parser.parse_args()


def run_git(command: list[str], cwd: Path, timeout: float) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
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


def fetch_branch(repo_dir: Path, remote_url: str, branch: str, ref: str, timeout: float) -> None:
    run_git(
        ["git", "fetch", "--no-tags", remote_url, f"{branch}:{ref}"],
        repo_dir,
        timeout,
    )


def get_commit_ids(repo_dir: Path, ref: str, timeout: float) -> set[str]:
    output = run_git(["git", "rev-list", ref], repo_dir, timeout)
    return {line.strip() for line in output.splitlines() if line.strip()}


def parse_numstat(value: str) -> tuple[int, int]:
    if value == "-":
        return 0, 0
    try:
        return int(value), 0
    except ValueError:
        return 0, 0


def inspect_commit(repo_dir: Path, commit_id: str, side: str, timeout: float) -> CommitChange:
    metadata = run_git(
        [
            "git",
            "show",
            "-s",
            "--format=%an%x00%ae%x00%aI%x00%P%x00%s",
            commit_id,
        ],
        repo_dir,
        timeout,
    ).rstrip("\n")
    author_name, author_email, author_date, parents_text, message = metadata.split("\x00", 4)
    parents = [item for item in parents_text.split() if item]
    is_merge = len(parents) > 1

    # --numstat is deliberately used instead of parsing a human-readable stat.
    # Rename detection is disabled so additions/deletions remain explicit.
    diff_output = run_git(
        [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--numstat",
            "-r",
            "--no-renames",
            commit_id,
        ],
        repo_dir,
        timeout,
    )
    additions = 0
    deletions = 0
    changed_files: list[str] = []
    for line in diff_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, delete = parts[0], parts[1]
        filename = "\t".join(parts[2:])
        add_count, _ = parse_numstat(add)
        delete_count, _ = parse_numstat(delete)
        additions += add_count
        deletions += delete_count
        changed_files.append(filename)

    return CommitChange(
        commit_id=commit_id,
        side=side,
        author_name=author_name,
        author_email=author_email,
        author_date=author_date,
        commit_message=message,
        is_merge=is_merge,
        parent_count=len(parents),
        files_changed=len(changed_files),
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )


def get_commit_order(repo_dir: Path, ref: str, timeout: float) -> list[str]:
    output = run_git(["git", "rev-list", "--date-order", ref], repo_dir, timeout)
    return [line.strip() for line in output.splitlines() if line.strip()]


def analyze(args: argparse.Namespace) -> dict:
    source_url = f"https://github.com/{args.source}.git"
    fork_url = f"https://github.com/{args.fork}.git"
    source_ref = "refs/analysis/source"
    fork_ref = "refs/analysis/fork"

    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = Path(tempfile.mkdtemp(prefix="commit-analysis-", dir=args.work_dir))
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="commit-analysis-")
        repo_dir = Path(temp_context.name)

    try:
        run_git(["git", "init", "--bare", "--quiet"], repo_dir, args.git_timeout)
        print(f"fetch source: {args.source}:{args.source_branch}", file=sys.stderr)
        fetch_branch(repo_dir, source_url, args.source_branch, source_ref, args.git_timeout)
        print(f"fetch fork: {args.fork}:{args.fork_branch}", file=sys.stderr)
        fetch_branch(repo_dir, fork_url, args.fork_branch, fork_ref, args.git_timeout)

        source_ids = get_commit_ids(repo_dir, source_ref, args.git_timeout)
        fork_ids = get_commit_ids(repo_dir, fork_ref, args.git_timeout)
        fork_only_ids = fork_ids - source_ids
        source_only_ids = source_ids - fork_ids

        fork_order = [commit for commit in get_commit_order(repo_dir, fork_ref, args.git_timeout) if commit in fork_only_ids]
        source_order = [commit for commit in get_commit_order(repo_dir, source_ref, args.git_timeout) if commit in source_only_ids]
        if args.max_commits is not None:
            fork_order = fork_order[: args.max_commits]
            source_order = source_order[: args.max_commits]

        changes: list[CommitChange] = []
        for index, commit_id in enumerate(fork_order, 1):
            print(f"fork commit {index}/{len(fork_order)} {commit_id}", file=sys.stderr)
            try:
                changes.append(inspect_commit(repo_dir, commit_id, "fork_only", args.git_timeout))
            except GitCommandError as exc:
                changes.append(
                    CommitChange(
                        commit_id=commit_id,
                        side="fork_only",
                        author_name="",
                        author_email="",
                        author_date="",
                        commit_message="",
                        is_merge=False,
                        parent_count=0,
                        files_changed=0,
                        additions=0,
                        deletions=0,
                        changed_files=[],
                        error=str(exc),
                    )
                )
        for index, commit_id in enumerate(source_order, 1):
            print(f"source commit {index}/{len(source_order)} {commit_id}", file=sys.stderr)
            try:
                changes.append(inspect_commit(repo_dir, commit_id, "source_only", args.git_timeout))
            except GitCommandError as exc:
                changes.append(
                    CommitChange(
                        commit_id=commit_id,
                        side="source_only",
                        author_name="",
                        author_email="",
                        author_date="",
                        commit_message="",
                        is_merge=False,
                        parent_count=0,
                        files_changed=0,
                        additions=0,
                        deletions=0,
                        changed_files=[],
                        error=str(exc),
                    )
                )

        fork_changes = [change for change in changes if change.side == "fork_only"]
        source_changes = [change for change in changes if change.side == "source_only"]
        return {
            "source": f"{args.source}:{args.source_branch}",
            "fork": f"{args.fork}:{args.fork_branch}",
            "comparison_method": "reachable_commit_id_set_difference",
            "source_commit_count": len(source_ids),
            "fork_commit_count": len(fork_ids),
            "fork_only_commit_count": len(fork_only_ids),
            "source_only_commit_count": len(source_only_ids),
            "analyzed_fork_only_commit_count": len(fork_changes),
            "analyzed_source_only_commit_count": len(source_changes),
            "analysis_truncated": args.max_commits is not None,
            "commits": [asdict(change) for change in changes],
        }
    finally:
        if args.work_dir and repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        if temp_context:
            temp_context.cleanup()


def write_result(result: dict, output_format: str, output: Path | None) -> None:
    if output_format == "json":
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    elif output_format == "csv":
        fields = [
            "source", "fork", "comparison_method", "source_commit_count", "fork_commit_count",
            "fork_only_commit_count", "source_only_commit_count", "side", "commit_id",
            "author_name", "author_email", "author_date", "commit_message", "is_merge",
            "parent_count", "files_changed", "additions", "deletions", "changed_files", "error",
        ]
        rows = []
        for commit in result["commits"]:
            rows.append({
                "source": result["source"],
                "fork": result["fork"],
                "comparison_method": result["comparison_method"],
                "source_commit_count": result["source_commit_count"],
                "fork_commit_count": result["fork_commit_count"],
                "fork_only_commit_count": result["fork_only_commit_count"],
                "source_only_commit_count": result["source_only_commit_count"],
                **{key: (json.dumps(value, ensure_ascii=False) if key == "changed_files" else value) for key, value in commit.items()},
            })
        from io import StringIO
        stream = StringIO()
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        text = stream.getvalue()
    else:
        fork_commits = [c for c in result["commits"] if c["side"] == "fork_only"]
        source_commits = [c for c in result["commits"] if c["side"] == "source_only"]
        lines = [
            f"source: {result['source']}",
            f"fork: {result['fork']}",
            f"source commits: {result['source_commit_count']}",
            f"fork commits: {result['fork_commit_count']}",
            f"fork-only IDs: {result['fork_only_commit_count']}",
            f"source-only IDs: {result['source_only_commit_count']}",
            "",
            "fork-only commit messages:",
        ]
        lines.extend(f"- {c['commit_id'][:12]} {c['commit_message']} (+{c['additions']}/-{c['deletions']})" for c in fork_commits)
        lines.append("")
        lines.append("source-only commit messages:")
        lines.extend(f"- {c['commit_id'][:12]} {c['commit_message']} (+{c['additions']}/-{c['deletions']})" for c in source_commits)
        text = "\n".join(lines) + "\n"

    if output:
        output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def main() -> int:
    args = parse_args()
    try:
        result = analyze(args)
        write_result(result, args.format, args.output)
    except (GitCommandError, OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
