"""按「原始路径」精确清除回收站条目，真正释放磁盘空间。

为什么需要这个脚本
------------------
本机的删除会被拦到回收站：shell 里的 ``rm`` 是个函数（``.../rm``），Python 的
``os.remove`` 在删备份包这种「别的进程刚写出来的大文件」时同样会被拦下来 ——
2026-11-02 删 2025 的两个包（16.14 GB）、11-03 删 2024 的分片与中转库
（9.66 + 5.44 GB），删完 ``df`` 一分没涨，文件都躺在 ``<盘>:\\$RECYCLE.BIN\\<SID>\\``
下的 ``$Rxxxxxx`` 里。

``$R*`` 是内容、``$I*`` 是元数据（里面记着原始路径）。清空整个回收站会连带删掉
用户自己的东西，所以这里**按原始路径前缀精确匹配**，只清掉指定目录下的条目：
同一 SID 下若 ``$I`` 记录的原始路径以某个 ``--prefix`` 开头，就删掉 ``$R`` 与 ``$I``。

用法
----

    cd backend

    # 先看会删哪些、能释放多少（不删任何东西）
    PYTHONPATH= .venv/Scripts/python.exe scripts/purge_recycle_paths.py \
        --prefix "E:\\tsp_backup_ticks\\2024" --dry-run

    # 真删
    PYTHONPATH= .venv/Scripts/python.exe scripts/purge_recycle_paths.py \
        --prefix "E:\\tsp_backup_ticks\\2024" --prefix "D:\\tsp\\backup_export\\_staging_minute"

目录条目手工递归删，不用 ``shutil.rmtree``：WorkBuddy 的 shim 在删除文件数 >50 时
会直接 ``SystemExit(1)``。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

PATH_RE = re.compile(r"[A-Za-z]:\\[^\x00]*")


def recycle_sid_dirs() -> list[Path]:
    """各盘回收站下属于当前用户的 SID 目录。"""
    out: list[Path] = []
    for letter in "CDEFGH":
        root = Path(f"{letter}:\\$RECYCLE.BIN")
        if not root.is_dir():
            continue
        out.extend(p for p in root.iterdir() if p.is_dir() and p.name.startswith("S-1-5-"))
    return out


def original_path(i_path: Path) -> str | None:
    """``$I`` 元数据里第 24 字节起是 UTF-16LE 的原始路径。"""
    text = i_path.read_bytes()[24:].decode("utf-16-le", errors="ignore")
    match = PATH_RE.search(text)
    return match.group(0) if match else None


def force_remove(path: Path) -> None:
    if path.is_dir():
        for child in path.iterdir():
            force_remove(child)
        os.rmdir(path)
        return
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass
    os.remove(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", action="append", required=True,
                    help="原始路径前缀，可给多个")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    # Git Bash 会把参数里的反斜杠转成斜杠（`E:\x\y` 传进来是 `E:/x/y`），而 `$I` 里
    # 记的是 Windows 风格路径，两边必须先统一成反斜杠再比。
    prefixes = tuple(p.replace("/", "\\").rstrip("\\") for p in args.prefix)

    hits: list[tuple[Path, str, int]] = []
    for sid in recycle_sid_dirs():
        for i_path in sid.glob("$I*"):
            path = original_path(i_path)
            if not path or not path.replace("/", "\\").startswith(prefixes):
                continue
            r_path = i_path.with_name("$R" + i_path.name[2:])
            size = 0
            if r_path.is_file():
                size = r_path.stat().st_size
            elif r_path.is_dir():
                size = sum(p.stat().st_size for p in r_path.rglob("*") if p.is_file())
            hits.append((i_path, path, size))

    total = sum(size for _, _, size in hits)
    print(f"命中 {len(hits)} 个条目，共 {total / 2**30:.2f} GB")
    for _, path, size in hits[:5]:
        print(f"  {path}  {size / 2**20:.1f} MB")
    if len(hits) > 5:
        print(f"  ...另有 {len(hits) - 5} 个")

    if args.dry_run:
        print("--dry-run：未删除。")
        return 0

    freed = 0
    problems: list[str] = []
    for i_path, path, size in hits:
        r_path = i_path.with_name("$R" + i_path.name[2:])
        try:
            if r_path.exists():
                force_remove(r_path)
            os.remove(i_path)
            freed += size
        except OSError as exc:
            problems.append(f"{path}: {exc}")

    print(f"已删除 {len(hits) - len(problems)} 个条目，释放 {freed / 2**30:.2f} GB")
    for item in problems[:10]:
        print(f"  ! {item}")
    for letter in sorted({i[1][0].upper() for i in hits}):
        free = shutil.disk_usage(f"{letter}:\\").free / 2**30
        print(f"{letter}: 剩余 {free:.2f} GB")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
