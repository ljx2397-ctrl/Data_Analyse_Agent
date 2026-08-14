# backend/api/sessions.py
# 会话 CRUD 路由。
import pandas as pd
from fastapi import APIRouter, HTTPException

from schemas import SessionOut, SessionDetail, DataOverview, TableOverview, DatasetInfo
from services import session_store, df_registry, agent_service

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _to_out(sess: dict) -> SessionOut:
    datasets = sess.get("datasets", [])
    return SessionOut(
        sid=sess["sid"],
        title=sess.get("title", "新对话"),
        df_name=sess.get("df_name"),
        df_kind=sess.get("df_kind"),
        df_columns=sess.get("df_columns"),
        df_sheet=sess.get("df_sheet"),
        has_data=bool(datasets or sess.get("df_storage_key")),
        datasets=[DatasetInfo(**{k: d[k] for k in ("id", "name", "kind", "columns", "sheet")
                                 if k in d}) for d in datasets],
        created_at=sess.get("created_at", ""),
        message_count=len(sess.get("messages", [])),
    )


def _compute_overview(df) -> DataOverview | None:
    """从 DataFrame 计算数据概览。"""
    if df is None:
        return None
    return DataOverview(
        rows=len(df),
        cols=len(df.columns),
        columns=list(df.columns),
        num_cols=df.select_dtypes(include="number").columns.tolist(),
        cat_cols=df.select_dtypes(include="object").columns.tolist(),
        date_cols=[c for c in df.columns
                   if pd.api.types.is_datetime64_any_dtype(df[c])],
    )


def _compute_table_overviews(sid: str, sess: dict) -> list[TableOverview]:
    """计算所有已加载数据集的概览。"""
    datasets = sess.get("datasets", [])
    if not datasets:
        return []
    df_registry.get_or_load_all(sid, sess)
    overviews = []
    for ds in datasets:
        df = df_registry.get(sid, ds["id"])
        if df is None:
            continue
        ov = _compute_overview(df)
        overviews.append(TableOverview(
            **ov.model_dump(),
            table_name=ds.get("name", ""),
            dataset_id=ds.get("id", ""),
        ))
    return overviews


@router.get("", response_model=list[SessionOut])
def list_sessions():
    return [_to_out(s) for s in session_store.list_all()]


@router.post("", response_model=SessionOut)
def create_session():
    sid = session_store.create()
    return _to_out(session_store.get(sid))


@router.get("/{sid}", response_model=SessionDetail)
def get_session(sid: str):
    sess = session_store.get(sid)
    if sess is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    datasets = sess.get("datasets", [])
    has_data = bool(datasets or sess.get("df_storage_key"))

    data_overview = None
    table_overviews = []
    if has_data:
        try:
            df_registry.get_or_load_all(sid, sess)
            df = df_registry.get(sid)
            data_overview = _compute_overview(df)
            table_overviews = _compute_table_overviews(sid, sess)
        except Exception:
            pass

    return SessionDetail(
        sid=sess["sid"],
        title=sess.get("title", "新对话"),
        messages=sess.get("messages", []),
        df_name=sess.get("df_name"),
        df_kind=sess.get("df_kind"),
        df_columns=sess.get("df_columns"),
        df_sheet=sess.get("df_sheet"),
        has_data=has_data,
        datasets=[DatasetInfo(**{k: d[k] for k in ("id", "name", "kind", "columns", "sheet")
                                 if k in d}) for d in datasets],
        data_overview=data_overview,
        table_overviews=table_overviews,
        created_at=sess.get("created_at", ""),
    )


@router.delete("/{sid}")
def delete_session(sid: str):
    if session_store.get(sid) is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    agent_service.reset_agent(sid)
    session_store.delete(sid)
    return {"ok": True}


@router.put("/{sid}/title")
def rename_session(sid: str, title: str):
    sess = session_store.update(sid, title=title)
    if sess is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    return {"ok": True, "title": title}


@router.post("/{sid}/switch")
def switch_session(sid: str):
    """切换会话：清空缓存的 Agent 与 lc_messages，强制下次重建。"""
    if session_store.get(sid) is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    agent_service.reset_agent(sid)
    return {"ok": True}


# ── 单条消息管理（复制/修改/删除）──────────────────────────────────────────────


@router.delete("/{sid}/messages")
def truncate_messages(sid: str, from_index: int):
    """截断消息：删除 from_index 及之后所有消息。
    用于编辑 user 消息后重发——丢弃该条及后续，重新生成。
    会清空 Agent 与 lc 缓存，下次对话从剩余消息重建上下文。
    """
    if from_index < 0:
        raise HTTPException(400, "from_index 不能为负")
    sess = session_store.truncate_messages(sid, from_index)
    if sess is None:
        raise HTTPException(404, f"会话不存在或 from_index 越界：{sid}")
    agent_service.reset_agent(sid)
    return {"ok": True, "remaining": len(sess.get("messages", []))}


@router.delete("/{sid}/messages/{index}")
def delete_message(sid: str, index: int):
    """删除单条消息（按索引）。会清空 Agent 缓存，下次对话重建上下文。"""
    sess = session_store.delete_message(sid, index)
    if sess is None:
        raise HTTPException(404, f"消息不存在：{sid}#{index}")
    agent_service.reset_agent(sid)
    return {"ok": True}
