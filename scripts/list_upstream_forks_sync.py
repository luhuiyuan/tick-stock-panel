#!/usr/bin/env python3
"""List and compare all forks of the current repository's GitHub parent.

GitHub API is used to discover the current repository's parent and its forks.
By default, commit comparison is performed with local Git objects instead of
GitHub's REST compare endpoint. This avoids treating a REST compare failure as
proof that two repositories have no common history.

Examples:
    python scripts/list_upstream_forks_sync.py
    python scripts/list_upstream_forks_sync.py --format csv --output forks.csv
    python scripts/list_upstream_forks_sync.py --repo luhuiyuan/tick-stock-panel
    python scripts/list_upstream_forks_sync.py --compare-mode api
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


GITHUB_API = "https://api.github.com"
@dataclass
class ForkResult:
    name: str
    full_name: str
    html_url: str
    default_branch: str
    archived: bool
    updated_at: str
    ahead: int | None
    behind: int | None
    status: str
    comparison_source: str
    merge_base: str = ""
    fetch_depth: int | None = None
    source_reachable_commits: int | None = None
    fork_reachable_commits: int | None = None
    error_type: str = ""
    error: str = ""


class GitHubApiError(RuntimeError):
    """An error returned while calling GitHub's API."""


class GitCommandError(RuntimeError):
    """A Git command failed."""

    def __init__(self, message: str, command: list[str], output: str = "") -> None:
        self.command = command
        self.output = output.strip()
        detail = f": {self.output}" if self.output else ""
        super().__init__(f"{message}{detail}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find the current repository's upstream GitHub repository, then "
            "list and compare all of its forks."
        )
    )
    parser.add_argument(
        "--repo",
        help="Current GitHub repository in OWNER/NAME form (default: detect from git remote)",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument("--output", type=Path, help="Write output to this file instead of stdout")
    parser.add_argument(
        "--include-source",
        action="store_true",
        help="Also include the upstream source repository as the first result",
    )
    parser.add_argument("--timeout", type=float, default=30, help="HTTP timeout in seconds (default: 30)")
    parser.add_argument(
        "--git-timeout",
        type=float,
        default=300,
        help="Timeout for each Git network command in seconds (default: 300)",
    )
    parser.add_argument(
        "--compare-mode",
        choices=("auto", "local", "api"),
        default="auto",
        help="Comparison provider: API first with local fallback (default), local Git only, or API only",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Maximum concurrent API comparisons (default: 2; local Git remains sequential)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Keep temporary Git repositories under this directory instead of the system temp directory",
    )
    parser.add_argument(
        "--only-fork",
        action="append",
        help="Compare only this fork full name; may be repeated",
    )
    return parser.parse_args()


def detect_current_repo() -> str:
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], text=True, stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("无法从 git remote origin 获取当前仓库地址") from exc

    match = re.search(r"github\.com[:/]([^/ :]+/[^/ ]+?)(?:\.git)?$", remote)
    if not match:
        raise RuntimeError(f"origin 不是可识别的 GitHub 地址: {remote}")
    return match.group(1)


