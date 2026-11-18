# -*- coding: utf-8 -*-
"""打板标签进化搜索：把 94 个标签位掩码当作信号池，枚举 AND / NOT 组合，
按 r_t1 胜率与均值找靠谱公式，并记录逐层 Top-N 与父子谱系形成「进化路径」。

设计要点（与 strategy_combo 同构，但针对打板标签数据）：
- 每个「文字」= 一个标签的正向(tag)或反向(NOT tag)，共 2*94 个；
- 一个「组合」= 若干文字的 AND -> 用 bitmask 按位与，O(1) 集合运算，不重跑任何回测；
- NOT(A AND B) 这类由前端按需对结果取补，引擎输出里附带每个组合的命中掩码统计；
- 分层贪心/束搜索：size=1 全量打分 -> 每轮从当前 beam 扩展一个文字 -> 保留 Top-N；
- 防过拟合：按事件日 di 升序切 IS(前 70%)/OOS(后 30%)，记录 is/oos 两段分数与退化标记；
- 收益口径：r_t1（T日涨停价买 -> T+1 收盘卖），-9999 为 MISSING 跳过。

本模块只读 parquet 缓存，不写任何文件；结果供 API 直接返回。
"""
from __future__ import annotations

import heapq
import itertools
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.daban_compute import TAG_DEFS

MISSING = -9999
# 搜索预算硬上限，防止组合爆炸
MAX_COMBO_SIZE = 3          # 组合最多 3 个文字（3 层进化）
BEAM_WIDTH = 24             # 每层保留的 beam 宽度
TOP_N = 12                  # 每层对外展示的 Top-N
MIN_SAMPLES = 200           # 最小样本门槛（过滤 100% 胜率的小样本假象）


def _data_dir() -> Path:
    from app.config import settings
    return Path(settings.data_dir) / "daban_research"


def _load() -> tuple[pd.DataFrame, pd.DataFrame]:
    d = _data_dir()
    stats = pd.read_parquet(d / "stats.parquet")
    tags = pd.read_parquet(d / "tags.parquet")
    if len(stats) != len(tags):
        raise ValueError("stats/tags 行数不一致")
    return stats, tags


def _term_masks(tags: pd.DataFrame) -> tuple[list[str], list[str], list[np.ndarray]]:
    """构造 pre 标签的正向/反向文字池。

    只纳入 timing=='pre' 的标签：event/buy/review 含 T 日涨停动作或事后信息
    （如一字板 t_yizi、三连板 t_gap3），是本研究的标签对象而非可用信号，
    纳入会造成前视偏差与过拟合。返回 (keys, labels, masks)。
    """
    keys: list[str] = []
    labels: list[str] = []
    masks: list[np.ndarray] = []
    name_of = {d["id"]: d["name"] for d in TAG_DEFS}
    for d in TAG_DEFS:
        if d.get("timing") != "pre":
            continue
        bit = d["bit"]
        w, off = divmod(bit, 53)
        base = ((tags[f"tag_{w}"].to_numpy(np.uint64) >> off) & 1).astype(bool)
        # 正向
        keys.append(f"+{d['id']}")
        labels.append(name_of[d["id"]])
        masks.append(base)
        # 反向（排除数据缺口：NOT 只作用于本 bit 合法的行；这里 bit 恒有值，直接取反）
        keys.append(f"-{d['id']}")
        labels.append(f"非·{name_of[d['id']]}")
        masks.append(~base)
    return keys, labels, masks


def _stats_of(mask: np.ndarray, r1: np.ndarray, is_mask: np.ndarray, oos_mask: np.ndarray) -> dict | None:
    m = mask
    n = int(m.sum())
    if n < MIN_SAMPLES:
        return None
    v = r1[m]
    win = float((v > 0).mean())
    mean = float(v.mean())

    def seg(seg_mask: np.ndarray) -> dict | None:
        sm = m & seg_mask
        sn = int(sm.sum())
        if sn < MIN_SAMPLES:
            return None
        sv = r1[sm]
        return {"n": sn, "win": round(float((sv > 0).mean()), 4), "mean": round(float(sv.mean()), 3)}

    st_is = seg(is_mask)
    st_oos = seg(oos_mask)
    return {
        "n": n,
        "win": round(win, 4),
        "mean": round(mean, 3),
        "is": st_is,
        "oos": st_oos,
    }


def _score(st: dict | None, baseline_win: float) -> float:
    """综合分：超额胜率为主 + 超额均值为辅，OOS 退化打折。"""
    if st is None:
        return -1e9
    ex_win = st["win"] - baseline_win
    ex_mean = st["mean"]
    score = ex_win * 100 + ex_mean  # 胜率百分点(权重高) + 均值百分点
    # OOS 退化惩罚：OOS 分 < IS 一半则腰斩
    if st["is"] and st["oos"]:
        if st["is"]["win"] > 0 and st["oos"]["win"] < 0.5 * st["is"]["win"]:
            score *= 0.5
    return score


