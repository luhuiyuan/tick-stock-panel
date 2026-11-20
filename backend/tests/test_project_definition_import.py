"""随版本发布的定义只显式追加，验证真实快照及冲突/回滚路径。"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.definitions import router
from app.config import settings
from app.factors.registry import get_factor, isolated_registry
from app.services import resource_bundle
from app.strategy.custom_signals import seed_if_empty


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert seed_if_empty(tmp_path) == 20
    app = FastAPI()
    app.include_router(router)
    with isolated_registry(), TestClient(app) as client:
        yield tmp_path, client


def get_preview(client):
    response = client.get("/api/definitions/resources/project")
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, preview, kind, *ids):
    return client.post("/api/definitions/resources/project", json={
        "kind": kind, "ids": ids, "revision": preview["revision"],
    })


def entries(preview, kind, action):
    return [item for item in preview["entries"] if item["kind"] == kind and item["action"] == action]


def test_fresh_install_full_snapshot_and_repeat(project):
    tmp, client = project
    preview = get_preview(client)
    assert len(preview["entries"]) == 213
    assert len(entries(preview, "signal", "reuse")) == 20
    missing_signals = entries(preview, "signal", "create")
    missing_factors = entries(preview, "factor", "create")
    assert len(missing_signals) == 93 and len(missing_factors) == 100
    assert not any(e["issues"] for e in preview["entries"])
    assert not list((tmp / "user_data/custom_factors").glob("*.json"))
    seeds = {p: p.read_bytes() for p in (tmp / "user_data/custom_signals").glob("*.json")}
    response = apply(client, preview, "factor", *(e["id"] for e in missing_factors))
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 100
    assert all(get_factor(e["id"]) is not None for e in missing_factors)
    latest = get_preview(client)
    response = apply(client, latest, "signal", *(e["id"] for e in missing_signals))
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 93
    complete = get_preview(client)
    assert all(e["action"] == "reuse" for e in complete["entries"])
    assert all(p.read_bytes() == payload for p, payload in seeds.items())
    assert apply(client, latest, "signal", "t_smallyang").status_code == 400  # stale preview
    assert apply(client, complete, "signal", "t_smallyang").status_code == 400  # already present


def test_signal_import_includes_factors_and_calls_cache_refresh(project, monkeypatch):
    tmp, client = project
    from app.indicators import pipeline

    invalidated = []
    monkeypatch.setattr(pipeline, "invalidate_custom_signals", lambda: invalidated.append(True))
    preview = get_preview(client)
    selected = next(e for e in preview["entries"] if e["id"] == "t_smallyang")
    assert set(selected["required"]) == {"factor:uf_abs_return_1d", "factor:uf_yang_count_5"}
    result = apply(client, preview, "signal", "t_smallyang")
    assert result.status_code == 200, result.text
    assert result.json()["imported"] == 3
    assert get_factor("uf_yang_count_5") is not None
    assert (tmp / "user_data/custom_signals/t_smallyang.json").is_file()
    assert invalidated == [True]


def test_factor_conflict_blocks_dependent_signal(project):
    tmp, client = project
    preview = get_preview(client)
    path = tmp / "user_data/custom_factors/uf_yang_count_5.json"
    path.parent.mkdir(exist_ok=True)
    original = (resource_bundle._PROJECT_SNAPSHOT / "custom_factors" / path.name).read_bytes()
    local = original.replace(b'"formula":', b'"local_metadata": true, "formula":')
    assert local != original
    path.write_bytes(local)
    assert apply(client, preview, "signal", "t_smallyang").status_code == 400
    latest = get_preview(client)
    assert [e["id"] for e in entries(latest, "factor", "conflict")] == [path.stem]
    selected = next(e for e in latest["entries"] if e["id"] == "t_smallyang")
    assert any("同 ID 冲突" in issue for issue in selected["issues"])
    assert apply(client, latest, "signal", "t_smallyang").status_code == 400
    assert apply(client, latest, "factor", path.stem).status_code == 400
    assert path.read_bytes() == local
    assert not (tmp / "user_data/custom_signals/t_smallyang.json").exists()


def test_race_never_overwrites_and_rolls_back_new_files(project, monkeypatch):
    tmp, client = project
    preview = get_preview(client)
    create = resource_bundle._create_payload
    preserved = b'{"different": true}'

    def create_with_race(path: Path, payload: bytes):
        if path.name == "t_smallyang.json":
            path.write_bytes(preserved)
        create(path, payload)

    monkeypatch.setattr(resource_bundle, "_create_payload", create_with_race)
    result = apply(client, preview, "signal", "t_smallyang")
    assert result.status_code == 400 and "已回滚" in result.text
    assert not list((tmp / "user_data/custom_factors").iterdir())
    assert get_factor("uf_yang_count_5") is None
    assert (tmp / "user_data/custom_signals/t_smallyang.json").read_bytes() == preserved
