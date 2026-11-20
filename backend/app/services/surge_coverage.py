"""Resumable daily-signal coverage jobs and version-bound evidence reads."""

import json
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
import pyarrow.parquet as pq

from app.services.coverage_replay import (
    Panel,
    UnavailableError,
    digest,
    fingerprint,
    input_stamp,
    scoped,
)
from app.services.fs_utils import atomic_write_text
from app.services.pipeline_jobs import JobCancelledError, JobStore, is_cancelled, run_with_capacity
from app.strategy.custom_signals import load_all

SIGNAL = "t_shrink_pullback"


class Coverage:
    def __init__(self, root, folder=None):
        self.root = root
        self.folder = folder = folder or root / "surge_research/coverage"
        self.path = folder / "shrink-v1.parquet"
        self.store = JobStore(store_dir=folder / "jobs")

    def catalog(self):
        return {
            s["id"]: s
            for s in load_all(self.root)
            if s.get("enabled")
            and s.get("timeframe") == "daily"
            and re.fullmatch(r"[A-Za-z0-9_-]+", s.get("id", ""))
        }

    def artifact(self, sid):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            raise ValueError("无效信号 ID")
        return self.folder / sid / "result-v1.parquet"

    def entry(self, sig, stamp):
        stamp = scoped(stamp, sig)
        path = self.artifact(sig["id"])
        status = "pending"
        reason = "尚未计算"
        result = None
        if path.exists():
            result = json.loads(pq.read_metadata(path).metadata[b"coverage"])
            if result["signal_id"] != sig["id"]:
                raise ValueError("结果信号 ID 不一致")
            verified_stamp = result["stamp"]
            proof = path.with_name("verified-inputs.json")
            if proof.exists():
                evidence = json.loads(proof.read_text())
                if (
                    evidence["version"] == result["version"]
                    and evidence["legacy_stamp"] == verified_stamp
                    and evidence["artifact"] == fingerprint(path.stat())
                ):
                    verified_stamp = evidence["scoped_stamp"]
            result["stale"] = verified_stamp != stamp or result["signal_hash"] != digest(sig)
            status = "stale" if result["stale"] else "ready"
            reason = "输入已更新" if result["stale"] else ""
        elif sig["id"] == SIGNAL and self.path.exists():
            result = json.loads(pq.read_metadata(self.path).metadata[b"coverage"])
            result.update(signal_id=SIGNAL, name=sig["name"], stale=True)
            status = "stale"
            reason = "首版结果保留，待按统一版本重算"
        last = path.with_name("attempt.json")
        try:
            attempt = json.loads(last.read_text())
        except FileNotFoundError:
            pass
        else:
            if (
                attempt["stamp"] == stamp
                and attempt["signal_hash"] == digest(sig)
                and (not result or result["version"] != attempt["version"])
            ):
                status = attempt["status"]
                reason = attempt["reason"]
        return dict(
            signal_id=sig["id"], name=sig["name"], status=status, reason=reason, result=result
        )

    def listing(self):
        stamp = input_stamp(self.root)
        return [self.entry(s, stamp) for s in self.catalog().values()]

    def start(self, signal_id=None):
        if signal_id is not None and signal_id not in self.catalog():
            raise ValueError("未知或未启用的日线信号")
        job, new = self.store.create(timeout_s=1740)
        if new:
            threading.Thread(target=self._run, args=(job, signal_id), daemon=True).start()
        return {"job_id": job, "created": new}

    def _run(self, job, sid):
        deadline = time.monotonic() + 1740

        def checkpoint():
            if time.monotonic() >= deadline or is_cancelled(job):
                raise TimeoutError("任务超时或取消，可继续未完成项")

        try:
            result = run_with_capacity(
                job, lambda: self.batch(job, sid, checkpoint), store=self.store, deadline=deadline
            )
            self.store.succeed(job, result)
        except (Exception, JobCancelledError) as exc:
            logging.getLogger(__name__).exception("coverage job %s failed", job)
            self.store.fail(job, str(exc) or "任务未完成")

    def batch(self, job, sid, checkpoint):
        sigs = self.catalog()
        stamp = input_stamp(self.root)
        todo = (
            [sigs[sid]]
            if sid
            else [
                s
                for s in sigs.values()
                if self.entry(s, stamp)["status"] not in ("ready", "unavailable")
            ]
        )
        counts = Counter()
        if not todo:
            return {"processed": 0}
        with TemporaryDirectory(prefix="coverage-") as stage:
            panel = Panel(self.root, Path(stage), checkpoint)
            if panel.stamp != stamp:
                raise ValueError("准备期间输入已变更")
            for i, s in enumerate(todo):
                checkpoint()
                k = s["id"]
                version = f"{job}-{k}"
                path = self.artifact(k)
                self.store.progress(
                    job, "coverage", 100 * i // len(todo), f"{i + 1}/{len(todo)} · {s['name']}"
                )
                try:
                    panel.evaluate(s, path, version, checkpoint)
                    counts["ready"] += 1
                except TimeoutError:
                    raise
                except Exception as exc:
                    panel.verify()
                    status = "unavailable" if isinstance(exc, UnavailableError) else "failed"
                    counts[status] += 1
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(
                        path.with_name("attempt.json"),
                        json.dumps(
                            dict(
                                stamp=scoped(stamp, s),
                                signal_hash=digest(s),
                                version=version,
                                status=status,
                                reason=str(exc),
                            ),
                            ensure_ascii=False,
                        ),
                    )
                    if status == "failed":
                        logging.getLogger(__name__).exception("coverage %s failed", k)
                else:
                    path.with_name("attempt.json").unlink(missing_ok=True)
        return dict(processed=len(todo), **counts)

    def read(self, category=None, offset=0, version=None, *, charts=None, signal_id=SIGNAL):
        try:
            if signal_id not in self.catalog():
                raise ValueError("未知或未启用的日线信号")
            path = self.artifact(signal_id)
            if signal_id == SIGNAL and (
                not path.exists()
                or (
                    version
                    and self.path.exists()
                    and version
                    == json.loads(pq.read_metadata(self.path).metadata[b"coverage"])["version"]
                )
            ):
                path = self.path
            handle = path.open("rb")
        except FileNotFoundError:
            if category:
                raise ValueError("尚无此信号的结果") from None
            return None, []
        with handle:
            pf = pq.ParquetFile(handle)
            summary = json.loads(pf.metadata.metadata[b"coverage"])
            if summary.get("signal_id", SIGNAL) != signal_id:
                raise ValueError("结果信号 ID 不一致")
            summary["stale"] = (
                not (self.root / "surge_research/runs-v1.parquet").exists()
                or fingerprint((self.root / "surge_research/runs-v1.parquet").stat())
                != summary["source"]
            )
            if category is None:
                return summary, []
            if version != summary["version"]:
                raise ValueError("结果版本已更新，请刷新榜单")
            rows = (
                pl.from_arrow(pf.read(use_threads=False))
                .filter(pl.col("category") == category)
                .slice(offset, 24)
            )
            records = rows.to_dicts()
            if charts is not None and records:
                bars = charts.page_bars(rows)
                for record in records:
                    record["bars"] = bars.get(record["id"], [])
            return summary, records


_lock = threading.Lock()
_services: dict[Path, Coverage] = {}


def get_coverage(root):
    with _lock:
        if root not in _services:
            _services[root] = Coverage(root)
        return _services[root]
