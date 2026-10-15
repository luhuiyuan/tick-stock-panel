#!/usr/bin/env python3
"""部署前检查 data/ 能否被容器用户和宿主机采集器写。

检查这些事，任一失败就以退出码 1 结束：

1. data/ 和 data/news 对 APP_UID/APP_GID 可写。采集器要写收件箱，容器要写库。
2. 只有通过 --dsa 启用 DSA 检查时，data/dsa 和 .env 才必须可写。
3. ~/.venvs/tsp-collector 的 Python 不低于 3.10；未启用宿主机采集器时缺失只提示。
4. 默认模式不要求所有历史文件属于目标 UID/GID；属主漂移只提示，--strict 才阻塞。

权限按属主、主组、other 三位判断，不看附加组，也不看 ACL。
当前进程的 uid 正好是目标用户时，会再实际写一个临时文件。不改 .env。
3.10 可以运行，但会提示推荐按 docs/news-sources.md 重建为 3.11。
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

_MIN_PYTHON = (3, 10)
_RECOMMENDED = (3, 11)
_DEFAULT_UID = 1000
_DEFAULT_GID = 1000
_DRIFT_LIMIT = 20


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def parse_id(raw: str, label: str) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{label} 不是整数: {raw}") from exc
    if value < 0:
        raise ValueError(f"{label} 不能为负")
    return value


def resolve_id(
    cli: int | None,
    env_value: str | None,
    dotenv_value: str | None,
    default: int,
    label: str,
) -> int:
    if cli is not None:
        return cli
    for raw in (env_value, dotenv_value):
        if raw is not None and str(raw).strip():
            return parse_id(str(raw), label)
    return default


def default_python(env_value: str | None = None, dotenv_value: str | None = None) -> Path:
    for raw in (env_value, dotenv_value):
        if raw is not None and str(raw).strip():
            return Path(str(raw).strip())
    return Path.home() / ".venvs" / "tsp-collector" / "bin" / "python"


def resolve_data_dir(raw: str, repo: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path


def parse_version(text: str) -> tuple[int, int, int]:
    raw = text.strip().split()[-1] if text.strip() else ""
    nums = raw.split(".")
    if len(nums) < 2:
        raise ValueError(f"无法解析 Python 版本: {text!r}")
    micro = int(nums[2]) if len(nums) > 2 else 0
    return int(nums[0]), int(nums[1]), micro


def read_python_version(executable: Path) -> tuple[int, int, int]:
    if not executable.is_file():
        raise FileNotFoundError(str(executable))
    proc = subprocess.run(
        [str(executable), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "无法执行").strip()
        raise RuntimeError(detail)
    return parse_version(proc.stdout)


def version_error(version: tuple[int, int, int]) -> str | None:
    if version < _MIN_PYTHON:
        shown = ".".join(str(part) for part in version)
        return (
            f"采集器 Python 是 {shown}，需要 3.10 或更高。"
            "推荐用 Python 3.11 重建 ~/.venvs/tsp-collector，见 docs/news-sources.md。"
        )
    return None


def version_warning(version: tuple[int, int, int]) -> str | None:
    if _MIN_PYTHON <= version < _RECOMMENDED:
        shown = ".".join(str(part) for part in version)
        return (
            f"采集器 Python 是 {shown}，可以运行。"
            "推荐按 docs/news-sources.md 用 Python 3.11 重建 ~/.venvs/tsp-collector。"
        )
    return None


def _mode_allows(path: Path, uid: int, gid: int, *, write: bool) -> bool:
    st = path.stat()
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == uid:
        mask = stat.S_IXUSR | (stat.S_IWUSR if write else 0)
    elif st.st_gid == gid:
        mask = stat.S_IXGRP | (stat.S_IWGRP if write else 0)
    else:
        mask = stat.S_IXOTH | (stat.S_IWOTH if write else 0)
    return (mode & mask) == mask


def file_writable(path: Path, uid: int, gid: int) -> bool:
    """文件是否允许 uid/gid 写入。不要求执行位。"""
    st = path.stat()
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == uid:
        mask = stat.S_IWUSR
    elif st.st_gid == gid:
        mask = stat.S_IWGRP
    else:
        mask = stat.S_IWOTH
    return bool(mode & mask)


def env_file_problem(
    path: Path,
    uid: int,
    gid: int,
    *,
    require_write: bool = False,
    require_owner: bool = False,
) -> str | None:
    """检查 dotenv 文件；默认只检查实际需要的读写权限。"""
    if not path.is_file():
        return f"{path} 不存在或不是文件。"
    st = path.stat()
    if require_owner and (st.st_uid != uid or st.st_gid != gid):
        return f"{path} 属主是 {st.st_uid}:{st.st_gid}，期望 {uid}:{gid}。"
    if require_write and not file_writable(path, uid, gid):
        return f"{path} 对 uid {uid}（gid {gid}）不可写。决策页保存定时设置会写回该文件。"
    return None


def dsa_data_problems(
    data_dir: Path,
    uid: int,
    gid: int,
    *,
    enabled: bool = False,
) -> tuple[list[str], list[str]]:
    """仅在 DSA 启用时要求 data/dsa 存在并可写。"""
    dsa = data_dir / "dsa"
    if not enabled:
        return [], []
    if not dsa.exists():
        return [f"{dsa} 不存在。启用 dsa 前先创建该目录。"], []
    if not dsa.is_dir():
        return [f"{dsa} 不是目录"], []
    ok, detail = directory_writable(dsa, uid, gid)
    if not ok:
        return [detail], []
    return [], []


def directory_writable(path: Path, uid: int, gid: int) -> tuple[bool, str]:
    """目录是否允许 uid/gid 创建文件。当前进程就是该 uid 时，实际写一次。"""
    if not path.is_dir():
        return False, f"{path} 不存在或不是目录"
    # 先按目标 uid/gid 的权限位判断。即使预检脚本由 root 运行，
    # 也不能把 root 的特权当成容器运行用户的可写性。
    if not _mode_allows(path, uid, gid, write=True):
        return False, f"{path} 不可写：权限不允许 uid {uid}（gid {gid}）写入"
    if os.geteuid() == uid:
        probe = path / f".preflight-{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8")
        except OSError as exc:
            return False, f"{path} 对当前用户（uid {uid}）不可写: {exc.strerror or exc}"
        try:
            probe.unlink()
        except OSError as exc:
            return False, f"无法删除预检文件 {probe}: {exc.strerror or exc}"
        return True, ""
    return True, ""


def iter_paths(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        yield current
        for name in filenames:
            yield current / name
        for name in dirnames:
            candidate = current / name
            if candidate.is_symlink():
                yield candidate


def ownership_errors(root: Path, uid: int, gid: int, *, limit: int = _DRIFT_LIMIT) -> list[str]:
    mismatched: list[str] = []
    for path in iter_paths(root):
        st = path.lstat()
        if st.st_uid == uid and st.st_gid == gid:
            continue
        mismatched.append(f"{path} 属主是 {st.st_uid}:{st.st_gid}，期望 {uid}:{gid}")
    if len(mismatched) <= limit:
        return mismatched
    hidden = len(mismatched) - limit
    return [*mismatched[:limit], f"另有 {hidden} 处属主不是 {uid}:{gid}"]


def collect_problems(
    data_dir: Path,
    *,
    uid: int,
    gid: int,
    python: Path,
    version: tuple[int, int, int] | None = None,
    env_file: Path | None = None,
    strict: bool = False,
    dsa_enabled: bool = False,
    collector_required: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if version is None:
        try:
            version = read_python_version(python)
        except FileNotFoundError:
            note = (
                f"找不到采集器 Python：{python}。"
                "如启用宿主机采集器，请按 docs/news-sources.md 用 Python 3.11 重建。"
            )
            (errors if (strict or collector_required) else warnings).append(note)
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"无法读取采集器 Python 版本（{python}）: {exc}")
    if version is not None:
        problem = version_error(version)
        if problem:
            errors.append(problem)
        else:
            note = version_warning(version)
            if note:
                warnings.append(note)

    if env_file is not None:
        problem = env_file_problem(
            env_file,
            uid,
            gid,
            require_write=dsa_enabled,
            require_owner=strict,
        )
        if problem:
            errors.append(problem)

    if not data_dir.is_dir():
        errors.append(f"{data_dir} 不存在。创建后执行 sudo chown -R {uid}:{gid} {data_dir}")
        return errors, warnings

    ok, detail = directory_writable(data_dir, uid, gid)
    if not ok:
        errors.append(detail)
    news = data_dir / "news"
    if not news.is_dir():
        errors.append(
            f"{news} 不存在。systemd 的 ReadWritePaths 要求这个目录事先存在，并属于运行用户。"
            f"可执行 sudo chown -R {uid}:{gid} {data_dir}"
        )
    else:
        ok, detail = directory_writable(news, uid, gid)
        if not ok:
            errors.append(detail)
    dsa_errors, dsa_warnings = dsa_data_problems(data_dir, uid, gid, enabled=dsa_enabled)
    errors.extend(dsa_errors)
    warnings.extend(dsa_warnings)
    drift = ownership_errors(data_dir, uid, gid)
    if strict:
        errors.extend(drift)
    elif drift:
        warnings.append(
            "data/ 下存在历史属主与目标 UID/GID 不同的文件；默认模式不阻塞，"
            "如需逐项校验请使用 --strict。"
        )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 data/ 可写性和可选的部署属主契约")
    parser.add_argument("--data-dir", default="", help="TSP data 目录，默认 <仓库>/data 或 DATA_DIR")
    parser.add_argument(
        "--python",
        default="",
        help="采集器 Python，默认 ~/.venvs/tsp-collector/bin/python",
    )
    parser.add_argument("--uid", type=int, default=None, help="容器和采集器的 uid，默认 APP_UID 或 1000")
    parser.add_argument("--gid", type=int, default=None, help="容器和采集器的 gid，默认 APP_GID 或 1000")
    parser.add_argument("--env-file", default="", help="要检查的 .env，默认 <仓库>/.env")
    parser.add_argument("--dsa", action="store_true", help="启用 DSA 的 data/dsa 和 .env 可写性检查")
    parser.add_argument("--strict", action="store_true", help="严格检查 data/ 下所有文件属主，并将采集器缺失视为错误")
    args = parser.parse_args(argv)

    repo = repo_root()
    dotenv = load_dotenv(repo / ".env")
    try:
        uid = resolve_id(
            args.uid, os.environ.get("APP_UID"), dotenv.get("APP_UID"), _DEFAULT_UID, "APP_UID",
        )
        gid = resolve_id(
            args.gid, os.environ.get("APP_GID"), dotenv.get("APP_GID"), _DEFAULT_GID, "APP_GID",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    data_raw = args.data_dir or os.environ.get("DATA_DIR") or dotenv.get("DATA_DIR") or "data"
    data_dir = resolve_data_dir(data_raw, repo)

    if args.python:
        python = Path(args.python)
    else:
        python = default_python(
            os.environ.get("TSP_COLLECTOR_PYTHON"),
            dotenv.get("TSP_COLLECTOR_PYTHON"),
        )

    if args.env_file:
        env_file = Path(args.env_file)
        if not env_file.is_absolute():
            env_file = repo / env_file
    else:
        env_file = repo / ".env"

    errors, warnings = collect_problems(
        data_dir,
        uid=uid,
        gid=gid,
        python=python,
        env_file=env_file,
        strict=args.strict,
        dsa_enabled=args.dsa,
        collector_required=args.strict,
    )
    for note in warnings:
        print(f"注意: {note}")
    if errors:
        for item in errors:
            print(item, file=sys.stderr)
        return 1
    print(f"部署预检通过（uid {uid}:{gid}，data {data_dir}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
