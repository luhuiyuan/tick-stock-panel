#!/usr/bin/env python3
"""Identify conservative feature-update groups from fork-unique commit CSVs.

The input CSVs are produced by analyze_specified_fork.py or the batch fork
analysis. This script is intentionally heuristic: it groups nearby commits
only when their subjects/scopes and changed paths provide enough overlap, and
keeps ambiguous commits separate with lower confidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

csv.field_size_limit(2**31 - 1)

ANALYSIS_VERSION = "fork-feature-heuristic/v1"

TYPE_MAP = {
    "feat": "feature",
    "feature": "feature",
    "fix": "bugfix",
    "bug": "bugfix",
    "refactor": "refactor",
    "perf": "performance",
    "performance": "performance",
    "data": "data",
    "docs": "docs",
    "doc": "docs",
    "test": "test",
    "tests": "test",
    "chore": "chore",
    "build": "dependency",
    "ci": "dependency",
    "style": "refactor",
    "research": "research",
    "revert": "other",
}

GENERIC_TOKENS = {
    "add", "added", "change", "changes", "chore", "cleanup", "create", "created",
    "docs", "fix", "fixed", "improve", "improved", "new", "refactor", "remove",
    "support", "test", "tests", "update", "updated", "修复", "优化", "新增", "补充",
    "调整", "同步", "完善", "改进", "处理", "支持", "增加", "移除", "重构", "统一",
}

DOMAIN_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("data-provider", ("data_providers", "provider", "stocksdk", "efinance", "akshare", "tdxquant"), ("数据源", "provider", "stocksdk", "efinance", "akshare", "tdxquant")),
    ("fund-flow", ("fund_flow", "fund-flow", "资金流", "两融", "融资融券", "southbound", "northbound"), ("资金流", "两融", "ETF份额", "fund flow")),
    ("strategy", ("strategy", "screener", "custom_signals", "signal", "选股", "策略", "信号"), ("策略", "选股", "信号", "overlay_filter", "gene")),
    ("backtest", ("backtest", "回测", "portfolio", "paper", "模拟盘"), ("回测", "模拟盘", "paper account", "paper")),
    ("market-data", ("kline", "quote", "market", "行情", "分钟", "daily", "intraday"), ("行情", "分钟", "日K", "盘口", "quote", "kline")),
    ("adjustment", ("adj", "复权", "除权"), ("复权", "除权", "adj")),
    ("frontend", ("frontend", "src/", "ui", "page", "dashboard", "dialog", "component", "页面", "界面"), ("页面", "界面", "UI", "前端", "dashboard")),
    ("deployment", ("docker", "compose", "deploy", "Dockerfile", "workflow", ".github", "部署", "容器"), ("部署", "Docker", "容器", "CI", "workflow")),
    ("assistant-ai", ("assistant", "ai_", "ai/", "AI", "大模型", "智能助手"), ("AI", "助手", "模型", "对话")),
    ("documentation", ("README", "docs/", ".md"), ("文档", "README", "指南", "截图")),
]

FEATURE_FIELDS = [
    "feature_id", "fork_full_name", "fork_rank", "feature_title", "feature_summary",
    "feature_type", "domain", "subdomain", "status", "commit_count",
    "first_commit_date", "last_commit_date", "total_additions", "total_deletions",
    "changed_file_count", "primary_paths", "identification_confidence",
    "identification_basis", "migration_candidate", "migration_notes", "risk_notes",
    "source_csv", "analysis_version", "generated_at",
]

COMMIT_FIELDS = [
    "feature_id", "fork_full_name", "commit_id", "short_commit_id", "subject", "message",
    "author_name", "author_email", "committer_date", "is_merge", "files_changed",
    "additions", "deletions", "file_changes", "commit_role", "commit_order",
    "commit_relevance", "raw_source_file",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Identify feature updates from fork-unique commit CSVs")
    p.add_argument("--input-dir", type=Path, default=Path("analysis/fork-analysis/unique-commits"))
    p.add_argument("--output-dir", type=Path, default=Path("analysis/fork-analysis/feature-updates"))
    p.add_argument("--ranking", type=Path, default=Path("analysis/fork-analysis/fork_change_ranking.csv"))
    p.add_argument("--limit", type=int, default=15, help="Only process the first N ranked forks")
    return p.parse_args()


def parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def commit_kind(subject: str) -> tuple[str, str]:
    match = re.match(r"^\s*([A-Za-z]+)(?:\(([^)]+)\))?\s*:\s*", subject)
    if not match:
        return "other", ""
    raw = match.group(1).lower()
    return TYPE_MAP.get(raw, "other"), (match.group(2) or "").lower()


def parse_file_changes(value: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def path_set(row: dict[str, str]) -> set[str]:
    paths = set()
    for item in parse_file_changes(row.get("file_changes", "")):
        path = str(item.get("path", "")).replace("\\", "/")
        if path:
            paths.add(path)
    return paths


def path_groups(paths: set[str]) -> set[str]:
    groups = set()
    for path in paths:
        parts = path.split("/")
        groups.add(parts[0])
        if len(parts) > 1:
            groups.add("/".join(parts[:2]))
    return groups


def text_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text):
        lowered = token.lower()
        if lowered not in GENERIC_TOKENS and len(lowered) >= 2:
            tokens.add(lowered)
    return tokens


def classify(row: dict[str, str]) -> dict[str, Any]:
    subject = row.get("subject", "")
    message = row.get("message", "")
    paths = path_set(row)
    path_text = " ".join(paths)
    combined = f"{subject}\n{message}\n{path_text}"
    kind, scope = commit_kind(subject)

    domain = "other"
    domain_score = 0
    for candidate, path_keys, text_keys in DOMAIN_RULES:
        score = sum(2 for key in path_keys if key.lower() in path_text.lower())
        score += sum(1 for key in text_keys if key.lower() in combined.lower())
        if score > domain_score:
            domain, domain_score = candidate, score

    tokens = text_tokens(subject)
    if scope and scope not in GENERIC_TOKENS:
        tokens.add(scope.lower())
    path_groups_value = path_groups(paths)
    clear_subject = bool(re.match(r"^\s*[A-Za-z]+(?:\([^)]*\))?\s*:\s*\S+", subject))
    vague = subject.strip().lower() in {"update", "优化", "完善", "修复", "chore", ""}
    if vague:
        confidence = "low"
        basis = "paths" if paths else "message"
    elif clear_subject and domain_score >= 2:
        confidence = "high"
        basis = "message+paths"
    elif clear_subject or domain_score >= 1:
        confidence = "medium"
        basis = "message+paths" if paths else "message"
    else:
        confidence = "low"
        basis = "message"

    subdomain = scope or (sorted(path_groups_value)[0] if path_groups_value else "")
    status = {
        "feature": "new",
        "bugfix": "bugfix",
        "refactor": "compatibility",
        "performance": "optimization",
        "data": "enhancement",
        "research": "research",
        "docs": "documentation",
        "test": "test-support",
        "dependency": "maintenance",
    }.get(kind, "unknown")
    migration = "yes" if kind in {"feature", "bugfix", "performance", "data", "refactor"} else ("no" if kind in {"docs", "test", "chore", "dependency"} else "review")
    return {
        "kind": kind,
        "scope": scope,
        "domain": domain,
        "subdomain": subdomain,
        "tokens": tokens,
        "path_groups": path_groups_value,
        "paths": paths,
        "confidence": confidence,
        "basis": basis,
        "status": status,
        "migration": migration,
    }


def numeric(row: dict[str, str], key: str) -> int:
    try:
        return int(row.get(key, "0") or 0)
    except ValueError:
        return 0


def can_join(current: dict[str, Any], group: dict[str, Any]) -> bool:
    if current["domain"] != group["domain"]:
        return False
    if current["date"] - group["last_date"] > timedelta(days=45):
        return False
    token_overlap = current["tokens"] & group["tokens"]
    path_overlap = current["path_groups"] & group["path_groups"]
    scope_overlap = current["scope"] and current["scope"] == group["scope"]
    if scope_overlap and path_overlap:
        return True
    if len(token_overlap) >= 2 and path_overlap:
        return True
    return len(token_overlap) >= 1 and len(path_overlap) >= 2


def load_ranks(path: Path, limit: int) -> dict[str, int]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    return {r["fork_full_name"]: int(r["rank"]) for r in rows[:limit] if r.get("fork_full_name")}


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit 必须为正整数")
    rank_map = load_ranks(args.ranking, args.limit)
    inputs = sorted(args.input_dir.glob("*_fork_unique_commits.csv"))
    if rank_map:
        inputs = [p for p in inputs if p.name.split("_fork_unique_commits.csv", 1)[0].replace("__", "/") in rank_map]
    generated_at = datetime.now(timezone.utc).isoformat()
    feature_rows: list[dict[str, str]] = []
    commit_rows: list[dict[str, str]] = []
    feature_number = 1

    for input_path in inputs:
        with input_path.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            continue
        fork_name = rows[0].get("repository", "")
        rank = rank_map.get(fork_name, "")
        enriched = []
        for row in rows:
            info = classify(row)
            enriched.append({"row": row, **info, "date": parse_date(row.get("committer_date", ""))})
        enriched.sort(key=lambda x: x["date"])

        groups: list[dict[str, Any]] = []
        for item in enriched:
            matches = [g for g in groups if can_join(item, g)]
            if matches:
                group = max(matches, key=lambda g: g["last_date"])
                group["items"].append(item)
                group["last_date"] = max(group["last_date"], item["date"])
                group["tokens"] |= item["tokens"]
                group["path_groups"] |= item["path_groups"]
                group["paths"] |= item["paths"]
                group["kinds"].append(item["kind"])
                group["confidences"].append(item["confidence"])
            else:
                groups.append({
                    "items": [item], "domain": item["domain"], "scope": item["scope"],
                    "last_date": item["date"], "tokens": set(item["tokens"]),
                    "path_groups": set(item["path_groups"]), "paths": set(item["paths"]),
                    "kinds": [item["kind"]], "confidences": [item["confidence"]],
                })

        for group in sorted(groups, key=lambda g: g["items"][0]["date"]):
            items = group["items"]
            feature_id = f"FU-{feature_number:06d}"
            feature_number += 1
            first = items[0]["row"]
            subjects = [i["row"].get("subject", "").strip() for i in items]
            kinds = Counter(group["kinds"])
            dominant_kind = kinds.most_common(1)[0][0]
            confidence_values = {"low": 0, "medium": 1, "high": 2}
            confidence = min(group["confidences"], key=lambda x: confidence_values.get(x, 0))
            if len(items) > 1 and confidence == "high":
                confidence = "medium"
            title = subjects[0] or "未命名功能更新"
            if len(items) > 1:
                title = f"{title}（关联 {len(items)} 个提交）"
            primary_paths = sorted(group["paths"])
            summary = (
                f"关联 {len(items)} 个提交，主要涉及 {group['domain']}"
                + (f"/{group['scope']}" if group["scope"] else "")
                + f"；提交主题包括：{'；'.join(subjects[:3])}"
            )
            migration = "yes" if any(i["migration"] == "yes" for i in items) else ("review" if any(i["migration"] == "review" for i in items) else "no")
            feature_rows.append({
                "feature_id": feature_id,
                "fork_full_name": fork_name,
                "fork_rank": str(rank),
                "feature_title": title,
                "feature_summary": summary,
                "feature_type": dominant_kind,
                "domain": group["domain"],
                "subdomain": group["scope"] or (primary_paths[0] if primary_paths else ""),
                "status": "bugfix" if dominant_kind == "bugfix" else ("new" if dominant_kind == "feature" else "enhancement"),
                "commit_count": str(len(items)),
                "first_commit_date": items[0]["row"].get("committer_date", ""),
                "last_commit_date": items[-1]["row"].get("committer_date", ""),
                "total_additions": str(sum(numeric(i["row"], "additions") for i in items)),
                "total_deletions": str(sum(numeric(i["row"], "deletions") for i in items)),
                "changed_file_count": str(len(group["paths"])),
                "primary_paths": ";".join(primary_paths),
                "identification_confidence": confidence,
                "identification_basis": "message+paths",
                "migration_candidate": migration,
                "migration_notes": "同一功能的后续修复/测试已合并；迁移前需复核原始 diff。" if len(items) > 1 else "建议结合原始 diff 复核功能边界。",
                "risk_notes": "包含多个提交或跨模块变更时需人工复核。" if len(items) > 1 or len(primary_paths) > 5 else "",
                "source_csv": input_path.name,
                "analysis_version": ANALYSIS_VERSION,
                "generated_at": generated_at,
            })
            for index, item in enumerate(items, 1):
                row = item["row"]
                kind = item["kind"]
                role = "initial" if index == 1 else ("followup-fix" if kind == "bugfix" else ("test" if kind == "test" else ("docs" if kind == "docs" else "supporting")))
                commit_rows.append({
                    "feature_id": feature_id,
                    "fork_full_name": fork_name,
                    "commit_id": row.get("commit_id", ""),
                    "short_commit_id": row.get("commit_id", "")[:12],
                    "subject": row.get("subject", ""),
                    "message": row.get("message", ""),
                    "author_name": row.get("author_name", ""),
                    "author_email": row.get("author_email", ""),
                    "committer_date": row.get("committer_date", ""),
                    "is_merge": row.get("is_merge", ""),
                    "files_changed": row.get("files_changed", ""),
                    "additions": row.get("additions", ""),
                    "deletions": row.get("deletions", ""),
                    "file_changes": row.get("file_changes", "[]"),
                    "commit_role": role,
                    "commit_order": str(index),
                    "commit_relevance": "primary" if index == 1 else "supporting",
                    "raw_source_file": input_path.name,
                })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "feature_updates.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FEATURE_FIELDS)
        writer.writeheader()
        writer.writerows(feature_rows)
    with (args.output_dir / "feature_commits.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=COMMIT_FIELDS)
        writer.writeheader()
        writer.writerows(commit_rows)
    print(json.dumps({"features": len(feature_rows), "commits": len(commit_rows), "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
