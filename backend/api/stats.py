# backend/api/stats.py
# 可观测性路由：会话 token 用量与运行轨迹（trace）。
from fastapi import APIRouter, HTTPException

from services import session_store, telemetry

router = APIRouter(prefix="/sessions/{sid}", tags=["stats"])


@router.get("/usage")
def get_usage(sid: str):
    """会话级 token 累计用量与成本（无记录时返回零值）。"""
    if session_store.get(sid) is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    usage = session_store.get(sid).get("usage") or {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "turns": 0, "estimated": False,
    }
    return usage


@router.get("/trace")
def get_trace(sid: str, limit: int = 200):
    """会话运行轨迹事件列表（按时间正序）。"""
    if session_store.get(sid) is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    limit = max(1, min(limit, telemetry.MAX_TRACE_EVENTS))
    return {"events": telemetry.read_trace(sid, limit)}
