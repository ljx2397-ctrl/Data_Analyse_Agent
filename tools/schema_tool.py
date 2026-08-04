# tools/schema_tool.py
"""
schema_tool — 让 LLM 全面了解已加载的 DataFrame。

Agent 在编写任何 Pandas 代码之前必须先调用本工具。
返回结构化文本，包含：
  - 行数 / 列数
  - 每列：名称、类型、空值率、唯一值数量、样本值
  - 数值列：最小 / 最大 / 平均值
  - 已识别并转为 datetime 的日期列
  - 低基数分类列的 Top-5 频次
"""
import json
import pandas as pd
from langchain.tools import tool

# _df_accessor 是在 agent 启动时注入的延迟绑定访问器（见 agent.py）
_df_accessor = None

def set_df_accessor(fn):
    global _df_accessor
    _df_accessor = fn


@tool
def schema_tool(query: str) -> str:
    """
    返回当前已加载数据集的完整 schema 与数据画像。

    在编写任何分析代码之前请先调用本工具。
    可在 query 中传入自然语言字段问题，以获取最匹配的列名，例如：
      query = "哪一列代表交易金额？"

    无论是否传入 query，都会返回完整 schema。
    """
    df = _df_accessor()
    if df is None:
        return "❌ 没有加载数据集，请先上传 CSV 文件。"

    lines = []

    # ── 头部概览 ────────────────────────────────────────────────────────────
    lines.append(f"📊 数据集概览")
    lines.append(f"总行数：{len(df):,}  |  总列数：{len(df.columns)}")
    lines.append("")

    # ── 每列画像 ────────────────────────────────────────────────────────────
    lines.append("### 字段明细")
    lines.append(f"{'字段名':<20} {'类型':<12} {'空值率':>7} {'唯一值':>6}  样本值")
    lines.append("-" * 72)

    for col in df.columns:
        dtype    = str(df[col].dtype)
        null_pct = round(df[col].isna().mean() * 100, 1)
        uniq     = df[col].nunique()
        samples  = df[col].dropna().astype(str).head(3).tolist()
        sample_str = " | ".join(samples)
        lines.append(f"{col:<20} {dtype:<12} {null_pct:>6}% {uniq:>6}  {sample_str}")

    lines.append("")

    # ── 数值摘要 ────────────────────────────────────────────────────────────
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

    # ── 日期列 ──────────────────────────────────────────────────────────────
    dt_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    if dt_cols:
        lines.append("### 日期字段（已转为 datetime）")
        for col in dt_cols:
            s = df[col].dropna()
            lines.append(f"  {col}: {s.min().date()} → {s.max().date()}  "
                         f"({s.nunique()} 个不重复日期)")
        lines.append("")

    # ── 分类列 Top-5 ───────────────────────────────────────────────────────
    cat_cols = df.select_dtypes(include="object").columns.tolist()
    if cat_cols:
        lines.append("### 分类字段 Top-5 频次")
        for col in cat_cols[:8]:   # 最多展示 8 列，避免 token 膨胀
            vc = df[col].value_counts().head(5)
            kv = "  ".join(f"{k}({v})" for k, v in vc.items())
            lines.append(f"  {col}: {kv}")
        lines.append("")

    # ── 可选：自然语言列匹配 ────────────────────────────────────────────────
    if query.strip():
        from schema_matcher import SchemaIndex   # 延迟导入
        idx  = SchemaIndex(df)
        hits = idx.match(query, top_k=5)
        if hits:
            lines.append(f"### 与「{query}」最相关的字段")
            for col, score in hits:
                lines.append(f"  {col}  (相关度 {score:.3f})")
            lines.append("")

    return "\n".join(lines)