def run_evolution(*, max_size: int = MAX_COMBO_SIZE, beam_width: int = BEAM_WIDTH,
                  top_n: int = TOP_N, min_samples: int = MIN_SAMPLES) -> dict:
    """执行进化搜索，返回 {meta, baseline, layers, tree}。"""
    global MIN_SAMPLES
    MIN_SAMPLES = max(10, min(min_samples, 500))
    max_size = max(1, min(max_size, MAX_COMBO_SIZE))
    beam_width = max(4, min(beam_width, 48))
    top_n = max(1, min(top_n, 40))

    t0 = time.perf_counter()
    stats, tags = _load()
    r1 = stats["r_t1"].to_numpy(np.float64) / 100.0
    valid = r1 > -9000
    # 触板口径：仅封板子集单独给一个基准参考（但搜索在全样本上做，避免样本过少）
    di = stats["di"].to_numpy(np.int64)
    order = np.argsort(di, kind="stable")
    split = int(len(order) * 0.7)
    is_mask = np.zeros(len(stats), bool)
    oos_mask = np.zeros(len(stats), bool)
    is_mask[order[:split]] = True
    oos_mask[order[split:]] = True
    is_mask &= valid
    oos_mask &= valid
    valid_mask = valid

    # 全样本与仅封板基准
    base_all = float((r1[valid_mask] > 0).mean())
    mean_all = float(r1[valid_mask].mean())
    seal = stats["zaban"].to_numpy(np.int8) == 0
    base_seal = float((r1[valid_mask & seal] > 0).mean())
    mean_seal = float(r1[valid_mask & seal].mean())

    keys, labels, masks = _term_masks(tags)
    # 给每个 text mask 施加 valid 约束
    masks = [m & valid_mask for m in masks]
    n_terms = len(keys)

    # size=1：所有单文字
    beam: list[dict] = []
    seen_single: set[tuple] = set()
    for i in range(n_terms):
        st = _stats_of(masks[i], r1, is_mask, oos_mask)
        if st is None:
            continue
        comb = (i,)
        beam.append({
            "terms": comb,
            "key": keys[i],
            "label": labels[i],
            "mask": masks[i],
            "stat": st,
            "score": _score(st, base_all),
            "parent": None,
        })
    beam.sort(key=lambda x: x["score"], reverse=True)
    beam = beam[:beam_width]

    layers: list[dict] = []
    tree_nodes: list[dict] = []
    tree_links: list[dict] = []
    node_index: dict[tuple, int] = {}

    def add_node(rec: dict, size: int) -> int:
        comb = rec["terms"]
        if comb in node_index:
            return node_index[comb]
        idx = len(tree_nodes)
        node_index[comb] = idx
        tree_nodes.append({
            "id": idx,
            "size": size,
            "key": rec["key"],
            "label": rec["label"],
            "terms": [keys[t] for t in comb],
            "win": rec["stat"]["win"],
            "mean": rec["stat"]["mean"],
            "n": rec["stat"]["n"],
            "score": round(rec["score"], 4),
            "oos_win": rec["stat"]["oos"]["win"] if rec["stat"]["oos"] else None,
        })
        if rec["parent"] is not None and rec["parent"] in node_index:
            tree_links.append({"source": node_index[rec["parent"]], "target": idx})
        return idx

    # 记录每层
    layer1 = sorted(beam, key=lambda x: x["score"], reverse=True)[:top_n]
    for r in layer1:
        add_node(r, 1)
    layers.append({
        "size": 1,
        "top": [{
            "key": r["key"], "label": r["label"], "terms": [keys[t] for t in r["terms"]],
            "stat": r["stat"], "score": round(r["score"], 4), "node": node_index[r["terms"]],
        } for r in layer1],
        "beam_count": len(beam),
    })

    # size>=2：从当前 beam 扩展一个更大下标的文字（保证组合内文字按 index 递增，避免重复）
    for size in range(2, max_size + 1):
        expanded: list[dict] = []
        for rec in beam:
            last = rec["terms"][-1]
            new_mask_base = rec["mask"]
            for j in range(last + 1, n_terms):
                m = new_mask_base & masks[j]
                st = _stats_of(m, r1, is_mask, oos_mask)
                if st is None:
                    continue
                comb = tuple(sorted((*rec["terms"], j)))
                # 反向标签之间可能重复计数；用 terms 去重集合即可（组合唯一）
                expanded.append({
                    "terms": comb,
                    "key": " AND ".join(keys[t] for t in comb),
                    "label": " + ".join(labels[t] for t in comb),
                    "mask": m,
                    "stat": st,
                    "score": _score(st, base_all),
                    "parent": rec["terms"],
                })
        if not expanded:
            break
        expanded.sort(key=lambda x: x["score"], reverse=True)
        beam = expanded[:beam_width]
        layer_top = beam[:top_n]
        for r in layer_top:
            add_node(r, size)
        layers.append({
            "size": size,
            "top": [{
                "key": r["key"], "label": r["label"], "terms": [keys[t] for t in r["terms"]],
                "stat": r["stat"], "score": round(r["score"], 4), "node": node_index.get(r["terms"]),
            } for r in layer_top],
            "beam_count": len(beam),
        })

    meta = {
        "total_events": int(len(stats)),
        "valid_events": int(valid_mask.sum()),
        "baseline": {"win": round(base_all, 4), "mean": round(mean_all, 3)},
        "baseline_seal": {"win": round(base_seal, 4), "mean": round(mean_seal, 3)},
        "split_date_index": split,
        "min_samples": MIN_SAMPLES,
        "beam_width": beam_width,
        "max_size": max_size,
        "term_count": n_terms,
        "search_pool": "仅 timing=pre 的事前形态标签（正向+反向）；event/buy/review 含涨停动作/事后信息，已排除",
        "return_basis": "r_t1: T日涨停价买入 -> T+1收盘卖出；-9999(停牌/缺口)已剔除",
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "note": "组合为标签正向(+)/反向(-)的 AND；分年 IS/OOS 70/30 切分，OOS 退化腰斩。"
                "胜率为历史复盘、含未来数据，不构成实盘信号；大量组合存在多重比较风险。",
    }
    return {"meta": meta, "layers": layers, "tree": {"nodes": tree_nodes, "links": tree_links}}
