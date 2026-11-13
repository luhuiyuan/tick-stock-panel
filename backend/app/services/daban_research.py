"""打板接力：点「重新计算」用本地日线重算全量交易，结果由接口返回。

与涨停段 / 连涨段同一套模式，页面上没有预先导出的静态数据文件：

    POST /api/daban-research/rebuild   重算 → 内存 + data/daban_research/ 缓存
    GET  /api/daban-research/dataset  读缓存并组装成 JSON 返回 (没算过 → computed=false)
    GET  /api/daban-research/bars     按页按行号取 K 线窗口

缓存落在 data/** 下、不进 git；只在用户点按钮时重算，不会因为行情更新自动跑。
缓存拆成三份: meta.json (口径/基线/扫描表)、stats.parquet (12 万行整数)、ohlc.npy (K 线)，
和另外两个研究页的 parquet 缓存是同一种东西。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from app.services.daban_compute import STATS_COLS, compute
from app.services.fs_utils import atomic_write_parquet, atomic_write_text

logger = logging.getLogger(__name__)

# 内存优先、其次读盘；与 limit_up_research / rise_research 的状态机一致。
_STATE: dict = {"payload": None, "ohlc": None, "computed": False}
_CACHE_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()

# 还没点过「重新计算」时给前端的空壳，字段齐全，避免顶部面板读 undefined 崩溃。
_EMPTY_META = {
    "title": "打板接力研究",
    "n_all": 0,
    "start": "",
    "exit_modes": {},
    "phases": [], "phase_names": [],
    "states": [], "state_names": [],
    "pos_names": [], "pos_counts": [],
    "symbols": [], "name_list": [], "calendar": [],
    "win_before": 0, "win_after": 0,
    "stats_cols": STATS_COLS,
    "ohlc_n": 0,
    "stop_loss": 0,
    "caveats": [],
}


class DabanResearchService:
    def __init__(self, repo) -> None:
        self._repo = repo

    def _data_dir(self) -> Path | None:
        # 与 limit_up_research 一致: 数据目录挂在 repo.store 上
        store = getattr(self._repo, "store", None)
        data_dir = getattr(store, "data_dir", None)
        return None if data_dir is None else Path(data_dir)

    def _dir(self) -> Path | None:
        data_dir = self._data_dir()
        return None if data_dir is None else data_dir / "daban_research"

    def _read_saved(self) -> tuple[dict | None, np.ndarray | None]:
        d = self._dir()
        if d is None:
            return None, None
        meta_path = d / "meta.json"
        stats_path = d / "stats.parquet"
        ohlc_path = d / "ohlc.npy"
        if not (meta_path.is_file() and stats_path.is_file() and ohlc_path.is_file()):
            return None, None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            frame = pl.read_parquet(stats_path)
            stats = [list(row) for row in frame.rows()]
            ohlc = np.load(ohlc_path, mmap_mode="r")
        except Exception:                                   # noqa: BLE001
            logger.exception("打板研究缓存读取失败，需要重新计算")
            return None, None
        return {**meta, "stats": stats}, ohlc

    def _state(self) -> tuple[dict | None, np.ndarray | None, bool]:
        with _CACHE_LOCK:
            if _STATE["computed"]:
                return _STATE["payload"], _STATE["ohlc"], True
        payload, ohlc = self._read_saved()
        if payload is None:
            return None, None, False
        with _CACHE_LOCK:
            _STATE["payload"] = payload
            _STATE["ohlc"] = ohlc
            _STATE["computed"] = True
        return payload, ohlc, True

    def _write_saved(self, payload: dict, ohlc: np.ndarray) -> None:
        d = self._dir()
        if d is None:
            raise RuntimeError("本地数据目录没初始化")
        d.mkdir(parents=True, exist_ok=True)
        meta = {k: v for k, v in payload.items() if k != "stats"}
        atomic_write_text(d / "meta.json", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        frame = pl.DataFrame(payload["stats"], schema=STATS_COLS, orient="row")
        atomic_write_parquet(frame, d / "stats.parquet")
        tmp = d / "ohlc.npy.tmp"
        with open(tmp, "wb") as handle:
            np.save(handle, ohlc)
        os.replace(tmp, d / "ohlc.npy")

    def dataset(self) -> dict:
        """全量统计结果由接口返回。没算过时明确告诉前端去点「重新计算」。"""
        payload, _, computed = self._state()
        if not computed or payload is None:
            return {"computed": False, "meta": _EMPTY_META, "baseline": {}, "scan": {}, "stats": []}
        return {"computed": True, **payload}

    def bars(self, ids: list[int]) -> dict:
        """按行号取 K 线窗口。越界或没算过时如实报错，不返回假数据。"""
        _, arr, computed = self._state()
        if not computed or arr is None:
            return {"ok": False, "reason": "missing", "rows": [], "n": 0, "total": 0}
        total = int(arr.shape[0])
        width = int(arr.shape[1]) if arr.ndim == 2 else 0
        out: list[list[int]] = []
        for i in ids:
            if i < 0 or i >= total:
                out.append([-1] * width)
                continue
            out.append([int(v) for v in arr[i]])
        return {"ok": True, "reason": "", "rows": out, "n": width, "total": total}

    def rebuild(self) -> dict:
        """按当前本地日线重算全部板位，写缓存并刷新内存。并发点击由锁串行化。"""
        with _BUILD_LOCK:
            data_dir = self._data_dir()
            if data_dir is None:
                raise RuntimeError("本地数据目录没初始化")
            # daban_lab 的行情路径是 <root>/data/...，所以 root 是 data_dir 的上一级。
            payload, ohlc = compute(str(data_dir.parent))
            self._write_saved(payload, ohlc)
            with _CACHE_LOCK:
                _STATE["payload"] = payload
                _STATE["ohlc"] = ohlc
                _STATE["computed"] = True
            return {
                "ok": True,
                "total": len(payload["stats"]),
                "as_of": date.today().isoformat(),
                "computed": True,
            }
