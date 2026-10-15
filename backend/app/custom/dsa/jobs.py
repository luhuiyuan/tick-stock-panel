"""通过外部 DSA HTTP 服务执行 TSP 侧的 ETF 轮动入口。"""
from __future__ import annotations

import json

from app.custom.dsa.etf_job import TOKEN_MISSING_DETAIL, configured_token, token_usable
from app.custom.dsa.proxy import UpstreamError, enabled, forward

_SIDECAR_TIMEOUT = 200.0


def etf_rotation_command() -> str:
    return "python main.py --etf-rotation --no-notify"


def run_etf_rotation() -> dict:
    """Ask the configured external DSA service to execute its fixed ETF command."""
    command = etf_rotation_command()
    if not enabled():
        return {
            "ok": False,
            "detail": "未配置 DSA_BASE_URL。请部署并配置外部 DSA 服务，再设置 DSA_BASE_URL。",
            "command": command,
        }
    if not token_usable(configured_token()):
        return {"ok": False, "detail": TOKEN_MISSING_DETAIL, "command": command}
    try:
        status, payload, _media, _extra = forward(
            "POST", "tsp/etf-rotation", timeout=_SIDECAR_TIMEOUT,
        )
    except UpstreamError as exc:
        return {"ok": False, "detail": str(exc), "command": command}
    return _parse_sidecar_payload(status, payload, command)


def _parse_sidecar_payload(status: int, payload: bytes, command: str) -> dict:
    text = payload.decode("utf-8", errors="replace")
    try:
        body = json.loads(text) if text else {}
    except json.JSONDecodeError:
        body = {}
    if isinstance(body, dict) and "ok" in body:
        parsed: dict = {
            "ok": bool(body.get("ok")),
            "detail": str(body.get("detail") or "")[-8000:],
            "command": command,
        }
        if "code" in body:
            parsed["code"] = body.get("code")
        if isinstance(body.get("result"), dict):
            parsed["result"] = body["result"]
        return parsed
    if status == 404:
        return {
            "ok": False,
            "detail": "外部 DSA 服务没有 TSP ETF 轮动入口，请部署与当前集成版本匹配的 DSA 适配器。",
            "command": command,
        }
    snippet = text.strip()[-500:]
    return {"ok": False, "detail": snippet or f"HTTP {status}", "command": command}
