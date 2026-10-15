#!/usr/bin/env python3
"""Generate the unique-commit CSV for one specified fork.

This is a thin orchestration entry point around the repository's canonical
commit-cache and SHA/stable-patch-id matching scripts. It deliberately does
not infer features or write a Markdown analysis report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_SCRIPT = SCRIPT_DIR / "cache_repo_commits.py"
MATCH_SCRIPT = SCRIPT_DIR / "match_cached_commit_patches.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate fork-unique commits for one specified fork."
    )
    parser.add_argument(
        "--source-repo",
        default="shy3130/tick-stock-panel",
        help="Source repository OWNER/NAME (default: shy3130/tick-stock-panel)",
    )
    parser.add_argument(
        "--source-branch",
        default="main",
        help="Source branch (default: main)",
    )
    parser.add_argument(
        "--fork-repo",
        required=True,
        help="Fork repository OWNER/NAME",
    )
    parser.add_argument(
        "--fork-branch",
        default="main",
        help="Fork branch (default: main)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output CSV path for fork-unique commits",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Keep temporary cache/match files under this directory for debugging",
    )
    parser.add_argument(
        "--git-timeout",
        type=float,
        default=300,
        help="Timeout for each Git operation in seconds (default: 300)",
    )
    parser.add_argument(
        "--repo-timeout",
        type=float,
        default=1800,
        help="Timeout for caching or matching one repository in seconds (default: 1800)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing output CSV",
    )
    return parser.parse_args()


def run(command: list[str], timeout: float) -> str:
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        output = result.stdout.strip()
        raise RuntimeError(
            f"命令失败（exit={result.returncode}）：{' '.join(command)}\n{output}"
        )
    return result.stdout


def cache_repository(
    repo: str,
    branch: str,
    cache_dir: Path,
    git_timeout: float,
    repo_timeout: float,
) -> Path:
    run(
        [
            sys.executable,
            str(CACHE_SCRIPT),
            "--repo",
            repo,
            "--branch",
            branch,
            "--cache-dir",
            str(cache_dir),
            "--git-timeout",
            str(git_timeout),
            "--force",
        ],
        timeout=repo_timeout,
    )
    cache_path = cache_dir / f"{repo.replace('/', '__')}.csv"
    if not cache_path.is_file():
        raise RuntimeError(f"缓存脚本完成但找不到输出：{cache_path}")
    return cache_path


def main() -> int:
    args = parse_args()
    if args.git_timeout <= 0 or args.repo_timeout <= 0:
        print("错误: --git-timeout 和 --repo-timeout 必须为正数", file=sys.stderr)
        return 2

    output = args.output.expanduser()
    if output.exists() and not args.force:
        print(f"错误: 输出文件已存在，使用 --force 覆盖：{output}", file=sys.stderr)
        return 2

    temp_root: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.work_dir:
            args.work_dir.mkdir(parents=True, exist_ok=True)
            work_dir = Path(tempfile.mkdtemp(prefix="specified-fork-", dir=args.work_dir))
        else:
            temp_root = tempfile.TemporaryDirectory(prefix="specified-fork-")
            work_dir = Path(temp_root.name)

        source_cache_dir = work_dir / "source-cache"
        fork_cache_dir = work_dir / "fork-cache"
        match_prefix = work_dir / "match"
        source_csv = cache_repository(
            args.source_repo,
            args.source_branch,
            source_cache_dir,
            args.git_timeout,
            args.repo_timeout,
        )
        fork_csv = cache_repository(
            args.fork_repo,
            args.fork_branch,
            fork_cache_dir,
            args.git_timeout,
            args.repo_timeout,
        )

        run(
            [
                sys.executable,
                str(MATCH_SCRIPT),
                "--source-csv",
                str(source_csv),
                "--fork-csv",
                str(fork_csv),
                "--output-prefix",
                str(match_prefix),
            ],
            timeout=args.repo_timeout,
        )

        unique_csv = match_prefix.with_name(match_prefix.name + "_fork_unique_commits.csv")
        summary_json = match_prefix.with_name(match_prefix.name + "_summary.json")
        if not unique_csv.is_file():
            raise RuntimeError(f"匹配脚本完成但找不到独有提交 CSV：{unique_csv}")

        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(unique_csv, output)
        summary = json.loads(summary_json.read_text(encoding="utf-8")) if summary_json.is_file() else {}
        print(
            json.dumps(
                {
                    "source_repository": args.source_repo,
                    "source_branch": args.source_branch,
                    "fork_repository": args.fork_repo,
                    "fork_branch": args.fork_branch,
                    "output": str(output),
                    "fork_unique_commits_count": summary.get("fork_unique_commits_count", 0),
                    "same_commit_count": summary.get("same_commit_count", 0),
                    "equivalent_patch_count": summary.get("equivalent_patch_count", 0),
                    "fork_source_author_commit_count": summary.get(
                        "fork_source_author_commit_count", 0
                    ),
                    "fork_merge_skipped_count": summary.get("fork_merge_skipped_count", 0),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_root is None and args.work_dir:
            # --work-dir is an explicit debugging request; preserve its contents.
            pass
        if temp_root is not None:
            temp_root.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
