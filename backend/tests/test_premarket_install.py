"""迁移显式安装：冲突、幂等、增量、回滚和资源依赖闭包。"""

import pytest
from premarket_fixtures import (
    factors as factors,
)

from app.factors import registry  # 初始化顺序与应用一致
from app.factors.premarket import FACTORS
from app.strategy import custom_signals
from app.strategy.premarket_signal_definitions import (
    SIGNALS,
)
from app.strategy.premarket_signals import install


def test_definitions_and_install(tmp_path):
    assert len(SIGNALS) == 93
    for sig in SIGNALS:
        custom_signals.validate(sig)
    report = install(tmp_path)
    assert report['signals_written'] == 93
    assert report['factors_written'] == len(FACTORS)
    assert len(custom_signals.load_all(tmp_path)) == 93
    assert install(tmp_path)['signals_written'] == 0
    path = tmp_path / 'user_data/custom_signals/t_oversold.json'
    path.write_text('{"id":"t_oversold","name":"用户自定义"}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='冲突'):
        install(tmp_path)
    assert path.read_bytes() == before


def test_resource_export_preserves_contract_and_factor_closure(tmp_path, monkeypatch):
    import io
    import json
    import zipfile

    from app.services import resource_bundle
    install(tmp_path)
    roots = [{'kind': 'signal', 'id': sig['id']} for sig in SIGNALS]
    blob = resource_bundle.build_resources(tmp_path, None, roots)[0]
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for sig in SIGNALS:
            actual = json.loads(archive.read(f"user_data/custom_signals/{sig['id']}.json"))
            assert actual['data_contract'] == sig['data_contract']
        for factor in FACTORS:
            assert f"user_data/custom_factors/{factor['id']}.json" in archive.namelist()
    from app.config import settings
    destination = tmp_path / 'imported'
    destination.mkdir()
    monkeypatch.setattr(settings, 'data_dir', destination)
    for factor in FACTORS:
        registry.unregister_factor(factor['id'])
    preview = resource_bundle.inspect_resources([('premarket.zip', blob)])
    assert preview['roots']
    resource_bundle.apply_resources(
        [('premarket.zip', blob)], selected=preview['roots'], overwrite=[], revision=preview['revision'],
    )
    for sig in SIGNALS:
        assert custom_signals.load_one(destination, sig['id'])['data_contract'] == sig['data_contract']


@pytest.mark.parametrize('failure_id', ['t_shrink', 't_dayang_shuangxiu', 't_asc_triangle', 't_caihong_qiao', 't_long_lower_shadow', 't_breakaway_gap', 'uf_open_gap_close_move14_ratio', 'uf_open_gap08_required_ratio', 'uf_amount_8_prev8_ratio', 't_vcp_3seg8', 'uf_low5_ma20_ratio', 't_cup_handle', 't_pullback_continue', 'uf_high_spread_10_ratio', 't_sanhu_keng', 't_keng_qiao_die', 'uf_confirmed_high_peak_count59', 't_guozuofeng', 'uf_position_fixed_band', 'uf_open_prev_close_ratio', 't_weakmkt'])
def test_install_failure_rolls_back_only_new_files(tmp_path, monkeypatch, failure_id):
    from pathlib import Path
    original = Path.open
    user_file = tmp_path / 'user_data/custom_signals/user.json'
    user_file.parent.mkdir(parents=True)
    user_file.write_text('{"id":"user"}')
    def fail(path, mode='r', *args, **kwargs):
        if mode == 'x' and path.stem == failure_id:
            raise OSError('模拟磁盘错误')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail)
    with pytest.raises(OSError, match='磁盘'):
        install(tmp_path)
    assert user_file.read_text() == '{"id":"user"}'
    assert list((tmp_path / 'user_data/custom_factors').glob('*.json')) == []
    assert list(user_file.parent.glob('*.json')) == [user_file]


