"""打板接力：点「重新计算」用本地日线重算全量交易，结果由接口返回。

与涨停段 / 连涨段同一套模式，页面上没有预先导出的静态数据文件：

    POST /api/daban-research/rebuild   重算 → 内存 + data/daban_research/ 缓存
    GET  /api/daban-research/dataset  读缓存并组装成 JSON 返回 (没算过 → computed=false)
    GET  /api/daban-research/bars     按页按行号取 K 线窗口

缓存落在 data/** 下、不进 git；只在用户点按钮时重算，不会因为行情更新自动跑。
缓存拆成四份: meta.json (口径/基线/扫描表/标签定义)、stats.parquet (全量整数行)、
tags.parquet (每笔 TAG_WORDS 个标签位掩码 word)、ohlc.npy (K 线)。
标签用位掩码而不是逐标签布尔数组, 避免 dataset 又变成十几 MB 的静态 JSON。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from app.services.daban_compute import STATS_COLS, TAG_DEFS, TAG_WORDS, compute
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
    "tag_defs": [],
    "tag_words": TAG_WORDS,
    "tag_bits_per_word": 53,
}


def _load_tag_words(path: Path, n_rows: int) -> tuple[list[list[int]], bool]:
    """读标签缓存, 返回 (每行 TAG_WORDS 个 word, 是否读到了真实标签)。

    新格式是 tag_0..tag_{W-1} 多列。旧格式是单列 tag —— 改造前 53 条标签全在 word0,
    所以当且仅当当前只需要 1 个 word 时, 新旧位域完全等价, 直接兼容读, 不必重算。
    列数不够 (比如将来 W=2 却只读到 1 列) 一律当作没有标签、让页面提示重算,
    绝不拿 0 补齐 —— 那样高位标签会永久静默不命中, 比明确说「要重算」更糟。
    """
    if not path.is_file():
        return [[0] * TAG_WORDS for _ in range(n_rows)], False
    frame = pl.read_parquet(path)
    cols = sorted(
        (c for c in frame.columns if re.fullmatch(r"tag_\d+", c)),
        key=lambda c: int(c.rsplit("_", 1)[1]),
    )
    if len(cols) >= TAG_WORDS:
        words = [[int(v) for v in row] for row in frame.select(cols[:TAG_WORDS]).rows()]
    elif TAG_WORDS == 1 and "tag" in frame.columns:
        words = [[int(v)] for v in frame["tag"].to_list()]
    else:
        return [[0] * TAG_WORDS for _ in range(n_rows)], False
    if len(words) != n_rows:
        return [[0] * TAG_WORDS for _ in range(n_rows)], False
    return words, True


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
        tags_path = d / "tags.parquet"
        if not (meta_path.is_file() and stats_path.is_file() and ohlc_path.is_file()):
            return None, None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            frame = pl.read_parquet(stats_path)
            stats = [list(row) for row in frame.rows()]
            ohlc = np.load(ohlc_path, mmap_mode="r")
            # 旧缓存可能没有标签文件: 全 0 = 所有标签都不命中, 页面照常能开,
            # 点「重新计算」后才会生成真实标签。
            tags, has_tags = _load_tag_words(tags_path, len(stats))
            # 标签定义是**代码常量**, 不是数据: 缓存里的 tag_defs 只是上次重算时的快照。
            # 代码升级后必须覆盖, 否则前端会一直画旧标签集 —— 旧实现把 setdefault 写在
            # payload 顶层, 而前端读的是 payload["meta"], 层级本身就错, 覆盖从未生效:
            # 缓存 61 条会压住代码 71 条, 页面少画 10 个按钮, 而掩码里 bit61-70 明明有真值。
            core = meta.get("meta")
            if isinstance(core, dict):
                cached = core.get("tag_defs") or []
                cached_max = max((int(d.get("bit", -1)) for d in cached), default=-1)
                core["tag_defs"] = [dict(d) for d in TAG_DEFS] if has_tags else []
                core["tag_max_bit"] = max(int(d["bit"]) for d in TAG_DEFS)
                # 缓存若是按更小的标签集算的, 超出 cached_max 的位在掩码里恒为 0:
                # 那是「没算过」而不是「不命中」。前端必须把它当 0 笔看的同时明确提示,
                # 不能让用户以为这些标签真的一笔都没有。
                core["tag_stale"] = (
                    {
                        "cached_count": len(cached),
                        "code_count": len(TAG_DEFS),
                        "bits": [int(d["bit"]) for d in TAG_DEFS if int(d["bit"]) > cached_max],
                    }
                    if has_tags and cached and len(cached) != len(TAG_DEFS)
                    else None
                )
        except Exception:                                   # noqa: BLE001
            logger.exception("打板研究缓存读取失败，需要重新计算")
            return None, None
        return {**meta, "stats": stats, "tags": tags}, ohlc

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
        meta = {k: v for k, v in payload.items() if k not in ("stats", "tags")}
        atomic_write_text(d / "meta.json", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        frame = pl.DataFrame(payload["stats"], schema=STATS_COLS, orient="row")
        atomic_write_parquet(frame, d / "stats.parquet")
        # 每笔 TAG_WORDS 个 int64: 单个 int64 只能装 53 个位, 53 条标签正好用满,
        # 以后加标签就落到 word1, 不能再用单列 (也不该退回 int32/JSON number)。
        tag_arr = np.asarray(payload["tags"], dtype=np.int64)
        if tag_arr.ndim == 1:
            tag_arr = tag_arr[:, None]
        tag_frame = pl.DataFrame({f"tag_{i}": tag_arr[:, i] for i in range(tag_arr.shape[1])})
        atomic_write_parquet(tag_frame, d / "tags.parquet")
        tmp = d / "ohlc.npy.tmp"
        with open(tmp, "wb") as handle:
            np.save(handle, ohlc)
        os.replace(tmp, d / "ohlc.npy")

    def dataset(self) -> dict:
        """全量统计结果由接口返回。没算过时明确告诉前端去点「重新计算」。"""
        payload, _, computed = self._state()
        if not computed or payload is None:
            return {"computed": False, "meta": _EMPTY_META, "baseline": {}, "scan": {}, "stats": [], "tags": []}
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
