"""打板接力：提供全量统计快照，并按行号读取全量 K 线窗口。

数据由离线脚本 `strategy_lab/export_daban.py` 生成，统一放在 data/ 下、不进 git：

    data/daban_research/daban_data.json   meta / baseline / scan / stats (约 11MB)
    data/daban_research/daban_ohlc.npy   形状 (N, 17*4), int32, 单位「分」

行号与 daban_data.json 里 stats 的行号严格一一对应（导出时断言校验过）。
本模块只负责把行号映射成价格窗口，**不重算任何口径**，避免和后端另写一套算法
算出两个不一样的数。

为什么 K 线不塞进 JSON：12 万笔全量 K 线约 40MB，塞给浏览器没法用；改成按页取，
一页 24 笔只有约 10KB。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 一个进程只 mmap 一次；文件很大，用 mmap_mode="r" 让页按需读入，不占常驻内存。
_LOCK = threading.Lock()
_CACHE: dict[str, np.ndarray] = {}


class DabanResearchService:
    def __init__(self, repo) -> None:
        self._store = repo

    @property
    def _dir(self) -> Path | None:
        # 与 limit_up_research 一致: 数据目录挂在 repo.store 上
        store = getattr(self._store, "store", None)
        data_dir = getattr(store, "data_dir", None)
        if data_dir is None:
            return None
        return Path(data_dir) / "daban_research"

    @property
    def _path(self) -> Path | None:
        d = self._dir
        return None if d is None else d / "daban_ohlc.npy"

    def dataset_path(self) -> Path | None:
        """全量统计快照。由后端 FileResponse 提供，不再放 frontend/public 进 git。"""
        d = self._dir
        return None if d is None else d / "daban_data.json"

    def _matrix(self) -> np.ndarray | None:
        path = self._path
        if path is None or not path.exists():
            return None
        key = str(path)
        with _LOCK:
            arr = _CACHE.get(key)
            if arr is None:
                try:
                    arr = np.load(path, mmap_mode="r")
                except Exception:                       # noqa: BLE001
                    logger.exception("打板 K 线文件读取失败: %s", path)
                    return None
                _CACHE[key] = arr
            return arr

    def bars(self, ids: list[int]) -> dict:
        """按行号取 K 线窗口。越界或文件缺失时如实报错，不返回假数据。"""
        arr = self._matrix()
        if arr is None:
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
