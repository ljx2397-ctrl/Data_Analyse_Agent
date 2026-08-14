# backend/services/telemetry.py
# 可观测性服务：运行轨迹（trace）落盘 + token 用量统计。
# - trace：每会话一个 JSONL 文件（TRACE_DIR/{sid}.jsonl），事件追加写入，
#   前端「运行轨迹」面板读取展示；事件写入失败静默，绝不影响主流程。
# - usage：每轮对话的 token 统计随消息持久化，并累计到 session.usage。
import datetime
import json
import os
import threading

from settings import TRACE_DIR

_lock = threading.Lock()
MAX_TRACE_EVENTS = 500   # 单次读取返回的最大事件数（防长会话文件过大）


def _trace_path(sid: str) -> str:
    # sid 由后端生成（时间戳格式），不含路径分隔符，无目录穿越风险
    return os.path.join(TRACE_DIR, f"{sid}.jsonl")


# ── 运行轨迹 ──────────────────────────────────────────────────────────────────

def record_event(sid: str, event_type: str, **fields) -> None:
    """追加一条 trace 事件。所有异常静默（埋点绝不能打断对话主流程）。"""
    evt = {
        "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
        "type": event_type,
        **fields,
    }
    try:
        with _lock:
            with open(_trace_path(sid), "a", encoding="utf-8") as f:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_trace(sid: str, limit: int = MAX_TRACE_EVENTS) -> list[dict]:
    """读取会话的 trace 事件（按时间正序，最多返回末尾 limit 条）。"""
    path = _trace_path(sid)
    if not os.path.exists(path):
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return []
    return events[-limit:]


def delete_trace(sid: str) -> None:
    """会话删除时清理 trace 文件。"""
    try:
        path = _trace_path(sid)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ── Token 用量 ────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（服务商不返回 usage 时的兜底）。
    中文约 1.5 字符/token，英文约 4 字符/token，取混合值 2 字符/token。
    """
    if not text:
        return 0
    return max(1, len(text) // 2)


def build_usage(input_tokens: int, output_tokens: int, model: str = "",
                duration_ms: int | None = None, estimated: bool = False) -> dict:
    """构造单轮用量对象（随 SSE done / 消息持久化传给前端）。"""
    input_tokens = int(input_tokens)
    output_tokens = int(output_tokens)
    return {
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "total_tokens":  input_tokens + output_tokens,
        "model":         model,
        "estimated":     estimated,   # True 表示为估算值（服务商未返回 usage）
        "duration_ms":   duration_ms,
    }


def accumulate_session_usage(sid: str, usage: dict, count_turn: bool = True) -> dict | None:
    """把单轮用量累计到 session.usage（会话级总计），返回累计结果。
    count_turn=False 用于系统开销（如滚动摘要），不计入对话轮数。
    """
    from services import session_store
    sess = session_store.get(sid)
    if sess is None:
        return None
    acc = sess.get("usage") or {}
    new = {
        "input_tokens":  acc.get("input_tokens", 0) + usage.get("input_tokens", 0),
        "output_tokens": acc.get("output_tokens", 0) + usage.get("output_tokens", 0),
        "turns":         acc.get("turns", 0) + (1 if count_turn else 0),
        # 只要有一轮是估算，整体就标记为估算
        "estimated":     bool(acc.get("estimated") or usage.get("estimated")),
    }
    new["total_tokens"] = new["input_tokens"] + new["output_tokens"]
    session_store.update(sid, usage=new)
    return new