@pytest.mark.parametrize(('signal_count', 'factor_count'), [(8, 3), (16, 7), (24, 11), (32, 17), (36, 25), (40, 33), (44, 40), (47, 44), (50, 48), (53, 58), (55, 65), (56, 67)])
def test_incremental_install_preserves_previous_batches(tmp_path, signal_count, factor_count):
    import json
    # 模拟已有首批或前两批定义，保留既有文件字节。
    originals = {}
    for folder, definitions in [('custom_signals', SIGNALS[:signal_count]), ('custom_factors', FACTORS[:factor_count])]:
        for item in definitions:
            path = tmp_path / 'user_data' / folder / f"{item['id']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(item, ensure_ascii=False))
            originals[path] = path.read_bytes()
    assert install(tmp_path) == {'signals_written': 93 - signal_count, 'factors_written': 100 - factor_count}
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert install(tmp_path) == {'signals_written': 0, 'factors_written': 0}


def test_second_batch_conflict_preflight_writes_nothing(tmp_path):
    folder = tmp_path / 'user_data/custom_signals'
    folder.mkdir(parents=True)
    path = folder / 't_engulf.json'
    path.write_text('{"id":"t_engulf","name":"用户修改"}')
    original = path.read_bytes()
    with pytest.raises(ValueError, match='冲突'):
        install(tmp_path)
    assert path.read_bytes() == original
    assert not (tmp_path / 'user_data/custom_factors').exists()
    assert list(folder.iterdir()) == [path]


def test_definition_snapshot_unchanged():
    """锁定结构整理前的完整定义及批次顺序；业务变更需另行确认后更新指纹。"""
    import hashlib
    import json

    from app.strategy import premarket_signal_definitions as definitions

    payload = {name: getattr(definitions, name) for name in vars(definitions)
               if name.endswith('_BATCH_SIGNALS') and name not in {'EIGHTH_BATCH_SIGNALS', 'NINTH_BATCH_SIGNALS', 'TENTH_BATCH_SIGNALS', 'ELEVENTH_BATCH_SIGNALS', 'TWELFTH_BATCH_SIGNALS'}}
    payload['SIGNALS'] = SIGNALS[:44]
    payload['FACTORS'] = FACTORS[:40]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        'c71043ed74bbe0bb5a8d29f82ebdd0ea8733e3e6c8c76f52b551bbc9672acd78'
    )


