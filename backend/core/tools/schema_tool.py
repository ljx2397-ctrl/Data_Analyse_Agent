# backend/core/tools/schema_tool.py
# 返回 DataFrame 的完整 schema 与数据画像，Agent 编写代码前必须先调用。
# 支持多表：当会话加载了多个数据集时，展示所有表的 schema。
# 样本值在返回给 LLM 前会做 PII 脱敏，避免真实手机号/身份证/姓名等外泄。
import re
import pandas as pd
from langchain.tools import tool

_df_accessor = None
_dfs_accessor = None
_sid_getter = None    # 返回当前会话 sid，用于记录 schema 是否已查过

def set_df_accessor(fn):
    global _df_accessor
    _df_accessor = fn

def set_dfs_accessor(fn):
    global _dfs_accessor
    _dfs_accessor = fn

def set_sid_getter(fn):
    """注入 sid 访问器：用于记录本会话是否已调过 schema，避免重复调用。"""
    global _sid_getter
    _sid_getter = fn


def _schema_called(sid: str) -> bool:
    """本会话是否已调用过 schema_tool（按 sid 记录，状态存 df_registry）。"""
    if not sid:
        return False
    try:
        from services import df_registry
        return df_registry.get_named_result(sid, "__schema_called__") is True
    except Exception:
        return False


def _mark_schema_called(sid: str) -> None:
    if not sid:
        return
    try:
        from services import df_registry
        df_registry.set_named_result(sid, "__schema_called__", True)
    except Exception:
        pass


# 姓名列识别关键词（不区分大小写匹配列名）
_PII_NAME_KEYWORDS = (
    "姓名", "客户名", "客户姓名", "name", "username", "account_name", "户名",
)


def _mask_pii(value, col: str = "") -> str:
    """对单个样本值做 PII 脱敏。
    识别手机号 / 身份证 / 邮箱 / 银行卡（按值格式）和姓名（按列名）。
    非敏感值原样返回。
    """
    if value is None:
        return ""
    v = str(value).strip()
    if not v:
        return v
    # 手机号：1 开头 11 位
    if re.fullmatch(r"1[3-9]\d{9}", v):
        return v[:3] + "****" + v[-4:]
    # 身份证：18 位，末位可能为 X
    if re.fullmatch(r"\d{17}[\dXx]", v):
        return v[:6] + "********" + v[-4:]
    # 邮箱
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        local, _, domain = v.partition("@")
        return (local[0] if local else "*") + "***@" + domain
    # 银行卡：16-19 位纯数字
    if re.fullmatch(r"\d{16,19}", v):
        return v[:4] + " **** **** " + v[-4:]
    # 姓名：依赖列名判断
    col_lower = (col or "").lower()
    if any(k in col_lower for k in _PII_NAME_KEYWORDS) and len(v) >= 2:
        return v[0] + "*" * (len(v) - 1)
    return v


