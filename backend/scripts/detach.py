"""以「脱离当前会话」的方式启动一个后台子进程（仅 Windows）。

用途：长时间跑的导任务（例如 export_history_minute.py）需要脱离调用它的终端 /
会话 / job object 独立存活，否则父进程一退出子进程就被连带回收。

用法::

    python scripts/detach.py <logfile> -- <command> [args...]

示例::

    python scripts/detach.py D:/tsp/backup_export/export-2025.log -- \\
        scripts/export_history_minute.py --years 2025

说明：
- ``DETACHED_PROCESS`` 让子进程没有控制台，不随父进程的控制台关闭而退出。
- ``CREATE_BREAKAWAY_FROM_JOB`` 让它能脱离父进程所在的 job object（不在 job 中时
  该标志会被系统忽略，不会报错）。
- ``PYTHONPATH`` 会被剔除：本机 shell 里的该变量指向 WorkBuddy 的 shim 目录，
  其 ``sitecustomize.py`` 会在删除文件数 >50 时 ``SystemExit(1)``，直接带崩导出进程。
- stdout/stderr 都以追加模式写入 ``<logfile>`` 与 ``<logfile>.err``。

局限（实测）：这三个标志只是让子进程脱离**控制台**和 **job object**，不改变父子
关系。如果调用方所在环境是按**进程树**清理子孙进程的（例如受管理的沙箱/CI），父进程
一退出子进程照样被连坐——``DETACHED_PROCESS`` 只去 console，``CREATE_BREAKAWAY_FROM_JOB``
只对 job object 生效，两者都拦不住按树枚举的清理。这种情况下唯一可靠的办法是让进程
的父进程不属于该树（计划任务、WMI、服务），做不到就只能在原会话里前台跑，靠脚本自身
的断点续跑兜底。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

BACKEND_DIR = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[1] != "--":
        print(__doc__)
        return 2

    logfile = Path(argv[0]).resolve()
    command = argv[2:]
    if not command:
        print("缺少要执行的命令", file=sys.stderr)
        return 2

    logfile.parent.mkdir(parents=True, exist_ok=True)
    errfile = logfile.with_suffix(logfile.suffix + ".err")

    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}

    with logfile.open("ab", buffering=0) as out, errfile.open("ab", buffering=0) as err:
        proc = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(BACKEND_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
            close_fds=True,
        )

    print(f"pid={proc.pid}")
    print(f"log={logfile}")
    print(f"err={errfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