def api_get(path: str, token: str | None, timeout: float) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "upstream-forks-sync-script",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{GITHUB_API}{path}", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("message", detail)
        except json.JSONDecodeError:
            message = detail or exc.reason
        raise GitHubApiError(f"HTTP {exc.code}: {message}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise GitHubApiError(f"网络请求失败: {reason}") from exc


def discover_upstream(current_repo: str, token: str | None, timeout: float) -> dict[str, Any]:
    current = api_get(f"/repos/{quote(current_repo, safe='/')}", token, timeout)
    parent = current.get("parent")
    if not parent or not parent.get("full_name"):
        raise RuntimeError(f"{current_repo} 不是 GitHub fork，API 没有返回 parent/source 仓库")
    return parent


def list_forks(source_repo: str, token: str | None, timeout: float) -> list[dict[str, Any]]:
    forks: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api_get(
            f"/repos/{quote(source_repo, safe='/')}/forks?per_page=100&page={page}",
            token,
            timeout,
        )
        if not batch:
            return forks
        forks.extend(batch)
        if len(batch) < 100:
            return forks
        page += 1


def result_common(fork: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": fork.get("name", ""),
        "full_name": fork["full_name"],
        "html_url": fork.get("html_url", ""),
        "default_branch": fork.get("default_branch", ""),
        "archived": bool(fork.get("archived", False)),
        "updated_at": fork.get("updated_at") or "",
    }


def run_git(command: list[str], cwd: Path, timeout: float) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(f"Git 命令超时（{timeout:g}s）", command, str(exc)) from exc
    except OSError as exc:
        raise GitCommandError("无法执行 Git 命令", command, str(exc)) from exc

    if completed.returncode != 0:
        raise GitCommandError(f"Git 命令失败（exit={completed.returncode}）", command, completed.stdout)
    return completed.stdout.strip()


def remote_ref(remote: str, branch: str) -> str:
    return f"refs/remotes/{remote}/{branch}"


def merge_base(repo_dir: Path, source_ref: str, fork_ref: str, timeout: float) -> str | None:
    try:
        output = run_git(["git", "merge-base", source_ref, fork_ref], repo_dir, timeout)
    except GitCommandError:
        return None
    return output.splitlines()[0].strip() if output else None


def count_commits(repo_dir: Path, base_ref: str, head_ref: str, timeout: float) -> int:
    output = run_git(["git", "rev-list", "--count", f"{base_ref}..{head_ref}"], repo_dir, timeout)
    return int(output.strip())


def count_reachable_commits(repo_dir: Path, ref: str, timeout: float) -> int:
    output = run_git(["git", "rev-list", "--count", ref], repo_dir, timeout)
    return int(output.strip())


class LocalGitBatchComparator:
    """Reuse one blobless bare repository for a sequential fork batch."""

    def __init__(
        self,
        source: dict[str, Any],
        git_timeout: float,
        work_root: Path | None,
    ) -> None:
        self.source = source
        self.git_timeout = git_timeout
        self.source_branch = source["default_branch"]
        self.source_url = source.get("clone_url") or f"https://github.com/{source['full_name']}.git"
        self.source_ref = remote_ref("upstream", self.source_branch)
        self.temp_context: tempfile.TemporaryDirectory[str] | None = None
        if work_root:
            work_root.mkdir(parents=True, exist_ok=True)
            self.repo_dir = Path(tempfile.mkdtemp(prefix="fork-batch-", dir=work_root))
        else:
            self.temp_context = tempfile.TemporaryDirectory(prefix="upstream-fork-batch-")
            self.repo_dir = Path(self.temp_context.name)
        self._closed = False

        run_git(["git", "init", "--bare", "--quiet"], self.repo_dir, git_timeout)
        self._fetch_ref(self.source_url, self.source_branch, self.source_ref)

    def _fetch_ref(self, url: str, branch: str, ref: str) -> None:
        command = [
            "git",
            "fetch",
            "--no-tags",
            "--filter=blob:none",
            url,
            f"{branch}:{ref}",
        ]
        try:
            run_git(command, self.repo_dir, self.git_timeout)
        except GitCommandError as exc:
            # Some Git servers do not advertise partial clone filtering. Retry
            # without the filter before treating the repository as unavailable.
            if "filter" not in str(exc).lower() and "unsupported" not in str(exc).lower():
                raise
            run_git(
                ["git", "fetch", "--no-tags", url, f"{branch}:{ref}"],
                self.repo_dir,
                self.git_timeout,
            )

    def compare(self, fork: dict[str, Any]) -> ForkResult:
        common = result_common(fork)
        fork_branch = fork["default_branch"]
        fork_url = fork.get("clone_url") or f"https://github.com/{fork['full_name']}.git"
        ref_key = hashlib.sha1(fork["full_name"].encode("utf-8")).hexdigest()[:16]
        fork_ref = f"refs/forks/{ref_key}/{fork_branch}"
        try:
            self._fetch_ref(fork_url, fork_branch, fork_ref)
            base = merge_base(self.repo_dir, self.source_ref, fork_ref, self.git_timeout)
            if not base:
                source_reachable = count_reachable_commits(self.repo_dir, self.source_ref, self.git_timeout)
                fork_reachable = count_reachable_commits(self.repo_dir, fork_ref, self.git_timeout)
                return ForkResult(
                    **common,
                    ahead=None,
                    behind=None,
                    status="verified_no_common_ancestor",
                    comparison_source="local_git_cache",
                    source_reachable_commits=source_reachable,
                    fork_reachable_commits=fork_reachable,
                    error_type="no_common_ancestor",
                    error=(
                        "Git merge-base 未找到共同祖先；"
                        f"source 可达 commit={source_reachable}，fork 可达 commit={fork_reachable}"
                    ),
                )

            ahead = count_commits(self.repo_dir, self.source_ref, fork_ref, self.git_timeout)
            behind = count_commits(self.repo_dir, fork_ref, self.source_ref, self.git_timeout)
            if ahead == 0 and behind == 0:
                status = "identical"
            elif ahead > 0 and behind > 0:
                status = "diverged"
            elif ahead > 0:
                status = "ahead"
            else:
                status = "behind"
            return ForkResult(
                **common,
                ahead=ahead,
                behind=behind,
                status=status,
                comparison_source="local_git_cache",
                merge_base=base,
            )
        except GitCommandError as exc:
            return ForkResult(
                **common,
                ahead=None,
                behind=None,
                status="error",
                comparison_source="local_git_cache",
                error_type="fetch_failed" if "fetch" in " ".join(exc.command) else "git_failed",
                error=str(exc),
            )
        finally:
            # Remove the ref, while retaining the shared source objects and
            # letting Git reuse already downloaded fork objects during the run.
            try:
                run_git(["git", "update-ref", "-d", fork_ref], self.repo_dir, self.git_timeout)
            except GitCommandError:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.repo_dir.exists():
            shutil.rmtree(self.repo_dir, ignore_errors=True)
        if self.temp_context:
            self.temp_context.cleanup()


def api_compare(
    source: dict[str, Any], fork: dict[str, Any], token: str | None, timeout: float
) -> ForkResult:
    common = result_common(fork)
    source_branch = source["default_branch"]
    fork_branch = fork["default_branch"]
    basehead = f"{source_branch}...{fork['owner']['login']}:{fork_branch}"
    compare_path = f"/repos/{quote(source['full_name'], safe='/')}/compare/{quote(basehead, safe='.:')}"
    try:
        comparison = api_get(compare_path, token, timeout)
        ahead = int(comparison.get("ahead_by", 0))
        behind = int(comparison.get("behind_by", 0))
        return ForkResult(
            **common,
            ahead=ahead,
            behind=behind,
            status=str(comparison.get("status", "unknown")),
            comparison_source="github_rest",
            merge_base=(comparison.get("merge_base_commit") or {}).get("sha", ""),
        )
    except (GitHubApiError, ValueError) as exc:
        error_type = "repository_not_found" if "HTTP 404: Not Found" in str(exc) else "rest_compare_failed"
        return ForkResult(
            **common,
            ahead=None,
            behind=None,
            status="rest_compare_failed",
            comparison_source="github_rest",
            error_type=error_type,
            error=str(exc),
        )


def compare_with_auto(
    source: dict[str, Any],
    fork: dict[str, Any],
    token: str | None,
    timeout: float,
    local_comparator: LocalGitBatchComparator,
) -> ForkResult:
    api_result = api_compare(source, fork, token, timeout)
    if api_result.status not in {"rest_compare_failed"}:
        return api_result
    if api_result.error_type == "repository_not_found":
        return api_result
    return local_comparator.compare(fork)


def source_result(source: dict[str, Any]) -> ForkResult:
    return ForkResult(
        name=source.get("name", ""),
        full_name=source["full_name"],
        html_url=source.get("html_url", ""),
        default_branch=source.get("default_branch", ""),
        archived=bool(source.get("archived", False)),
        updated_at=source.get("updated_at") or "",
        ahead=0,
        behind=0,
        status="identical",
        comparison_source="source",
    )


def format_table(source_name: str, results: Iterable[ForkResult]) -> str:
    rows = [
        [
            result.full_name,
            str(result.behind) if result.behind is not None else "-",
            str(result.ahead) if result.ahead is not None else "-",
            result.status,
            result.comparison_source,
            result.default_branch,
            "yes" if result.archived else "no",
            result.error,
        ]
        for result in results
    ]
    headers = ["repository", "behind", "ahead", "status", "source", "branch", "archived", "error"]
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]

    lines = [f"upstream: {source_name}", ""]
    lines.append("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(value.ljust(width) for value, width in zip(row, widths)) for row in rows)
    return "\n".join(lines)


def format_csv(results: Iterable[ForkResult], stream: TextIOWrapper) -> None:
    fieldnames = list(ForkResult.__dataclass_fields__)
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(asdict(result) for result in results)


def write_output(
    source_name: str, results: list[ForkResult], output_format: str, output: Path | None
) -> None:
    if output_format == "table":
        content = format_table(source_name, results) + "\n"
        if output:
            output.write_text(content, encoding="utf-8")
        else:
            sys.stdout.write(content)
        return

    if output_format == "json":
        content = json.dumps(
            {"upstream": source_name, "forks": [asdict(result) for result in results]},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        if output:
            output.write_text(content, encoding="utf-8")
        else:
            sys.stdout.write(content)
        return

    if output:
        with output.open("w", encoding="utf-8", newline="") as stream:
            format_csv(results, stream)  # type: ignore[arg-type]
    else:
        format_csv(results, sys.stdout)  # type: ignore[arg-type]



def write_checkpoint(source_name: str, results: list[ForkResult], output_format: str, output: Path | None) -> None:
    if output and output_format == "csv":
        write_output(source_name, results, output_format, output)


def api_compare_one(
    source: dict[str, Any], fork: dict[str, Any], token: str | None, timeout: float
) -> ForkResult:
    return api_compare(source, fork, token, timeout)


def main() -> int:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    current_repo = args.repo or detect_current_repo()

    if args.workers < 1 or args.workers > 2:
        print("错误: --workers 只能是 1 或 2", file=sys.stderr)
        return 1

    try:
        current = api_get(f"/repos/{quote(current_repo, safe='/')}", token, args.timeout)
        parent = current.get("parent")
        if not parent or not parent.get("full_name"):
            raise RuntimeError(f"{current_repo} 不是 GitHub fork，API 没有返回 parent/source 仓库")
        source = parent
        source_name = source["full_name"]
        forks = list_forks(source_name, token, args.timeout)
        if args.only_fork:
            selected = set(args.only_fork)
            forks = [fork for fork in forks if fork.get("full_name") in selected]
            missing = selected - {fork.get("full_name") for fork in forks}
            if missing:
                raise RuntimeError(f"指定的 fork 不在 source fork 列表中: {', '.join(sorted(missing))}")

        results: list[ForkResult] = []
        local_comparator: LocalGitBatchComparator | None = None
        try:
            if args.compare_mode == "local":
                local_comparator = LocalGitBatchComparator(source, args.git_timeout, args.work_dir)
                for index, fork in enumerate(forks, start=1):
                    print(f"[{index}/{len(forks)}] {fork['full_name']}", file=sys.stderr)
                    results.append(local_comparator.compare(fork))
                    write_checkpoint(source_name, results, args.format, args.output)
            else:
                # API calls are the lightweight phase. Limit concurrency to two
                # workers to improve throughput without creating a request storm.
                api_results: dict[str, ForkResult] = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                    future_map = {
                        executor.submit(api_compare_one, source, fork, token, args.timeout): fork
                        for fork in forks
                    }
                    for index, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
                        fork = future_map[future]
                        try:
                            api_results[fork["full_name"]] = future.result()
                        except Exception as exc:  # keep one network failure from aborting the batch
                            api_results[fork["full_name"]] = ForkResult(
                                **result_common(fork),
                                ahead=None,
                                behind=None,
                                status="error",
                                comparison_source="github_rest",
                                error_type="api_worker_failed",
                                error=str(exc),
                            )
                        print(f"[API {index}/{len(forks)}] {fork['full_name']}", file=sys.stderr)
                        if args.compare_mode == "api":
                            write_checkpoint(source_name, list(api_results.values()), args.format, args.output)

                if args.compare_mode == "api":
                    results = [api_results[fork["full_name"]] for fork in forks]
                    write_checkpoint(source_name, results, args.format, args.output)
                else:
                    # Preserve source fork order and only use local Git for API
                    # compare failures that are not ordinary 404s.
                    for index, fork in enumerate(forks, start=1):
                        api_result = api_results[fork["full_name"]]
                        if api_result.status != "rest_compare_failed" or api_result.error_type == "repository_not_found":
                            results.append(api_result)
                        else:
                            if local_comparator is None:
                                local_comparator = LocalGitBatchComparator(source, args.git_timeout, args.work_dir)
                            results.append(local_comparator.compare(fork))
                        print(f"[结果 {index}/{len(forks)}] {fork['full_name']}", file=sys.stderr)
                        write_checkpoint(source_name, results, args.format, args.output)
        finally:
            if local_comparator:
                local_comparator.close()

        if args.include_source:
            results.insert(0, source_result(source))
        write_output(source_name, results, args.format, args.output)
    except (GitHubApiError, RuntimeError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
