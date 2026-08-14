# backend/api/upload.py
# 文件上传流程：上传数据文件 → 列 Sheet → LLM 解析字段 → 确认导入。
# 支持多表：会话中可追加多个数据文件，通过 pending_dataset 机制管理待确认的上传。
from fastapi import APIRouter, HTTPException, UploadFile, File

from schemas import (
    UploadDataResponse, ParseHeaderResponse, ConfirmDataRequest,
    ConfirmDataResponse, DataOverview, TableOverview, DatasetInfo,
)
from services import session_store, df_registry, agent_service
from services.storage import get_storage
from core.data_loader import detect_file_kind, list_excel_sheets, llm_parse_header_file
from core.tools.analyse_tool import set_df_accessor as _set_at

router = APIRouter(prefix="/sessions/{sid}", tags=["upload"])


@router.post("/upload-data", response_model=UploadDataResponse)
async def upload_data(sid: str, file: UploadFile = File(...)):
    """上传数据文件，存原始字节到对象存储，返回文件类型与 Sheet 列表。
    支持多表：已有数据时追加新表，否则作为第一张表。
    """
    sess = session_store.get(sid)
    if sess is None:
        raise HTTPException(404, f"会话不存在：{sid}")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")

    kind = detect_file_kind(file.filename, raw)
    if kind == "xls":
        raise HTTPException(400, "暂不支持旧版 .xls，请在 Excel 中另存为 .xlsx 后重传。")

    # 存原始字节到对象存储（先创建 pending 以拿到唯一 ds_id，再用它生成 storage_key）
    pending = session_store.set_pending_dataset(
        sid, name=file.filename, kind=kind,
        storage_key=f"sessions/{sid}/pending_tmp.bin",
    )
    # 每个 dataset 用独立的 storage_key，避免多次上传互相覆盖
    final_storage_key = f"sessions/{sid}/data_{pending['id']}.bin"
    session_store.update_pending_dataset(sid, storage_key=final_storage_key)
    get_storage().put(final_storage_key, raw)

    sheets = None
    if kind == "xlsx":
        try:
            sheets = list_excel_sheets(raw)
        except Exception as e:
            raise HTTPException(400, f"读取 Excel Sheet 失败：{e}")

    # 清空已加载的 df 缓存与 Agent 缓存（数据已变更）
    df_registry.remove(sid)
    agent_service.reset_agent(sid)

    return UploadDataResponse(kind=kind, name=file.filename, sheets=sheets)


@router.post("/parse-header", response_model=ParseHeaderResponse)
async def parse_header(sid: str, file: UploadFile = File(...)):
    """LLM 解析字段说明文件，返回字段名列表。"""
    sess = session_store.get(sid)
    if sess is None:
        raise HTTPException(404, f"会话不存在：{sid}")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "字段说明文件为空")

    columns = llm_parse_header_file(raw)
    if not columns:
        raise HTTPException(422, "大模型解析失败，请手动填写字段名或检查文件内容。")
    return ParseHeaderResponse(columns=columns)


def _compute_overview(df) -> DataOverview:
    import pandas as pd
    return DataOverview(
        rows=len(df),
        cols=len(df.columns),
        columns=list(df.columns),
        num_cols=df.select_dtypes(include="number").columns.tolist(),
        cat_cols=df.select_dtypes(include="object").columns.tolist(),
        date_cols=[c for c in df.columns
                   if pd.api.types.is_datetime64_any_dtype(df[c])],
    )


@router.post("/confirm-data", response_model=ConfirmDataResponse)
def confirm_data(sid: str, body: ConfirmDataRequest):
    """确认导入：按 sheet/columns 正式加载 DataFrame 并注册。
    支持多表：从 pending_dataset 读取待确认的数据集，加载后加入 datasets 列表。
    """
    sess = session_store.get(sid)
    if sess is None:
        raise HTTPException(404, f"会话不存在：{sid}")

    pending = session_store.get_pending_dataset(sid)
    if not pending:
        raise HTTPException(400, "没有待确认的数据文件，请先上传。")

    # 添加到 datasets 列表
    ds = session_store.add_dataset(
        sid,
        ds_id=pending["id"],
        name=pending["name"],
        kind=pending["kind"],
        columns=body.columns,
        sheet=body.sheet,
        storage_key=pending["storage_key"],
    )

    # 清空旧 df 缓存与 Agent 缓存并重新加载所有
    df_registry.remove(sid)
    agent_service.reset_agent(sid)
    sess = session_store.get(sid)

    # 加载所有数据集
    df_registry.get_or_load_all(sid, sess)
    df = df_registry.get(sid)
    if df is None:
        raise HTTPException(500, "DataFrame 加载失败")

    overview = _compute_overview(df)

    # 计算所有表的概览
    datasets = sess.get("datasets", [])
    table_overviews = []
    for ds_meta in datasets:
        ds_df = df_registry.get(sid, ds_meta["id"])
        if ds_df is not None:
            ov = _compute_overview(ds_df)
            table_overviews.append(TableOverview(
                **ov.model_dump(),
                table_name=ds_meta["name"],
                dataset_id=ds_meta["id"],
            ))

    ds_infos = [DatasetInfo(**{k: ds_meta[k] for k in ("id", "name", "kind", "columns", "sheet")
                               if k in ds_meta})
                for ds_meta in datasets]

    return ConfirmDataResponse(
        overview=overview,
        datasets=ds_infos,
        table_overviews=table_overviews,
    )


@router.delete("/datasets/{ds_id}", response_model=ConfirmDataResponse)
def remove_dataset(sid: str, ds_id: str):
    """删除指定的副表（主表不可删除）。
    清理对象存储文件、df 缓存与 Agent 缓存，返回更新后的概览。
    """
    sess = session_store.remove_dataset(sid, ds_id)
    if sess is None:
        raise HTTPException(400, "无法删除：数据集不存在或为主表（主表不可删除）")

    # 清空 df 缓存与 Agent 缓存并重新加载
    df_registry.remove(sid)
    agent_service.reset_agent(sid)
    sess = session_store.get(sid)

    df_registry.get_or_load_all(sid, sess)
    df = df_registry.get(sid)
    if df is None:
        raise HTTPException(500, "DataFrame 加载失败")

    overview = _compute_overview(df)

    datasets = sess.get("datasets", [])
    table_overviews = []
    for ds_meta in datasets:
        ds_df = df_registry.get(sid, ds_meta["id"])
        if ds_df is not None:
            ov = _compute_overview(ds_df)
            table_overviews.append(TableOverview(
                **ov.model_dump(),
                table_name=ds_meta["name"],
                dataset_id=ds_meta["id"],
            ))

    ds_infos = [DatasetInfo(**{k: ds_meta[k] for k in ("id", "name", "kind", "columns", "sheet")
                               if k in ds_meta})
                for ds_meta in datasets]

    return ConfirmDataResponse(
        overview=overview,
        datasets=ds_infos,
        table_overviews=table_overviews,
    )
