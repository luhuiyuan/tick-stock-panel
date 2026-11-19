"""标签迁移定义的显式安装入口；校验、落盘与回滚沿用既有存储链路。

因子定义见 app.factors.premarket，信号定义见 premarket_signal_definitions。
不由启动流程或默认种子自动调用。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.factors import store as factor_store
from app.factors.premarket import FACTORS
from app.strategy import custom_signals
from app.strategy.premarket_signal_definitions import SIGNALS


def install(data_dir: Path) -> dict[str, int]:
    """显式安装；全批冲突预检，已有相同定义不写，同名异义不覆盖。

    不自动运行：用户删除后不会在启动时复活。失败时回收本次新文件；
    已有用户定义及旧种子标记始终不动。安装需在服务停止或维护窗口执行。
    """
    from app.factors.registry import get_factor, isolated_registry

    groups = [('custom_factors', FACTORS), ('custom_signals', SIGNALS)]
    pending = []
    for folder, definitions in groups:
        for definition in definitions:
            path = data_dir / 'user_data' / folder / f"{definition['id']}.json"
            if path.exists():
                try:
                    same = json.loads(path.read_text(encoding='utf-8')) == definition
                except (ValueError, OSError):
                    same = False
                if not same:
                    raise ValueError(f"定义冲突，未覆盖: {definition['id']}")
            else:
                pending.append((folder, path, definition))
    # 所有因子和信号通过既有验证路径；预检注册不泄漏全局状态。
    with isolated_registry():
        for definition in FACTORS:
            spec = factor_store.to_spec(definition)
            existing = get_factor(spec.id)
            if existing is None:
                factor_store.register_definition(definition)
            elif existing != spec:
                raise ValueError(f"运行时因子冲突: {spec.id}")
        for definition in SIGNALS:
            custom_signals.validate(definition)
    written = []
    try:
        for _folder, path, definition in pending:
            path.parent.mkdir(parents=True, exist_ok=True)
            # x 模式防止预检后出现的新用户文件被覆盖。
            with path.open('x', encoding='utf-8') as stream:
                written.append(path)
                json.dump(definition, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
    except Exception:
        for path in written:
            path.unlink()
        raise
    # 当前进程也重新加载；运行服务必须通过其维护生命周期调用，不能跨进程失效。
    for definition in FACTORS:
        if get_factor(definition['id']) is None:
            factor_store.register_definition(definition)
    from app.indicators.pipeline import invalidate_custom_signals
    invalidate_custom_signals()
    return {'signals_written': sum(f == 'custom_signals' for f, _, _ in pending),
            'factors_written': sum(f == 'custom_factors' for f, _, _ in pending)}