def test_definition_and_installer_imports_do_not_install(tmp_path):
    """新进程验证声明模块无运行时依赖，安装模块导入不注册、不写入、不改种子。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = '''
import copy
import sys
from pathlib import Path
from app.strategy.signal_seeds import SEED_SIGNALS
seeds_before = copy.deepcopy(SEED_SIGNALS)
from app.factors.premarket import FACTORS
from app.strategy.premarket_signal_definitions import SIGNALS
assert len(FACTORS) == 100 and len(SIGNALS) == 93
for module in ('app.factors.registry', 'app.factors.store', 'app.strategy.custom_signals'):
    assert module not in sys.modules, module
from app.factors import registry
before = {f['id']: registry.get_factor(f['id']) for f in FACTORS}
from app.strategy.premarket_signals import install
assert callable(install)
assert before == {f['id']: registry.get_factor(f['id']) for f in FACTORS}
assert all(spec is None for spec in before.values())
assert SEED_SIGNALS == seeds_before
assert not ({s['id'] for s in SIGNALS} & {s['id'] for s in SEED_SIGNALS})
assert list(Path.cwd().iterdir()) == []
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_eighth_definition_contract_and_dependency_order():
    from app.strategy.premarket_signal_definitions import EIGHTH_BATCH_SIGNALS

    assert len(FACTORS[:44]) == len({f['id'] for f in FACTORS[:44]}) == 44
    assert len(SIGNALS[:47]) == len({s['id'] for s in SIGNALS[:47]}) == 47
    assert SIGNALS[44:47] == EIGHTH_BATCH_SIGNALS
    assert [s['id'] for s in EIGHTH_BATCH_SIGNALS] == [
        't_morning_star', 't_momentum_accel', 't_breakaway_gap',
    ]
    assert [f['id'] for f in FACTORS[40:44]] == [
        'uf_abs_body_close', 'uf_close_body_midpoint_lag2_ratio',
        'uf_open_gap_close_move14_ratio', 'uf_open_gap08_required_ratio',
    ]
    gap = EIGHTH_BATCH_SIGNALS[-1]
    conditions = {c['left']: (c['op'], c['right']) for c in gap['conditions']}
    assert conditions['uf_open_gap_close_move14_ratio'] == ('>', '0')
    assert conditions['uf_open_gap08_required_ratio'] == ('>=', '1')
    assert gap['data_contract'] == {
        'version': 1, 'inputs': {'open': 2, 'high': 2, 'low': 2, 'close': 15, 'amount': 2},
        'ohlc_window': 2,
    }
    for sig in EIGHTH_BATCH_SIGNALS:
        assert len(sig['conditions']) <= 8
        custom_signals.validate(sig)


def test_eighth_snapshot_unchanged_after_ninth_batch():
    import hashlib
    import json

    from app.strategy import premarket_signal_definitions as definitions

    payload = {name: getattr(definitions, name) for name in vars(definitions)
               if name.endswith('_BATCH_SIGNALS') and name not in {'NINTH_BATCH_SIGNALS', 'TENTH_BATCH_SIGNALS', 'ELEVENTH_BATCH_SIGNALS', 'TWELFTH_BATCH_SIGNALS'}}
    payload['SIGNALS'] = SIGNALS[:47]
    payload['FACTORS'] = FACTORS[:44]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        'd4d5296a272a26242227b2656fa91225b61345bbe3cfb434e02c55bfdd841292'
    )


def test_ninth_unique_ids_contracts_and_condition_budget():
    from app.strategy.premarket_signal_definitions import NINTH_BATCH_SIGNALS

    assert len(SIGNALS[:50]) == len({s['id'] for s in SIGNALS[:50]}) == 50
    assert len(FACTORS[:48]) == len({f['id'] for f in FACTORS[:48]}) == 48
    assert SIGNALS[47:50] == NINTH_BATCH_SIGNALS
    assert [s['id'] for s in NINTH_BATCH_SIGNALS] == [
        't_amp_mean_compress', 't_4yang1yin', 't_vcp_3seg8',
    ]
    assert 't_vcp' not in {s['id'] for s in SIGNALS}
    assert [f['id'] for f in FACTORS[44:48]] == [
        'uf_intraday_range_mean5_20_ratio', 'uf_amount_current_mean5_ratio',
        'uf_range_8d_low', 'uf_amount_8_prev8_ratio',
    ]
    for sig, n in zip(NINTH_BATCH_SIGNALS, [20, 5, 24], strict=True):
        assert len(sig['conditions']) <= 8
        assert sig['data_contract']['ohlc_window'] == n
        assert set(sig['data_contract']['inputs'].values()) == {n}
        custom_signals.validate(sig)


@pytest.mark.parametrize('sid', ['t_amp_mean_compress', 't_4yang1yin', 't_vcp_3seg8'])
def test_ninth_conflict_does_not_overwrite_or_install(tmp_path, sid):
    folder = tmp_path / 'user_data/custom_signals'
    folder.mkdir(parents=True)
    path = folder / f'{sid}.json'
    path.write_text('{"id":"user-owned-conflict"}')
    original = path.read_bytes()
    with pytest.raises(ValueError, match='冲突'):
        install(tmp_path)
    assert path.read_bytes() == original
    assert list(folder.iterdir()) == [path]
    assert not (tmp_path / 'user_data/custom_factors').exists()


def test_ninth_snapshot_unchanged_after_tenth_batch():
    import hashlib
    import json

    from app.strategy import premarket_signal_definitions as definitions
    payload = {name: getattr(definitions, name) for name in vars(definitions)
               if name.endswith('_BATCH_SIGNALS') and name not in {'TENTH_BATCH_SIGNALS', 'ELEVENTH_BATCH_SIGNALS', 'TWELFTH_BATCH_SIGNALS'}}
    payload.update(SIGNALS=SIGNALS[:50], FACTORS=FACTORS[:48])
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        '651e371619dbbbdea029761b4daa37ded703ee954d3b78abffe5a6c0058b83e5'
    )


def test_tenth_definition_contracts_and_budget():
    from app.strategy.premarket_signal_definitions import TENTH_BATCH_SIGNALS
    assert SIGNALS[50:53] == TENTH_BATCH_SIGNALS
    assert len(SIGNALS[:53]) == len({s['id'] for s in SIGNALS[:53]}) == 53
    assert len(FACTORS[:58]) == len({f['id'] for f in FACTORS[:58]}) == 58
    assert [s['id'] for s in TENTH_BATCH_SIGNALS] == [
        't_qixing_zhengli', 't_cup_handle', 't_pullback_continue',
    ]
    cup = TENTH_BATCH_SIGNALS[1]
    assert [(c['left'], c['op'], c['right']) for c in cup['conditions'][:2]] == [
        ('uf_high10_lag20_low12_lag8_ratio', '>', str(10 / 9)),
        ('uf_high5_lag3_low12_lag8_ratio', '>', str(10 / 9)),
    ]
    assert not ({'uf_low12_lag8_high10_lag20_ratio', 'uf_low12_lag8_high5_lag3_ratio'}
                & {f['id'] for f in FACTORS})
    for sig, window, amount, count in zip(TENTH_BATCH_SIGNALS, [21,30,25], [20,None,2], [5,7,5], strict=True):
        inputs = dict.fromkeys(['open', 'high', 'low', 'close'], window)
        if amount is not None:
            inputs['amount'] = amount
        assert sig['data_contract'] == {'version': 1, 'inputs': inputs, 'ohlc_window': window}
        assert len(sig['conditions']) == count <= 8
        custom_signals.validate(sig)


@pytest.mark.parametrize('fid', ['t_qixing_zhengli', 't_cup_handle', 't_pullback_continue',
                                  'uf_close_pole_midpoint_ratio', 'uf_low5_ma20_ratio'])
def test_tenth_conflicts_preserve_user_files(tmp_path, fid):
    folder = tmp_path / 'user_data' / ('custom_factors' if fid.startswith('uf_') else 'custom_signals')
    folder.mkdir(parents=True)
    path = folder / f'{fid}.json'
    path.write_text('{"id":"user-owned-conflict"}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='冲突'):
        install(tmp_path)
    assert path.read_bytes() == before
    assert list((tmp_path / 'user_data').rglob('*.json')) == [path]


def test_tenth_snapshot_unchanged_after_eleventh_batch():
    import hashlib
    import json

    from app.strategy import premarket_signal_definitions as definitions
    payload={name:getattr(definitions,name) for name in vars(definitions)
             if name.endswith('_BATCH_SIGNALS') and name not in {'ELEVENTH_BATCH_SIGNALS', 'TWELFTH_BATCH_SIGNALS'}}
    payload.update(SIGNALS=SIGNALS[:53],FACTORS=FACTORS[:58])
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    assert hashlib.sha256(canonical.encode()).hexdigest()==(
        '80b80357847021ab587e9b3cf46aa6994867076e44e1b4580d5c3bdf42cd4aa2'
    )


def test_eleventh_definition_contracts_and_offsets():
    from app.strategy.premarket_signal_definitions import ELEVENTH_BATCH_SIGNALS
    assert SIGNALS[53:55]==ELEVENTH_BATCH_SIGNALS
    assert len(SIGNALS[:55])==len({s['id'] for s in SIGNALS[:55]})==55
    assert len(FACTORS[:65])==len({f['id'] for f in FACTORS[:65]})==65
    assert [s['id'] for s in ELEVENTH_BATCH_SIGNALS]==['t_sanhu_keng','t_keng_qiao_die']
    for sig,n in zip(ELEVENTH_BATCH_SIGNALS,[21,39],strict=True):
        assert sig['data_contract']=={'version':1,'inputs':dict.fromkeys(['open','high','low','close','amount'],n),'ohlc_window':n}
        assert len(sig['conditions'])==4
        custom_signals.validate(sig)
    assert ELEVENTH_BATCH_SIGNALS[0]['conditions'][0]=={
        'left':'uf_high_low_12_ratio','op':'<=','right':'1.12','leftDays':9,'rightDays':0,
    }
    for index,condition in [(0,1),(1,0)]:
        c=ELEVENTH_BATCH_SIGNALS[index]['conditions'][condition]
        assert (c['op'],c['right'])==('>',str(100/99))


@pytest.mark.parametrize('fid',['t_sanhu_keng','t_keng_qiao_die','uf_high_low_12_ratio','uf_high_spread_10_ratio'])
def test_eleventh_conflicts_do_not_overwrite(tmp_path,fid):
    folder=tmp_path/'user_data'/('custom_factors' if fid.startswith('uf_') else 'custom_signals')
    folder.mkdir(parents=True)
    path=folder/f'{fid}.json'
    path.write_text('{"id":"user-owned-conflict"}')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='冲突'):
        install(tmp_path)
    assert path.read_bytes()==before
    assert list((tmp_path/'user_data').rglob('*.json'))==[path]


def test_eleventh_snapshot_unchanged_after_twelfth_batch():
    import hashlib
    import json

    from app.strategy import premarket_signal_definitions as definitions
    payload={name:getattr(definitions,name) for name in vars(definitions)
             if name.endswith('_BATCH_SIGNALS') and name!='TWELFTH_BATCH_SIGNALS'}
    payload.update(SIGNALS=SIGNALS[:55],FACTORS=FACTORS[:65])
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    assert hashlib.sha256(canonical.encode()).hexdigest()==(
        'b9d8fe20ca0ce9c52e2531e750ed5b68391183351b7ad34d119027537ba934a4'
    )


def test_twelfth_definition_contract_and_local_threshold():
    import math

    from app.strategy.premarket_signal_definitions import TWELFTH_BATCH_SIGNALS
    assert SIGNALS[55:56]==TWELFTH_BATCH_SIGNALS
    assert len(SIGNALS)==len({s['id'] for s in SIGNALS})==93
    assert len(FACTORS)==len({f['id'] for f in FACTORS})==100
    sig=TWELFTH_BATCH_SIGNALS[0]
    assert sig['id']=='t_guozuofeng'
    assert sig['data_contract']=={'version':1,'inputs':dict.fromkeys(['open','high','low','close'],61),'ohlc_window':61}
    assert len(sig['conditions'])==2
    assert [(c['left'],c['op'],c['right']) for c in sig['conditions']]==[
        ('uf_close_prev60_high_ratio','>',str(1.01+8*math.ulp(1.)*1.01)),
        ('uf_confirmed_high_peak_count59','>=','1'),
    ]
    assert [f['id'] for f in FACTORS[65:67]]==['uf_close_prev60_high_ratio','uf_confirmed_high_peak_count59']
    custom_signals.validate(sig)


@pytest.mark.parametrize('fid',['t_guozuofeng','uf_close_prev60_high_ratio','uf_confirmed_high_peak_count59'])
def test_twelfth_conflicts_do_not_overwrite(tmp_path,fid):
    folder=tmp_path/'user_data'/('custom_factors' if fid.startswith('uf_') else 'custom_signals')
    folder.mkdir(parents=True)
    path=folder/f'{fid}.json'
    path.write_text('{"id":"user-owned-conflict"}')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='冲突'):
        install(tmp_path)
    assert path.read_bytes()==before
    assert list((tmp_path/'user_data').rglob('*.json'))==[path]


def test_original_56_signals_and_67_factors_unchanged():
    import hashlib
    import json

    from app.strategy import premarket_signal_definitions as definitions
    payload = {name: getattr(definitions, name) for name in vars(definitions)
               if name.endswith('_BATCH_SIGNALS')}
    payload.update(SIGNALS=SIGNALS[:56], FACTORS=FACTORS[:67])
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        '355c964020db8dd32640acadf6bbc03d651bb77ad4a6174139a53f2823df17aa')


@pytest.mark.parametrize('fid', ['uf_position_fixed_band', 'uf_open_prev_close_ratio', 't_weakmkt', 't_niugu_sanjue'])
def test_final_conflicts_do_not_overwrite(tmp_path, fid):
    folder = tmp_path / 'user_data' / ('custom_factors' if fid.startswith('uf_') else 'custom_signals')
    folder.mkdir(parents=True)
    path = folder / f'{fid}.json'
    path.write_text('{"id":"user-owned-conflict"}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='冲突'):
        install(tmp_path)
    assert path.read_bytes() == before
    assert list((tmp_path / 'user_data').rglob('*.json')) == [path]
