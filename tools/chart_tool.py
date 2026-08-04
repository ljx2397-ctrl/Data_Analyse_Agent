# tools/chart_tool.py
# 图表生成工具。
# 接收 data_expr + 轴配置，执行数据代码，自动推荐或使用指定图表类型，
# 渲染 PNG 图片并返回文件路径标记供 UI 渲染。

import os
import re
import json
import uuid
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from langchain.tools import tool

from config import CHARTS_DIR, CHART_COLORS
from chart_advisor import recommend          # ← 复用独立的推荐模块
from tools.analyse_tool import run_sandboxed   # ← 复用统一的超时沙箱与静态检查

_df_accessor = None

def set_df_accessor(fn):
    """由 agent.py 注入，返回当前 DataFrame 的可调用对象"""
    global _df_accessor
    _df_accessor = fn


def _draw(result_df: pd.DataFrame, x_col: str, y_col: str,
          chart_type: str, title: str,
          xlabel: str = "", ylabel: str = "") -> str:
    """绘制图表并保存为 PNG，返回文件路径。"""
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#F8F9FA")
    ax.set_facecolor("#F8F9FA")

    x_vals = result_df[x_col].astype(str)
    y_vals = pd.to_numeric(result_df[y_col], errors="coerce").fillna(0)
    # 颜色循环：分类数超过色板长度时自动复用
    colors = (CHART_COLORS * ((len(x_vals) // len(CHART_COLORS)) + 1))[:len(x_vals)]

    if chart_type == "bar":
        bars = ax.bar(x_vals, y_vals, color=colors, edgecolor="white", linewidth=0.8)
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
        plt.xticks(rotation=20, ha="right")
        ax.set_xlabel(xlabel or x_col)
        ax.set_ylabel(ylabel or y_col)

    elif chart_type == "barh":
        bars = ax.barh(x_vals, y_vals, color=colors, edgecolor="white")
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
        ax.set_xlabel(ylabel or y_col)

    elif chart_type == "pie":
        _, _, autotexts = ax.pie(
            y_vals, labels=x_vals, autopct="%1.1f%%",
            colors=colors, startangle=90,
            wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        )
        for t in autotexts:
            t.set_fontsize(9)

    elif chart_type == "line":
        ax.plot(x_vals, y_vals, marker="o", color=CHART_COLORS[0],
                linewidth=2, markersize=6)
        ax.fill_between(range(len(x_vals)), y_vals,
                        alpha=0.12, color=CHART_COLORS[0])
        plt.xticks(rotation=20, ha="right")
        ax.set_xlabel(xlabel or x_col)
        ax.set_ylabel(ylabel or y_col)

    elif chart_type == "scatter":
        ax.scatter(x_vals, y_vals, color=CHART_COLORS[0], alpha=0.7)
        ax.set_xlabel(xlabel or x_col)
        ax.set_ylabel(ylabel or y_col)

    ax.set_title(title, fontsize=14, fontweight="bold", pad=14)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if chart_type != "pie":
        ax.grid(axis="y", linestyle="--", alpha=0.35)

    plt.tight_layout()
    safe_name  = re.sub(r"[^\w\-]", "_", title)
    chart_path = os.path.join(CHARTS_DIR, f"{safe_name}_{uuid.uuid4().hex[:6]}.png")
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    return chart_path


@tool
def chart_tool(params: str) -> str:
    """
    根据数据表达式生成图表 PNG，返回 CHART:<路径> 标记。

    params 为 JSON 字符串，字段说明：
    - data_expr  （必填）：Python 代码，将结果赋值给 result（DataFrame）
    - x_col      （必填）：x 轴/标签列名
    - y_col      （必填）：数值列名
    - chart_type （可选）：bar | barh | pie | line | scatter，不填则自动推荐
    - title      （可选）：图表标题，默认"图表"
    - xlabel     （可选）：x 轴标签覆盖
    - ylabel     （可选）：y 轴标签覆盖

    注意：data_expr 中禁止调用 plt，图表绘制由工具内部处理。
    """
    try:
        p = json.loads(params)
    except Exception:
        return "❌ 参数解析失败，请传入合法 JSON 字符串。"

    data_expr  = p.get("data_expr", "")
    x_col      = p.get("x_col", "")
    y_col      = p.get("y_col", "")
    chart_type = p.get("chart_type")    # 为 None 时自动推荐
    title      = p.get("title", "图表")
    xlabel     = p.get("xlabel", "")
    ylabel     = p.get("ylabel", "")

    if not data_expr or not x_col or not y_col:
        return "❌ 必须提供 data_expr、x_col、y_col 三个字段。"

    df = _df_accessor()
    if df is None:
        return "❌ 没有加载数据集。"

    # ── 执行 data_expr（复用 analyse_tool 的统一超时沙箱）────────────────
    try:
        ok, err, local_vars = run_sandboxed(data_expr, df)
    except ValueError as e:
        return f"❌ data_expr 执行出错：{e}"
    if not ok:
        return f"❌ data_expr 执行出错：{err}"

    result = local_vars.get("result")
    if result is None:
        dfs = [v for v in local_vars.values() if isinstance(v, pd.DataFrame)]
        result = dfs[0] if dfs else None
    if not isinstance(result, pd.DataFrame) or result.empty:
        return "❌ data_expr 执行后未得到有效的非空 DataFrame（请赋值给 result）。"

    if x_col not in result.columns:
        return f"❌ x_col='{x_col}' 不在 result 中。可用列：{list(result.columns)}"
    if y_col not in result.columns:
        return f"❌ y_col='{y_col}' 不在 result 中。可用列：{list(result.columns)}"

    # ── 自动推荐图表类型 ──────────────────────────────────────────────────
    recommendation_note = ""
    if not chart_type:
        chart_type, reason = recommend(result, x_col, y_col)
        recommendation_note = f"📊 自动推荐图表类型：**{chart_type}**（{reason}）\n"

    # ── 绘图 ──────────────────────────────────────────────────────────────
    try:
        chart_path = _draw(result, x_col, y_col, chart_type, title, xlabel, ylabel)
    except Exception as e:
        return f"❌ 图表绘制出错：{e}"

    return f"{recommendation_note}CHART:{chart_path}\n图表已生成：{title}"
