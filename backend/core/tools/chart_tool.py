# backend/core/tools/chart_tool.py
# 复用原 chart_tool.py，import 路径改为 core 包。
# 接收 data_expr + 轴配置，执行数据代码，自动推荐图表类型，渲染 PNG 返回路径标记。
import os
import re
import json
import uuid
import threading
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from langchain.tools import tool

from core.config import CHARTS_DIR, CHART_COLORS
from core.chart_advisor import recommend
from core.tools.analyse_tool import run_sandboxed

# matplotlib 全局状态非线程安全，并行生成多张图时会互相干扰（空白/错位/重复）。
# 用锁串行化绘图操作，确保每张图独立完成后再画下一张。
_draw_lock = threading.Lock()

_df_accessor = None
_dfs_accessor = None
_sid_getter = None    # 返回当前会话 sid，用于取 last_result

def set_df_accessor(fn):
    global _df_accessor
    _df_accessor = fn

def set_dfs_accessor(fn):
    global _dfs_accessor
    _dfs_accessor = fn

def set_sid_getter(fn):
    """注入 sid 访问器：用于复用上次 analyse_tool 的 result。"""
    global _sid_getter
    _sid_getter = fn


def _draw(result_df: pd.DataFrame, x_col: str, y_col,
          chart_type: str, title: str,
          xlabel: str = "", ylabel: str = "") -> str:
    with _draw_lock:
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor("#F8F9FA")
        ax.set_facecolor("#F8F9FA")

        # 按 x_col 排序，避免时间序列乱序导致折线锯齿
        result_df = result_df.sort_values(x_col).reset_index(drop=True)
        x_vals = result_df[x_col].astype(str)

        # y_col 支持单列名（str）或多列名（list），统一成 list 处理
        y_cols = [y_col] if isinstance(y_col, str) else list(y_col)
        colors = (CHART_COLORS * ((len(y_cols) // len(CHART_COLORS)) + 1))[:len(y_cols)]

        if chart_type == "line" and len(y_cols) > 1:
            # 多系列折线图：每个 y_col 一条线
            for i, yc in enumerate(y_cols):
                yv = pd.to_numeric(result_df[yc], errors="coerce").fillna(0)
                ax.plot(x_vals, yv, marker="o", color=colors[i],
                        linewidth=2, markersize=6, label=yc)
            ax.legend(fontsize=10)
            ax.tick_params(axis="x", rotation=20)
            for label in ax.get_xticklabels():
                label.set_ha("right")
            ax.set_xlabel(xlabel or x_col)
            ax.set_ylabel(ylabel or "数值")
        elif chart_type == "bar" and len(y_cols) > 1:
            # 多系列柱状图：分组柱
            x_pos = np.arange(len(x_vals))
            bar_w = 0.8 / len(y_cols)
            for i, yc in enumerate(y_cols):
                yv = pd.to_numeric(result_df[yc], errors="coerce").fillna(0)
                ax.bar(x_pos + i * bar_w, yv, bar_w, color=colors[i],
                       edgecolor="white", linewidth=0.8, label=yc)
            ax.set_xticks(x_pos + bar_w * (len(y_cols) - 1) / 2)
            ax.set_xticklabels(x_vals, rotation=20, ha="right")
            ax.legend(fontsize=10)
            ax.set_xlabel(xlabel or x_col)
            ax.set_ylabel(ylabel or "数值")
        else:
            # 单系列
            y_vals = pd.to_numeric(result_df[y_cols[0]], errors="coerce").fillna(0)
            bar_colors = (CHART_COLORS * ((len(x_vals) // len(CHART_COLORS)) + 1))[:len(x_vals)]
            if chart_type == "bar":
                bars = ax.bar(x_vals, y_vals, color=bar_colors, edgecolor="white", linewidth=0.8)
                ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
                ax.tick_params(axis="x", rotation=20)
                for label in ax.get_xticklabels():
                    label.set_ha("right")
                ax.set_xlabel(xlabel or x_col)
                ax.set_ylabel(ylabel or y_cols[0])
            elif chart_type == "barh":
                bars = ax.barh(x_vals, y_vals, color=bar_colors, edgecolor="white")
                ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
                ax.set_xlabel(ylabel or y_cols[0])
            elif chart_type == "pie":
                _, _, autotexts = ax.pie(
                    y_vals, labels=x_vals, autopct="%1.1f%%",
                    colors=bar_colors, startangle=90,
                    wedgeprops={"edgecolor": "white", "linewidth": 1.5},
                )
                for t in autotexts:
                    t.set_fontsize(9)
            elif chart_type == "line":
                ax.plot(x_vals, y_vals, marker="o", color=CHART_COLORS[0],
                        linewidth=2, markersize=6)
                ax.fill_between(range(len(x_vals)), y_vals,
                                alpha=0.12, color=CHART_COLORS[0])
                ax.tick_params(axis="x", rotation=20)
                for label in ax.get_xticklabels():
                    label.set_ha("right")
                ax.set_xlabel(xlabel or x_col)
                ax.set_ylabel(ylabel or y_cols[0])
            elif chart_type == "scatter":
                ax.scatter(x_vals, y_vals, color=CHART_COLORS[0], alpha=0.7)
                ax.set_xlabel(xlabel or x_col)
                ax.set_ylabel(ylabel or y_cols[0])

        ax.set_title(title, fontsize=14, fontweight="bold", pad=14)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if chart_type != "pie":
            ax.grid(axis="y", linestyle="--", alpha=0.35)

        fig.tight_layout()
        safe_name  = re.sub(r"[^\w\-]", "_", title)
        chart_path = os.path.join(CHARTS_DIR, f"{safe_name}_{uuid.uuid4().hex[:6]}.png")
        fig.savefig(chart_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return chart_path


@tool
def chart_tool(params: str) -> str:
    """
    根据数据生成图表 PNG，返回 CHART:<路径> 标记。
    params 为 JSON 字符串：
    - x_col / y_col（必填）：指定横纵轴列名。y_col 可传字符串或数组（多系列折线/柱状图）。
    - chart_type / title / xlabel / ylabel（可选）：不填 chart_type 时自动推荐。
    - 数据来源三选一（按优先级）：
      ① result_name: 复用已命名的 analyse_tool 结果（推荐，省 token）
      ② use_last_result: true —— 复用最后一次 analyse_tool 结果（单表场景）
      ③ data_expr: 重新计算（仅在无可复用结果时用）
    复用已有结果时严禁同时写 data_expr。需要多张独立图表时，分别调用本工具多次。
    """
    try:
        p = json.loads(params)
    except Exception:
        return "❌ 参数解析失败，请传入合法 JSON 字符串。"

    data_expr  = p.get("data_expr", "")
    use_last   = bool(p.get("use_last_result", False))
    res_name   = (p.get("result_name") or "").strip()
    x_col      = p.get("x_col", "")
    y_col      = p.get("y_col", "")
    chart_type = p.get("chart_type")
    title      = p.get("title", "图表")
    xlabel     = p.get("xlabel", "")
    ylabel     = p.get("ylabel", "")

    # LLM 有时会传 ["月份"] 这种 list，统一转为 str
    if isinstance(x_col, list):
        x_col = x_col[0] if x_col else ""
    # y_col 允许是 list（多系列折线图/柱状图），但也可能是 ["金额"] 这种单元素 list
    if isinstance(y_col, list) and len(y_col) == 1:
        y_col = y_col[0]

    # 复用优先级：result_name（按名取）> use_last_result / 空 data_expr（取上次）
    if res_name or use_last or not data_expr.strip():
        if _sid_getter is None:
            return "❌ 无法复用结果：sid 访问器未配置。"
        from services import df_registry
        sid = _sid_getter()
        if res_name:
            result = df_registry.get_named_result(sid, res_name)
            not_found_hint = f"❌ 未找到名为 '{res_name}' 的结果。请确认 analyse_tool 时已用相同 result_name 命名。"
        else:
            result = df_registry.get_last_result(sid)
            not_found_hint = "❌ 没有可复用的上次分析结果，请先调用 analyse_tool 或提供 data_expr。"
        if result is None:
            return not_found_hint
        if not isinstance(result, pd.DataFrame) or result.empty:
            return "❌ 复用的结果不是有效的非空 DataFrame，无法画图。"
    else:
        if not x_col or not y_col:
            return "❌ 必须提供 data_expr、x_col、y_col 三个字段。"

        df = _df_accessor()
        if df is None:
            return "❌ 没有加载数据集。"

        dfs = {}
        if _dfs_accessor is not None:
            try:
                dfs = _dfs_accessor() or {}
            except Exception:
                dfs = {}

        try:
            ok, err, local_vars = run_sandboxed(data_expr, df, dfs)
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
    # y_col 可能是 str 或 list（多系列）
    y_cols = [y_col] if isinstance(y_col, str) else list(y_col)
    missing = [c for c in y_cols if c not in result.columns]
    if missing:
        return f"❌ y_col='{missing}' 不在 result 中。可用列：{list(result.columns)}"

    recommendation_note = ""
    if not chart_type:
        chart_type, reason = recommend(result, x_col, y_cols[0])
        recommendation_note = f"📊 自动推荐图表类型：**{chart_type}**（{reason}）\n"

    try:
        chart_path = _draw(result, x_col, y_col, chart_type, title, xlabel, ylabel)
    except Exception as e:
        return f"❌ 图表绘制出错：{e}"

    return f"{recommendation_note}CHART:{chart_path}\n图表已生成：{title}"
