import json
import time
from datetime import timedelta
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.signal_evolution import router
from app.enriched_generation import EnrichedGenerationSnapshot
from app.factors.registry import get_factor, registry_cache_revision
from app.services import signal_evolution as evolution


@pytest.fixture
def cached_research(tmp_path, monkeypatch):
    days = [evolution.START + timedelta(days=i) for i in range(-30, 60)]
    monkeypatch.setattr(evolution, 'END', days[-1])
    (tmp_path / 'trading_calendar.json').write_text(json.dumps([d.isoformat() for d in days]), encoding='utf-8')
    monkeypatch.setattr(evolution, 'get_enriched_generation_snapshot',
                        lambda *_args, **_kwargs: EnrichedGenerationSnapshot('a', 'a'))
    root = tmp_path / 'kline_daily_enriched'
    for i, day in enumerate(days):
        path = root / f'date={day.isoformat()}'
        path.mkdir(parents=True)
        pl.DataFrame({'symbol': [evolution.SYMBOLS[0]], 'date': [day], 'close': [100.0 + i * i / 100],
                      'amount': [0.0 if i == 29 else 2e8 + i * 1000]}).write_parquet(path / 'part.parquet')
    return tmp_path, days


def test_preset_isolated_snapshot_and_prefix_causality(cached_research):
    path, days = cached_research
    revision = registry_cache_revision(), get_factor(evolution.FACTOR_ID)
    one = evolution.run_preset(path, checkpoint=lambda: None, progress=lambda *_: None)
    two = evolution.run_preset(path, checkpoint=lambda: None, progress=lambda *_: None)
    assert one == two
    assert one['snapshot']['generation'] == 'a'
    assert one['attempted'] == 17
    assert not (path / 'user_data').exists()
    assert revision == (registry_cache_revision(), get_factor(evolution.FACTOR_ID))
    raw = pl.DataFrame(one['_frozen_inputs']['rows']).with_columns(pl.col('date').str.to_date())
    definition = one['_frozen_inputs']['definition']
    days = sorted(set(raw['date'].to_list()))
    full = evolution.materialize_features(raw, days, [evolution.SYMBOLS[0]], definition)
    end = days[38]
    prefix = evolution.materialize_features(raw.filter(pl.col('date') <= end), days[:39],
                                            [evolution.SYMBOLS[0]], definition)
    assert prefix.equals(full.filter(pl.col('date') <= end))
    assert full.filter(pl.col('date') == evolution.START)['factor'].item() is None


def test_definition_conflict_and_generation_change(cached_research, monkeypatch):
    path, _ = cached_research
    definition = path / 'user_data' / 'custom_signals' / 't_mabull.json'
    definition.parent.mkdir(parents=True)
    definition.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='冲突'):
        evolution.run_preset(path, checkpoint=lambda: None, progress=lambda *_: None)
    definition.unlink()
    snapshots = iter([EnrichedGenerationSnapshot('a', 'a'), EnrichedGenerationSnapshot('b', 'b')])
    monkeypatch.setattr(evolution, 'get_enriched_generation_snapshot', lambda *_a, **_kw: next(snapshots))
    with pytest.raises(ValueError, match='代次'):
        evolution.run_preset(path, checkpoint=lambda: None, progress=lambda *_: None)


def _local_capacity(jid, fn, *, store, deadline):
    store.start(jid)
    return fn()


def test_job_api_and_snapshot_persistence(cached_research, monkeypatch):
    path, _ = cached_research
    monkeypatch.setattr(evolution, 'run_with_capacity', _local_capacity)
    app = FastAPI()
    app.include_router(router)
    app.state.signal_evolution_jobs = evolution.SignalEvolutionJobs(path)
    client = TestClient(app)
    endpoint = '/api/signal-evolution/v1/jobs'
    assert client.get(f'{endpoint}/0123456789').status_code == 404
    assert client.get(f'{endpoint}/bad-id').status_code == 422
    assert client.post(endpoint, json={'preset': 'not-supported'}).status_code == 422
    created = client.post(endpoint, json={'preset': evolution.PRESET})
    assert created.status_code == 202
    jid = created.json()['id']
    job = client.get(f'{endpoint}/{jid}').json()
    assert job['status'] == 'succeeded'
    assert '_frozen_inputs' not in job['result']
    saved = evolution.SignalEvolutionJobs(path).store.get(jid)
    assert saved['result']['_frozen_inputs']['rows']
    assert client.post(f'{endpoint}/{jid}/cancel').status_code == 409


def test_single_flight_cancel_timeout_and_no_partial_success(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution, 'run_with_capacity', _local_capacity)
    monkeypatch.setattr(evolution, 'run_preset', lambda *_args, **_kwargs: {'snapshot': 'complete'})
    jobs = evolution.SignalEvolutionJobs(tmp_path)
    jid, created, deadline = jobs.create()
    assert created and jobs.create()[:2] == (jid, False)
    assert jobs.cancel(jid)
    jobs.run(jid, deadline)
    failed = jobs.store.get(jid)
    assert failed['status'] == 'failed' and failed['result'] is None and '取消' in failed['error']
    jid, _, _ = jobs.create()
    jobs.run(jid, time.monotonic() - 1)
    assert '时限' in jobs.store.get(jid)['error']
    jid, _, deadline = jobs.create()
    writer = jobs.store._write_file

    def fail_success(job, *, required=False):
        if required:
            raise OSError('test storage unavailable')
        writer(job)

    monkeypatch.setattr(jobs.store, '_write_file', fail_success)
    jobs.run(jid, deadline)
    failed = jobs.store.get(jid)
    assert failed['status'] == 'failed' and failed['result'] is None


def test_api_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution, 'run_with_capacity', _local_capacity)
    app = FastAPI(state=SimpleNamespace())
    app.include_router(router)
    app.state.signal_evolution_jobs = evolution.SignalEvolutionJobs(tmp_path)
    client = TestClient(app)
    endpoint = '/api/signal-evolution/v1/jobs'
    job_id = client.post(endpoint, json={}).json()['id']
    job = client.get(f'{endpoint}/{job_id}').json()
    assert job['status'] == 'failed' and '日历' in job['error'] and job['result'] is None
