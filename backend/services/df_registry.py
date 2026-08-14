# backend/services/df_registry.py
# 进程内 session_id → {ds_id: DataFrame} 注册表（支持多表）。
# Agent 工具通过 _df_getter 拿当前会话的主 DataFrame（第一个）；
# 通过 _dfs_getter 拿全部 DataFrame 的字典 {表名: df}，用于多表 join。
# df 体积大，不放 Redis，而是放进程内存；df 原始字节放对象存储，按需加载。
import os
import threading
import pandas as pd

from core.data_loader import load_dataframe, load_excel
from services.storage import get_storage


# sid -> {ds_id: DataFrame}
_registry: dict[str, dict[str, pd.DataFrame]] = {}
# sid -> 上一次 analyse_tool 的完整 result（截断前的 DataFrame），
# 供 excel_tool / chart_tool 通过 use_last_result 复用，避免重复计算。
_last_result: dict[str, object] = {}
# sid -> {name: result}：命名 result 寄存器，供多 Sheet Excel / 多图场景按名取用。
# analyse_tool 通过 result_name 参数命名；excel_tool / chart_tool 通过 result_name 取用。
_named_results: dict[str, dict[str, object]] = {}
_lock = threading.Lock()


def _table_name(filename: str, index: int) -> str:
    """从文件名生成表名：去掉扩展名，冲突时加序号。"""
    base = os.path.splitext(os.path.basename(filename or f"table{index}"))[0]
    return base or f"table{index}"


def register(sid: str, ds_id: str, df: pd.DataFrame) -> None:
    """把已加载的 DataFrame 放入注册表（按 ds_id 区分）。"""
    with _lock:
        _registry.setdefault(sid, {})[ds_id] = df


def get(sid: str, ds_id: str | None = None) -> pd.DataFrame | None:
    """获取指定数据集的 df；ds_id=None 时返回主表（第一个）。"""
    with _lock:
        tables = _registry.get(sid, {})
        if not tables:
            return None
        if ds_id is not None:
            return tables.get(ds_id)
        # 主表 = 第一个注册的
        return next(iter(tables.values()))


def get_all(sid: str) -> dict[str, pd.DataFrame]:
    """返回当前会话所有已加载的 DataFrame，key 为 ds_id。"""
    with _lock:
        return dict(_registry.get(sid, {}))


def get_all_named(sid: str, datasets: list[dict]) -> dict[str, pd.DataFrame]:
    """返回 {表名: DataFrame}，表名取自文件名（去扩展名）。
    datasets 为 session_store 的 datasets 元数据列表，用于映射 ds_id→表名。
    """
    tables = get_all(sid)
    if not tables:
        return {}
    named = {}
    used_names = set()
    for i, ds in enumerate(datasets):
        ds_id = ds["id"]
        df = tables.get(ds_id)
        if df is None:
            continue
        name = _table_name(ds.get("name", f"table{i+1}"), i + 1)
        # 处理重名
        if name in used_names:
            name = f"{name}_{ds_id}"
        used_names.add(name)
        named[name] = df
    return named


def remove(sid: str) -> None:
    """移除会话的所有 df 缓存。"""
    with _lock:
        _registry.pop(sid, None)
        _last_result.pop(sid, None)
        _named_results.pop(sid, None)


def make_getter(sid: str):
    """返回闭包，供 agent 工具作为 _df_accessor 使用（返回主表）。"""
    def _getter():
        return get(sid)
    return _getter


def make_all_getter(sid: str, datasets: list[dict]):
    """返回无参闭包，供 agent 工具作为 _dfs_accessor 使用（返回 {表名: df} 字典）。
    datasets 在创建闭包时传入（快照），确保 Agent 生命周期内表名映射稳定。
    """
    def _getter():
        return get_all_named(sid, datasets)
    return _getter


# ── 分析结果寄存器（供 excel_tool / chart_tool 复用，避免重复计算）─────────────
# 两套机制并存：
# 1. last_result：analyse_tool 自动缓存最后一次 result，适合"对刚才那个结果画图/导出"
# 2. named_results：analyse_tool 通过 result_name 命名缓存，适合多表场景按名取用

def set_last_result(sid: str, result) -> None:
    """缓存一次 analyse_tool 的完整 result（截断前）。
    result 可以是 DataFrame / 标量 / dict，但只有 DataFrame 会被后续工具复用。
    """
    with _lock:
        _last_result[sid] = result


def get_last_result(sid: str):
    """读取上次 analyse_tool 的 result；不存在返回 None。"""
    with _lock:
        return _last_result.get(sid)


def set_named_result(sid: str, name: str, result) -> None:
    """缓存一个命名 result。name 由 LLM 在 analyse_tool 中通过 result_name 指定。"""
    with _lock:
        _named_results.setdefault(sid, {})[name] = result


def get_named_result(sid: str, name: str):
    """读取命名 result；不存在返回 None。"""
    with _lock:
        return _named_results.get(sid, {}).get(name)


def list_named_results(sid: str) -> list[str]:
    """列出当前会话所有已命名的 result 名（供 LLM 参考）。"""
    with _lock:
        return list(_named_results.get(sid, {}).keys())


def _load_one(ds: dict) -> pd.DataFrame:
    """从对象存储加载单个数据集的 DataFrame。"""
    storage = get_storage()
    raw = storage.get(ds["storage_key"])
    kind = ds.get("kind", "csv")
    columns = ds.get("columns")
    sheet = ds.get("sheet")

    if kind == "xlsx":
        return load_excel(raw, sheet_name=sheet if sheet is not None else 0,
                          column_names=columns)
    else:
        return load_dataframe(raw, column_names=columns)


def get_or_load(sid: str, session: dict) -> pd.DataFrame | None:
    """加载所有数据集，返回主表（第一个）。向后兼容旧代码。"""
    get_or_load_all(sid, session)
    return get(sid)


def get_or_load_all(sid: str, session: dict) -> dict[str, pd.DataFrame]:
    """加载会话的所有数据集到注册表，返回 {ds_id: df}。
    已加载的跳过（进程内缓存）。
    """
    datasets = session.get("datasets", [])
    if not datasets:
        # 向后兼容：旧会话没有 datasets 但有 df_storage_key
        if session.get("df_storage_key"):
            datasets = [{
                "id":          "ds1",
                "name":        session.get("df_name", "data.csv"),
                "kind":        session.get("df_kind", "csv"),
                "columns":     session.get("df_columns"),
                "sheet":       session.get("df_sheet"),
                "storage_key": session["df_storage_key"],
            }]
        else:
            return {}

    with _lock:
        cached = _registry.setdefault(sid, {})

    for ds in datasets:
        ds_id = ds["id"]
        if ds_id in cached:
            continue
        try:
            df = _load_one(ds)
            register(sid, ds_id, df)
        except Exception:
            pass

    return get_all(sid)
