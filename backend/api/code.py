# backend/api/code.py
# 代码重新执行路由。
from fastapi import APIRouter, HTTPException

from schemas import CodeRunRequest, CodeRunResponse
from services import session_store, agent_service

router = APIRouter(prefix="/sessions/{sid}", tags=["code"])


@router.post("/code/run", response_model=CodeRunResponse)
def run_code(sid: str, body: CodeRunRequest):
    """重新执行用户编辑后的分析代码，返回 Markdown 结果。"""
    if session_store.get(sid) is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    if not body.code.strip():
        raise HTTPException(400, "code 不能为空")
    result = agent_service.run_code(sid, body.code)
    return CodeRunResponse(result=result)