def _schema_for_df(df: pd.DataFrame, query: str, table_name: str = "") -> list[str]:
    """生成单个 DataFrame 的 schema 文本行。"""
    lines = []
    header = f"📊 数据集概览"
    if table_name:
        header += f"：{table_name}"
    lines.append(header)
    lines.append(f"总行数：{len(df):,}  |  总列数：{len(df.columns)}  （行数请直接引用此值，勿用 index.max()）")
    lines.append("")
    lines.append("### 字段明细")
    lines.append(f"{'字段名':<20} {'类型':<12} {'空值率':>7} {'唯一值':>6}  样本值(脱敏)")
    lines.append("-" * 72)
    for col in df.columns:
        dtype    = str(df[col].dtype)
        null_pct = round(df[col].isna().mean() * 100, 1)
        uniq     = df[col].nunique()
        samples  = [_mask_pii(s, col)
                    for s in df[col].dropna().astype(str).head(3).tolist()]
        sample_str = " | ".join(samples)
        lines.append(f"{col:<20} {dtype:<12} {null_pct:>6}% {uniq:>6}  {sample_str}")
    lines.append("")

    num_cols = df.select_dtypes(include="number").columns.tolist()
    if num_cols:
        lines.append("### 数值字段统计")
        for col in num_cols:
            s = df[col].dropna()
            lines.append(
                f"  {col}: min={s.min():,.2f}  max={s.max():,.2f}  "
                f"mean={s.mean():,.2f}  sum={s.sum():,.2f}"
            )
        lines.append("")

    dt_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    if dt_cols:
        lines.append("### 日期字段（已转为 datetime）")
        for col in dt_cols:
            s = df[col].dropna()
            lines.append(f"  {col}: {s.min().date()} → {s.max().date()}  "
                         f"({s.nunique()} 个不重复日期)")
        lines.append("")

    cat_cols = df.select_dtypes(include="object").columns.tolist()
    if cat_cols:
        lines.append("### 分类字段 Top-5 频次")
        for col in cat_cols[:8]:
            vc = df[col].value_counts().head(5)
            kv = "  ".join(f"{_mask_pii(k, col)}({v})" for k, v in vc.items())
            lines.append(f"  {col}: {kv}")
        lines.append("")

    if query.strip():
        from core.schema_matcher import SchemaIndex
        idx  = SchemaIndex(df)
        hits = idx.match(query, top_k=5)
        if hits:
            lines.append(f"### 与「{query}」最相关的字段")
            for col, score in hits:
                lines.append(f"  {col}  (相关度 {score:.3f})")
            lines.append("")

    return lines


@tool
def schema_tool(query: str) -> str:
    """
    返回当前已加载数据集的完整 schema 与数据画像。
    在编写任何分析代码之前请先调用本工具。
    可在 query 中传入自然语言字段问题，以获取最匹配的列名。
    多表场景下会展示所有已加载表的 schema，主表（df）和其他表（dfs）均会列出。

    注意：同一会话只需调用一次。已调过时本工具会直接拒绝（返回错误），
    请不要重复调用——直接用之前已返回的字段信息即可。reload_df 后状态会重置。
    """
    df = _df_accessor()
    if df is None:
        return "❌ 没有加载数据集，请先上传 CSV 文件。"

    # 同一会话已调过 schema：直接拒绝，不返回任何内容，强制 LLM 停止这个动作
    sid = _sid_getter() if _sid_getter else ""
    if _schema_called(sid):
        return (
            "❌ schema_tool 本会话已调用过，禁止重复调用。"
            "之前的 schema 信息已在对话历史中，请直接基于已有字段信息进行下一步操作。"
            "若要导出/画图上次结果，请直接调 excel_tool / chart_tool（用 result_name 或 use_last_result 复用），"
            "无需重新 schema_tool + analyse_tool。"
        )

    # 首次调用：返回完整 schema 并标记
    _mark_schema_called(sid)

    all_lines = []

    # 主表
    all_lines.extend(_schema_for_df(df, query, "主表 df"))

    # 多表：展示其他表
    if _dfs_accessor is not None:
        try:
            dfs = _dfs_accessor() or {}
        except Exception:
            dfs = {}
        if len(dfs) > 1:
            all_lines.append("=" * 72)
            all_lines.append(f"📋 当前共加载 {len(dfs)} 张表，可用表名（在代码中通过 dfs[\"表名\"] 访问）：")
            for name in dfs:
                tag = "（主表 df）" if dfs[name] is df else ""
                all_lines.append(f"  - dfs[\"{name}\"]{tag}  {len(dfs[name]):,} 行 × {len(dfs[name].columns)} 列")
            all_lines.append("")
            all_lines.append("💡 多表关联示例：pd.merge(df, dfs[\"表名\"], on=\"关联字段\", how=\"left\")")
            all_lines.append("")

            # 展示其他表的 schema（非主表）
            for name, other_df in dfs.items():
                if other_df is df:
                    continue
                all_lines.append("-" * 72)
                all_lines.extend(_schema_for_df(other_df, query, f"dfs[\"{name}\"]"))

    return "\n".join(all_lines)
