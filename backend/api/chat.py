# backend/api/chat.py
# SSE 流式聊天路由。
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from schemas import ChatRequest
from services import session_store, agent_service

router = APIRouter(prefix="/sessions/{sid}", tags=["chat"])


@router.post("/chat")
def chat(sid: str, body: ChatRequest):
    """流式聊天，返回 text/event-stream。"""
    if session_store.get(sid) is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    if not body.query.strip():
        raise HTTPException(400, "query 不能为空")

    def event_stream():
        try:
            for sse in agent_service.stream_chat(sid, body.query, body.skill_id, body.mode):
                yield sse
        except Exception as e:
            # 兜底：流式中途未捕获的异常
            import json
            yield (f"event: error\ndata: "
                   f"{json.dumps({'message': f'内部错误：{e}'}, ensure_ascii=False)}\n\n")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # nginx 不缓冲
            "Connection": "keep-alive",
        },
    )
