"""plugin_gateway.start 的单元测试 —— 网关一键启动的幂等、复用与失败路径。

背景: eltdx 插件装完 SDK 仍不可用, 因为 provider 默认走 HTTP 网关
(``ELTDX_TRANSPORT=http``), 网关进程没起时 availability() 恒为 False。设置页新增的
「启动网关」按钮走本服务。这里覆盖四条决策路径(已在运行/需拉起/子进程秒退/连点),
全部用假进程, 不依赖真实网关, 也不写用户数据目录。
"""
from __future__ import annotations

import pytest

from app.services import plugin_gateway as pg


class _FakeProc:
    """最小 Popen 替身: 只需要 pid 与 poll()。"""

    def __init__(self, pid: int = 4242, exit_code: int | None = None) -> None:
        self.pid = pid
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


@pytest.fixture(autouse=True)
def _clean_pending():
    """_PENDING 是模块级状态, 用例之间必须隔离。"""
    pg._PENDING.clear()
    yield
    pg._PENDING.clear()


@pytest.fixture
def fake_gateway_plugin(monkeypatch, tmp_path):
    """铺一个声明了网关的内置插件; 日志路径指到 tmp_path, 不碰 data/logs。"""
    manifest = {
        "name": "fakegw",
        "display_name": "Fake",
        "gateway": {"module": "fake.gateway_server", "port": 8123, "health_path": "/health"},
    }
    monkeypatch.setattr(
        "app.data_providers.custom.is_builtin", lambda name: name == "fakegw"
    )
    monkeypatch.setattr(
        "app.data_providers.custom.plugin_manifest",
        lambda name: manifest if name == "fakegw" else None,
    )
    monkeypatch.setattr(pg, "log_path", lambda name: tmp_path / f"{name}-gateway.log")
    return manifest


def test_start_rejects_unknown_plugin(monkeypatch):
    """非内置插件直接拒绝 —— 启动命令只认仓库内的清单。"""
    monkeypatch.setattr("app.data_providers.custom.is_builtin", lambda name: False)

    ok, msg = pg.start("nope")
    assert not ok
    assert "不存在" in msg


def test_start_rejects_plugin_without_gateway(monkeypatch):
    """没声明 gateway 的插件不该走这条路径。"""
    monkeypatch.setattr("app.data_providers.custom.is_builtin", lambda name: True)
    monkeypatch.setattr(
        "app.data_providers.custom.plugin_manifest", lambda name: {"name": name}
    )

    ok, msg = pg.start("plain")
    assert not ok
    assert "未声明网关" in msg


def test_start_returns_ok_without_spawning_when_already_running(fake_gateway_plugin, monkeypatch):
    """幂等: 网关已在运行(含用户手起)时不重复拉起。"""
    spawned: list[str] = []
    monkeypatch.setattr(pg, "is_healthy", lambda cfg: True)
    monkeypatch.setattr(pg, "_spawn", lambda name, cfg: (spawned.append(name), (None, ""))[1])

    ok, msg = pg.start("fakegw")
    assert ok, msg
    assert "已在运行" in msg
    assert spawned == [], "已在运行时不应再拉起进程"


def test_start_polls_until_health_ok(fake_gateway_plugin, monkeypatch):
    """拉起后要等健康检查就绪才报成功, 不能拉起即返回。"""
    calls = {"spawn": 0, "health": 0}
    proc = _FakeProc()

    def fake_spawn(name, cfg):
        calls["spawn"] += 1
        return proc, ""

    def fake_health(cfg):
        calls["health"] += 1
        return calls["health"] >= 3  # 前两次未就绪, 逼出轮询

    monkeypatch.setattr(pg, "_spawn", fake_spawn)
    monkeypatch.setattr(pg, "is_healthy", fake_health)
    monkeypatch.setattr(pg, "_START_TIMEOUT_S", 5.0)
    monkeypatch.setattr(pg, "_POLL_INTERVAL_S", 0.0)

    ok, msg = pg.start("fakegw")
    assert ok, msg
    assert calls["spawn"] == 1
    assert "已启动" in msg
    assert "fakegw" not in pg._PENDING, "成功后应清理待启动表"


def test_start_reports_child_exit_with_log_path(fake_gateway_plugin, monkeypatch):
    """子进程秒退(如依赖缺失)必须报出退出码与日志路径, 不能干等超时。"""
    monkeypatch.setattr(pg, "_spawn", lambda name, cfg: (_FakeProc(exit_code=1), ""))
    monkeypatch.setattr(pg, "is_healthy", lambda cfg: False)
    monkeypatch.setattr(pg, "_START_TIMEOUT_S", 5.0)
    monkeypatch.setattr(pg, "_POLL_INTERVAL_S", 0.0)

    ok, msg = pg.start("fakegw")
    assert not ok
    assert "exit 1" in msg
    assert "gateway.log" in msg


def test_start_reuses_pending_process_on_repeated_click(fake_gateway_plugin, monkeypatch):
    """连点: 第一次启动未就绪时, 第二次点击复用同一进程, 不抢同一端口。"""
    spawned: list[str] = []
    proc = _FakeProc()

    def fake_spawn(name, cfg):
        spawned.append(name)
        return proc, ""

    monkeypatch.setattr(pg, "_spawn", fake_spawn)
    monkeypatch.setattr(pg, "is_healthy", lambda cfg: False)
    monkeypatch.setattr(pg, "_START_TIMEOUT_S", 0.0)  # 立即超时, 进程仍活着
    monkeypatch.setattr(pg, "_POLL_INTERVAL_S", 0.0)

    ok1, _ = pg.start("fakegw")
    ok2, _ = pg.start("fakegw")
    assert not ok1 and not ok2
    assert spawned == ["fakegw"], f"应只拉起一次, 实际 {len(spawned)} 次"
    assert "fakegw" in pg._PENDING, "超时但进程仍活的记录要留着给下次复用"


def test_spawn_uses_backend_interpreter_and_manifest_module(fake_gateway_plugin, monkeypatch):
    """启动命令: 后端自身解释器 + 清单里的 module 与端口; 不经 shell。"""
    captured: dict = {}

    class _Popen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            self.pid = 1234

    monkeypatch.setattr(pg.subprocess, "Popen", _Popen)

    proc, err = pg._spawn("fakegw", fake_gateway_plugin["gateway"])
    assert err == "", err
    assert proc is not None
    assert captured["cmd"] == [
        pg.sys.executable,
        "-m",
        "fake.gateway_server",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
    ]
    assert captured["kwargs"]["stdin"] is pg.subprocess.DEVNULL
    assert "shell" not in captured["kwargs"], "不能走 shell"
