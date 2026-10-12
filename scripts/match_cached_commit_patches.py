#!/usr/bin/env python3
"""Match source and fork commit caches by SHA first, then stable patch-id.

This intentionally does not require a common ancestor.  A patch-id match means
Git considers the normalized diff equivalent; it does not prove identical
history or identical feature intent.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_AUTHOR_NAMES = {"shy3130", "wshy"}
SOURCE_AUTHOR_EMAILS = {"415333856@qq.com", "wangshuai443130@gmail.com"}

# file_changes is JSON embedded in one CSV cell and can exceed Python's
# default 131072-byte CSV field limit for large commits.
csv.field_size_limit(sys.maxsize)

OUTPUT_FIELDS = [
    "side", "repository", "branch", "commit_id", "match_type", "matched_commit_id",
    "patch_id", "patch_status", "is_merge", "author_name", "author_email",
    "author_date", "committer_name", "committer_email", "committer_date",
    "subject", "message", "files_changed", "additions", "deletions",
    "binary_files_changed", "file_changes", "error",
]


def args_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Match cached source/fork commits by SHA and patch-id")
    p.add_argument("--source-csv", type=Path, required=True)
    p.add_argument("--fork-csv", type=Path, required=True)
    p.add_argument("--output-prefix", type=Path, required=True, help="Output path without extension")
    return p


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def as_bool(value: str) -> bool:
    return value.lower() == "true"


def row_key(row: dict[str, str]) -> str:
    return row.get("commit_id", "")


def is_source_author(row: dict[str, str]) -> bool:
    return (
        row.get("author_name", "") in SOURCE_AUTHOR_NAMES
        or row.get("author_email", "") in SOURCE_AUTHOR_EMAILS
    )


def classify_unmatched(row: dict[str, str], side: str) -> str:
    status = row.get("patch_status", "")
    if as_bool(row.get("is_merge", "")) or status == "merge_skipped":
        return "merge_skipped"
    if status == "empty":
        return "empty_patch"
    if status == "error":
        return "patch_error"
    if side == "fork" and is_source_author(row):
        return "fork_source_author_commit"
    return "fork_unique_patch" if side == "fork" else "source_unique_patch"


def commit_date_key(row: dict[str, str]) -> datetime:
    value = row.get("committer_date", "")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def output_row(side: str, row: dict[str, str], match_type: str, matched_id: str = "") -> dict[str, str]:
    return {
        "side": side,
        "repository": row.get("repository", ""),
        "branch": row.get("branch", ""),
        "commit_id": row.get("commit_id", ""),
        "match_type": match_type,
        "matched_commit_id": matched_id,
        "patch_id": row.get("patch_id", ""),
        "patch_status": row.get("patch_status", ""),
        "is_merge": row.get("is_merge", ""),
        "author_name": row.get("author_name", ""),
        "author_email": row.get("author_email", ""),
        "author_date": row.get("author_date", ""),
        "committer_name": row.get("committer_name", ""),
        "committer_email": row.get("committer_email", ""),
        "committer_date": row.get("committer_date", ""),
        "subject": row.get("subject", ""),
        "message": row.get("message", ""),
        "files_changed": row.get("files_changed", ""),
        "additions": row.get("additions", ""),
        "deletions": row.get("deletions", ""),
        "binary_files_changed": row.get("binary_files_changed", ""),
        "file_changes": row.get("file_changes", ""),
        "error": row.get("error", ""),
    }


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def int_value(row: dict[str, str], field: str) -> int:
    try:
        return int(row.get(field, "") or 0)
    except ValueError:
        return 0


def main() -> int:
    args = args_parser().parse_args()
    try:
        source = load(args.source_csv)
        fork = load(args.fork_csv)
    except (OSError, csv.Error) as exc:
        print(f"错误: 无法读取缓存: {exc}", file=sys.stderr)
        return 1
    if not source or not fork:
        print("错误: source/fork 缓存不能为空", file=sys.stderr)
        return 1

    source_by_id = {row_key(r): r for r in source}
    fork_by_id = {row_key(r): r for r in fork}
    matched_source: dict[str, tuple[str, str]] = {}
    matched_fork: dict[str, tuple[str, str]] = {}

    # Exact SHA matching has priority, including exact matching merge commits.
    for commit_id in sorted(set(source_by_id) & set(fork_by_id)):
        matched_source[commit_id] = (commit_id, "same_commit")
        matched_fork[commit_id] = (commit_id, "same_commit")

    # Patch IDs can collide/repeat, so retain queues and consume one-to-one.
    source_patch_queues: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source:
        commit_id = row_key(row)
        if commit_id in matched_source:
            continue
        if row.get("patch_status") == "ok" and row.get("patch_id"):
            source_patch_queues[row["patch_id"]].append(row)

    for fork_row in fork:
        fork_id = row_key(fork_row)
        if fork_id in matched_fork:
            continue
        patch_id = fork_row.get("patch_id", "")
        if fork_row.get("patch_status") != "ok" or not patch_id:
            continue
        candidates = source_patch_queues.get(patch_id, [])
        if not candidates:
            continue
        source_row = candidates.pop(0)
        source_id = row_key(source_row)
        matched_source[source_id] = (fork_id, "equivalent_patch")
        matched_fork[fork_id] = (source_id, "equivalent_patch")

    details: list[dict[str, str]] = []
    for row in source:
        commit_id = row_key(row)
        if commit_id in matched_source:
            matched_id, kind = matched_source[commit_id]
            details.append(output_row("source", row, kind, matched_id))
        else:
            details.append(output_row("source", row, classify_unmatched(row, "source")))
    for row in fork:
        commit_id = row_key(row)
        if commit_id in matched_fork:
            matched_id, kind = matched_fork[commit_id]
            details.append(output_row("fork", row, kind, matched_id))
        else:
            details.append(output_row("fork", row, classify_unmatched(row, "fork")))

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(args.source_csv),
        "fork_csv": str(args.fork_csv),
        "source_repository": source[0].get("repository", ""),
        "fork_repository": fork[0].get("repository", ""),
        "source_branch": source[0].get("branch", ""),
        "fork_branch": fork[0].get("branch", ""),
        "source_commit_count": len(source),
        "fork_commit_count": len(fork),
        "same_commit_count": sum(1 for kind in matched_source.values() if kind[1] == "same_commit"),
        "equivalent_patch_count": sum(1 for kind in matched_source.values() if kind[1] == "equivalent_patch"),
        "source_unique_patch_count": sum(1 for r in source if row_key(r) not in matched_source and classify_unmatched(r, "source") == "source_unique_patch"),
        "fork_unique_patch_count": sum(1 for r in fork if row_key(r) not in matched_fork and classify_unmatched(r, "fork") == "fork_unique_patch"),
        "fork_source_author_commit_count": sum(1 for r in fork if row_key(r) not in matched_fork and classify_unmatched(r, "fork") == "fork_source_author_commit"),
        "source_merge_skipped_count": sum(1 for r in source if row_key(r) not in matched_source and classify_unmatched(r, "source") == "merge_skipped"),
        "fork_merge_skipped_count": sum(1 for r in fork if row_key(r) not in matched_fork and classify_unmatched(r, "fork") == "merge_skipped"),
        "source_additions_unmatched": sum(int_value(r, "additions") for r in source if row_key(r) not in matched_source and classify_unmatched(r, "source") == "source_unique_patch"),
        "source_deletions_unmatched": sum(int_value(r, "deletions") for r in source if row_key(r) not in matched_source and classify_unmatched(r, "source") == "source_unique_patch"),
        "fork_additions_unmatched": sum(int_value(r, "additions") for r in fork if row_key(r) not in matched_fork and classify_unmatched(r, "fork") == "fork_unique_patch"),
        "fork_deletions_unmatched": sum(int_value(r, "deletions") for r in fork if row_key(r) not in matched_fork and classify_unmatched(r, "fork") == "fork_unique_patch"),
        "notes": [
            "same_commit means identical commit SHA.",
            "equivalent_patch means stable patch-id matched after exact SHA matching.",
            "Merge commits are retained but patch-id matching is skipped.",
            "Unmatched additions/deletions are sums of cached per-commit stats, not net tree differences.",
            "Fork commits matching the configured shy3130 identity aliases (shy3130/wshy and the two known emails) are excluded from fork_unique_patch.",
        ],
    }
    prefix = args.output_prefix
    unique_rows = [
        row for row in details
        if row["side"] == "fork" and row["match_type"] == "fork_unique_patch"
    ]
    unique_rows.sort(key=commit_date_key, reverse=True)
    unique_output = prefix.with_name(prefix.name + "_fork_unique_commits.csv")
    summary["fork_unique_commits_count"] = len(unique_rows)
    summary["fork_unique_commits_csv"] = str(unique_output)

    write_csv(prefix.with_name(prefix.name + "_commit_matches.csv"), details, OUTPUT_FIELDS)
    write_csv(unique_output, unique_rows, OUTPUT_FIELDS)
    summary_csv_row = summary_to_csv(summary)
    write_csv(prefix.with_name(prefix.name + "_summary.csv"), [summary_csv_row], list(summary_csv_row.keys()))
    prefix.with_name(prefix.name + "_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def summary_to_csv(summary: dict[str, Any]) -> dict[str, str]:
    return {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value) for key, value in summary.items()}


if __name__ == "__main__":
    raise SystemExit(main())
