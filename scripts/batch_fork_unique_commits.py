#!/usr/bin/env python3
"""Process upstream forks sequentially and retain only unique fork commits.

The source commit cache is kept. Each fork's Git/complete commit cache and
full matching outputs are temporary and are removed immediately after that
fork's summary row is appended.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

csv.field_size_limit(sys.maxsize)

SUMMARY_FIELDS = [
    "fork_full_name", "fork_html_url", "default_branch", "processed_at", "status",
    "fork_commit_count", "same_commit_count", "equivalent_patch_count",
    "fork_unique_commit_count", "fork_source_author_commit_count",
    "fork_merge_skipped_count", "fork_unique_files_changed", "fork_unique_additions",
    "fork_unique_deletions", "unique_commits_csv", "error_msg",
]


class BatchInterrupted(Exception):
    """Raised by the signal handler so run_one() can clean up in finally."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-analyze forks and retain only fork-unique commit CSVs."
    )
    parser.add_argument("--fork-csv", type=Path, default=Path("upstream-forks.csv"))
    parser.add_argument("--source-repo", default="shy3130/tick-stock-panel")
    parser.add_argument("--source-branch", default="main")
    parser.add_argument("--source-cache", type=Path, default=Path("/tmp/tsp-fork-analysis/commits/shy3130__tick-stock-panel.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/tsp-fork-analysis/batch"), help="Retained per-fork unique commit CSV directory")
    parser.add_argument("--summary-csv", type=Path, default=Path("fork_unique_summary.csv"), help="Incremental summary CSV path (default: current directory)")
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/tsp-fork-analysis/batch-work"))
    parser.add_argument("--git-timeout", type=float, default=180, help="Timeout for each Git/cache operation in seconds")
    parser.add_argument("--repo-timeout", type=float, default=300, help="Total timeout for one fork in seconds")
    parser.add_argument("--limit", type=int, help="Process at most N forks; useful for a smoke test")
    parser.add_argument("--only-fork", help="Process one exact OWNER/REPO")
    parser.add_argument("--force-source", action="store_true", help="Regenerate source cache")
    parser.add_argument("--resume", action="store_true", help="Skip forks already completed with status=ok")
    parser.add_argument("--keep-work", action="store_true", help="Keep per-fork work directories for debugging")
    return parser.parse_args()


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Stop a command and all descendants, including Git children."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def run_command(command: list[str], timeout: float) -> tuple[int, str]:
    """Run in its own process group and terminate the whole group on timeout."""
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=max(0.1, timeout))
        except subprocess.TimeoutExpired as exc:
            terminate_process_group(process)
            partial = exc.output or ""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            return 124, f"命令超时（{timeout:.1f}s）：{partial}"
        return process.returncode, output or ""
    except BatchInterrupted:
        if process is not None:
            terminate_process_group(process)
        raise
    except OSError as exc:
        return 1, str(exc)


def load_forks(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    result: list[dict[str, str]] = []
    for row in rows:
        full_name = (row.get("full_name") or row.get("fork_full_name") or "").strip()
        if not full_name or full_name == "shy3130/tick-stock-panel":
            continue
        result.append({
            "full_name": full_name,
            "html_url": row.get("html_url") or row.get("fork_html_url") or f"https://github.com/{full_name}",
            "default_branch": row.get("default_branch") or "main",
        })
    return result


def read_completed(summary_path: Path) -> set[str]:
    if not summary_path.exists():
        return set()
    try:
        with summary_path.open(newline="", encoding="utf-8-sig") as stream:
            return {row.get("fork_full_name", "") for row in csv.DictReader(stream) if row.get("status") == "ok"}
    except (OSError, csv.Error):
        return set()


def ensure_summary_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS).writeheader()


def append_summary(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", newline="", encoding="utf-8-sig") as stream:
        csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS).writerow({field: row.get(field, "") for field in SUMMARY_FIELDS})
        stream.flush()
        os.fsync(stream.fileno())


def integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def make_slug(full_name: str) -> str:
    return full_name.replace("/", "__").replace("\\", "_")


def error_text(step: str, code: int, output: str) -> str:
    text = output.strip()
    lower = text.lower()
    if "repository not found" in lower or "could not read from remote repository" in lower:
        reason = "仓库不存在或当前不可访问"
    elif "authentication failed" in lower or "permission denied" in lower:
        reason = "GitHub 认证或权限失败"
    elif code == 124 or "命令超时" in text:
        reason = "Git 操作超时"
    else:
        reason = "Git/批处理命令失败"
    return f"{step}失败（{reason}, exit={code}）: {text[-2000:]}"


def empty_row(fork: dict[str, str], now: str, status: str, error: str) -> dict[str, Any]:
    return {
        "fork_full_name": fork["full_name"], "fork_html_url": fork["html_url"],
        "default_branch": fork["default_branch"], "processed_at": now, "status": status,
        "fork_commit_count": "", "same_commit_count": "", "equivalent_patch_count": "",
        "fork_unique_commit_count": "", "fork_source_author_commit_count": "",
        "fork_merge_skipped_count": "", "fork_unique_files_changed": "",
        "fork_unique_additions": "", "fork_unique_deletions": "",
        "unique_commits_csv": "", "error_msg": error,
    }


def run_one(args: argparse.Namespace, fork: dict[str, str], source_cache: Path, output_dir: Path, work_root: Path) -> dict[str, Any]:
    full_name = fork["full_name"]
    branch = fork["default_branch"]
    slug = make_slug(full_name)
    now = datetime.now(timezone.utc).isoformat()
    final_unique = output_dir / f"{slug}_fork_unique_commits.csv"
    fork_work = Path(tempfile.mkdtemp(prefix=f"{slug}-", dir=work_root))
    fork_cache_dir = fork_work / "cache"
    match_prefix = fork_work / "match"
    fork_cache = fork_cache_dir / f"{slug}.csv"
    deadline = time.monotonic() + args.repo_timeout

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"Fork 总处理超时（{args.repo_timeout:.1f}s）")
        return left

    try:
        cache_command = [
            sys.executable, str(Path(__file__).with_name("cache_repo_commits.py")),
            "--repo", full_name, "--branch", branch, "--cache-dir", str(fork_cache_dir),
            "--git-timeout", str(min(args.git_timeout, remaining())), "--force",
        ]
        print(f"[{full_name}] fetch/cache", flush=True)
        code, output = run_command(cache_command, remaining())
        if code != 0:
            raise RuntimeError(error_text("缓存", code, output))
        if not fork_cache.exists():
            raise RuntimeError(f"缓存脚本完成但找不到: {fork_cache}")

        match_command = [
            sys.executable, str(Path(__file__).with_name("match_cached_commit_patches.py")),
            "--source-csv", str(source_cache), "--fork-csv", str(fork_cache),
            "--output-prefix", str(match_prefix),
        ]
        print(f"[{full_name}] match", flush=True)
        code, output = run_command(match_command, remaining())
        if code != 0:
            raise RuntimeError(error_text("匹配", code, output))
        summary_path = match_prefix.with_name(match_prefix.name + "_summary.json")
        if not summary_path.exists():
            raise RuntimeError(f"匹配脚本完成但找不到: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        unique_count = integer(summary.get("fork_unique_commits_count"))
        unique_path = match_prefix.with_name(match_prefix.name + "_fork_unique_commits.csv")
        if unique_count > 0:
            if not unique_path.exists():
                raise RuntimeError(f"汇总显示有独有提交但找不到: {unique_path}")
            output_dir.mkdir(parents=True, exist_ok=True)
            os.replace(unique_path, final_unique)
            retained_path = str(final_unique)
        else:
            final_unique.unlink(missing_ok=True)
            retained_path = ""

        unique_rows: list[dict[str, str]] = []
        if retained_path:
            with open(retained_path, newline="", encoding="utf-8-sig") as stream:
                unique_rows = list(csv.DictReader(stream))
        return {
            "fork_full_name": full_name, "fork_html_url": fork["html_url"], "default_branch": branch,
            "processed_at": now, "status": "ok", "fork_commit_count": summary.get("fork_commit_count", ""),
            "same_commit_count": summary.get("same_commit_count", ""), "equivalent_patch_count": summary.get("equivalent_patch_count", ""),
            "fork_unique_commit_count": unique_count, "fork_source_author_commit_count": summary.get("fork_source_author_commit_count", ""),
            "fork_merge_skipped_count": summary.get("fork_merge_skipped_count", ""),
            "fork_unique_files_changed": sum(integer(row.get("files_changed")) for row in unique_rows),
            "fork_unique_additions": sum(integer(row.get("additions")) for row in unique_rows),
            "fork_unique_deletions": sum(integer(row.get("deletions")) for row in unique_rows),
            "unique_commits_csv": retained_path, "error_msg": "",
        }
    except BatchInterrupted as exc:
        final_unique.unlink(missing_ok=True)
        return empty_row(fork, now, "interrupted", str(exc) or "收到中断信号")
    except Exception as exc:
        final_unique.unlink(missing_ok=True)
        return empty_row(fork, now, "error", str(exc))
    finally:
        if not args.keep_work:
            shutil.rmtree(fork_work, ignore_errors=True)


def ensure_source_cache(args: argparse.Namespace) -> None:
    if args.source_cache.exists() and not args.force_source:
        print(f"使用源仓库缓存: {args.source_cache}", flush=True)
        return
    args.source_cache.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(Path(__file__).with_name("cache_repo_commits.py")),
        "--repo", args.source_repo, "--branch", args.source_branch,
        "--cache-dir", str(args.source_cache.parent), "--git-timeout", str(args.git_timeout), "--force",
    ]
    print(f"生成源仓库缓存: {args.source_repo}:{args.source_branch}", flush=True)
    code, output = run_command(command, args.repo_timeout)
    if code != 0:
        raise RuntimeError(error_text("源仓库缓存", code, output))
    if not args.source_cache.exists():
        raise RuntimeError(f"源仓库缓存脚本完成但找不到: {args.source_cache}")


