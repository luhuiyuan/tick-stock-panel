"""插件外部网关进程的启动编排。

为什么需要: 部分插件的"可用"不只看 Python 依赖, 还看一个**独立进程**。eltdx 是
典型: `install_plugin` 只装 eltdx SDK, 而 provider 默认走 HTTP 网关
(``ELTDX_TRANSPORT=http``), 网关没起时 availability() 返回 False, 设置页卡片永久
灰显不可选。此前用户必须自己开终端跑 ``eltdx-http`` —— 安装流程缺了最后一步。

本模块把这一步补成可点击动作:
  1. 先探 ``/health``, 已在运行则幂等返回成功;
  2. 否则用后端自身解释器 ``python -m <gateway.module>`` 拉起**分离进程**,
     后端热重载/重启不会带走它(网关持有 7709 连接池, 生命周期应独立于后端);
  3. 轮询 ``/health`` 直到就绪或超时, 并把子进程输出落到日志供排障。

安全边界:
  * 只对 ``plugin.yaml`` 声明了 ``gateway.module`` 的**内置**插件生效 —— 清单随
    仓库走, 用户无法通过界面写入, 不接受前端传入命令/端口;
  * 启动命令是参数数组, 不经 shell, 不做字符串拼接;
  * 端口与健康路径同样来自清单。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 网关冷启动要连通上游行情主站, 实测数秒; 给 20s 上限兼顾慢网络。
_START_TIMEOUT_S = 20.0
_POLL_INTERVAL_S = 0.3
# 单次健康探测超时: 已在运行的网关应秒回, 卡住即视为未就绪。
_PROBE_TIMEOUT_S = 2.0

# 正在启动中的网关进程, 防止连点按钮拉起多个进程。
# 只保护"是否需要 spawn"这一判断; 健康探测在锁外进行 (CONTRIBUTING §6.2:
# 不在持锁期间执行网络请求)。
_PENDING: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


def gateway_config(manifest: dict | None) -> dict | None:
    """从插件清单取网关声明(校验口径与设置页展示一致, 见 loader.gateway_manifest)。"""
    from app.data_providers.custom import gateway_manifest

    return gateway_manifest(manifest)


def health_url(cfg: dict) -> str:
    port = int(cfg.get("port") or 8000)
    path = str(cfg.get("health_path") or "/health").strip()
    if not path.startswith("/"):
        path = "/" + path
    return f"http://127.0.0.1:{port}{path}"


def is_healthy(cfg: dict) -> bool:
    """探测网关健康端点。连接失败/超时/非 200 一律视为未就绪。"""
    try:
        return httpx.get(health_url(cfg), timeout=_PROBE_TIMEOUT_S).status_code == 200
    except Exception:  # noqa: BLE001 — 未就绪的判定不该因异常类型而变化
        return False


def log_path(name: str) -> Path:
    """网关子进程日志路径 (与 app/logfile.py 的 data/logs 约定一致)。"""
    return settings.data_dir / "logs" / f"{name}-gateway.log"


def _spawn(name: str, cfg: dict) -> tuple[subprocess.Popen | None, str]:
    """拉起分离的网关进程; 失败返回 (None, 原因)。"""
    port = int(cfg.get("port") or 8000)
    cmd = [
        sys.executable, "-m", str(cfg["module"]).strip(),
        "--host", "127.0.0.1", "--port", str(port),
    ]
    path = log_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 追加模式: 保留上一轮失败现场, 便于对照"这次和上次失败原因是否相同"
        log = open(path, "ab")  # noqa: SIM115 — 句柄需显式传给子进程后立即关闭
    except OSError as e:
        return None, f"无法写网关日志 {path}: {e}"
    kwargs: dict = {}
    if os.name == "nt":
        # DETACHED_PROCESS: 不继承后端控制台窗口;
        # CREATE_NEW_PROCESS_GROUP: 后端的 Ctrl-C / 退出不波及网关。
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        # argv 全部来自内置插件清单与固定参数, 不经 shell, 无注入面
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs
        )
    except Exception as e:  # noqa: BLE001
        return None, f"网关进程启动失败: {e}"
    finally:
        log.close()
    logger.info("插件 %s 网关进程已拉起: %s (pid=%s)", name, " ".join(cmd[1:]), proc.pid)
    return proc, ""


def start(name: str) -> tuple[bool, str]:
    """启动插件声明的网关, 幂等。返回 (是否就绪, 说明)。"""
    from app.data_providers import custom

    if not custom.is_builtin(name):
        return False, f"插件 '{name}' 不存在或不是内置插件"
    cfg = gateway_config(custom.plugin_manifest(name))
    if cfg is None:
        return False, f"插件 '{name}' 未声明网关, 无需启动"
    url = health_url(cfg)

    # 快路径: 已在运行(用户手动启动过, 或上一次点击已成功) → 直接成功, 不重复拉起
    if is_healthy(cfg):
        return True, f"网关已在运行 ({url})"

    with _LOCK:
        pending = _PENDING.get(name)
        if pending is not None and pending.poll() is None:
            # 连点: 复用上一次拉起、尚未就绪的进程, 而不是再起一个抢同一端口
            proc = pending
        else:
            proc, err = _spawn(name, cfg)
            if proc is None:
                _PENDING.pop(name, None)
                return False, err
            _PENDING[name] = proc

    deadline = time.monotonic() + _START_TIMEOUT_S
    while time.monotonic() < deadline:
        if is_healthy(cfg):
            with _LOCK:
                _PENDING.pop(name, None)
            return True, f"网关已启动 ({url})"
        code = proc.poll()
        if code is not None:
            with _LOCK:
                _PENDING.pop(name, None)
            logger.warning("插件 %s 网关进程退出 (exit=%s), 详见 %s", name, code, log_path(name))
            return False, f"网关进程已退出 (exit {code}), 详见日志 {log_path(name)}"
        time.sleep(_POLL_INTERVAL_S)

    # 超时但进程仍在: 保留在 _PENDING, 下次点击继续探测而不是再拉一个
    return False, f"网关启动超时 ({_START_TIMEOUT_S:.0f}s), 详见日志 {log_path(name)}"
