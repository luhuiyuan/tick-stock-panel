"""Explicit opt-in research jobs; GET never creates a job or computes results."""
from __future__ import annotations

import threading
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.services.signal_evolution import PRESET, SignalEvolutionJobs

router = APIRouter(prefix='/api/signal-evolution/v1/jobs', tags=['signal-evolution'])
_lock = threading.Lock()
_JobId = Annotated[str, Path(pattern=r'^[0-9a-f]{10}$')]


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preset: Literal['mabull-amihud-v1'] = PRESET


def _jobs(request: Request) -> SignalEvolutionJobs:
    with _lock:
        if not hasattr(request.app.state, 'signal_evolution_jobs'):
            request.app.state.signal_evolution_jobs = SignalEvolutionJobs(settings.data_dir)
        return request.app.state.signal_evolution_jobs


@router.post('', status_code=202)
def create_job(body: CreateRequest, request: Request, tasks: BackgroundTasks) -> dict:
    jobs = _jobs(request)
    job_id, created, deadline = jobs.create()
    if created:
        tasks.add_task(jobs.run, job_id, deadline)
    return {'id': job_id, 'created': created, 'preset': body.preset}


@router.get('/{job_id}')
def get_job(job_id: _JobId, request: Request) -> dict:
    job = _jobs(request).store.get(job_id)
    if job is None:
        raise HTTPException(404, '研究任务不存在')
    result = job['result']
    return {**job, 'result': {k: v for k, v in result.items() if k != '_frozen_inputs'} if result else None}


@router.post('/{job_id}/cancel', status_code=202)
def cancel_job(job_id: _JobId, request: Request) -> dict:
    jobs = _jobs(request)
    if jobs.store.get(job_id) is None:
        raise HTTPException(404, '研究任务不存在')
    if not jobs.cancel(job_id):
        raise HTTPException(409, '研究任务已结束')
    return {'id': job_id, 'cancel_requested': True}