def install_signal_handlers() -> None:
    def handler(signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name
        raise BatchInterrupted(f"收到 {name}，正在清理当前 Fork")
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> int:
    args = parse_args()
    install_signal_handlers()
    if args.limit is not None and args.limit <= 0:
        print("错误: --limit 必须为正整数", file=sys.stderr)
        return 1
    try:
        forks = load_forks(args.fork_csv)
    except (OSError, csv.Error) as exc:
        print(f"错误: 无法读取 Fork 列表: {exc}", file=sys.stderr)
        return 1
    if args.only_fork:
        forks = [fork for fork in forks if fork["full_name"] == args.only_fork]
    if args.limit is not None:
        forks = forks[: args.limit]
    if not forks:
        print("没有需要处理的 Fork", file=sys.stderr)
        return 1

    summary_path = args.summary_csv
    ensure_summary_header(summary_path)
    completed = read_completed(summary_path) if args.resume else set()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    try:
        ensure_source_cache(args)
    except BatchInterrupted:
        print("收到中断信号，未启动 Fork 处理", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    processed = 0
    for fork in forks:
        if fork["full_name"] in completed:
            print(f"[{fork['full_name']}] 已完成，跳过", flush=True)
            continue
        row = run_one(args, fork, args.source_cache, args.output_dir, args.work_dir)
        append_summary(summary_path, row)
        processed += 1
        print(f"[{fork['full_name']}] 完成 status={row['status']} unique={row['fork_unique_commit_count'] or 0}; 已立即写入 {summary_path}", flush=True)
        if row["status"] == "interrupted":
            return 130
    print(f"批处理结束，本次处理 {processed} 个 Fork；汇总: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
