# backend/core/chart_advisor.py
# 直接复用原 chart_advisor.py（纯算法，无 Streamlit 依赖）。
# 根据数据特征推荐最合适的图表类型。
import pandas as pd

CHART_BAR     = "bar"
CHART_BARH    = "barh"
CHART_PIE     = "pie"
CHART_LINE    = "line"
CHART_SCATTER = "scatter"

_TIME_KEYWORDS = ("月", "年", "季", "周", "日期", "时间", "date", "time", "period")


def recommend(result_df: pd.DataFrame, x_col: str, y_col: str) -> tuple[str, str]:
    """根据数据特征推荐图表类型，返回 (chart_type, reason)。"""
    if x_col not in result_df.columns or y_col not in result_df.columns:
        return CHART_BAR, "列名未找到，使用默认柱状图"

    x_series = result_df[x_col]
    x_nuniq  = int(x_series.nunique())

    x_is_datetime = pd.api.types.is_datetime64_any_dtype(x_series)
    x_looks_like_time = (
        x_series.dtype == object
        and x_series.dropna().astype(str).str.match(r"\d{4}[-/]\d{2}").any()
    )
    x_col_name_is_time = any(kw in str(x_col) for kw in _TIME_KEYWORDS)

    if x_is_datetime or x_looks_like_time or x_col_name_is_time:
        return CHART_LINE, "x 轴为时间/周期型字段，折线图最能体现趋势变化"

    if x_nuniq <= 2:
        return CHART_PIE, f"仅 {x_nuniq} 个分类，饼图直观展示占比关系"
    if x_nuniq <= 6:
        return CHART_PIE, f"{x_nuniq} 个分类（≤6），饼图清晰展示各类占比"
    if x_nuniq <= 12:
        return CHART_BAR, f"{x_nuniq} 个分类（≤12），竖向柱状图对比效果清晰"
    if x_nuniq <= 30:
        return CHART_BARH, f"{x_nuniq} 个分类（12~30），横向柱状图避免标签重叠"
    return CHART_BARH, f"分类数达 {x_nuniq} 个，横向柱状图配合滚动查看更易读"


def recommend_for_columns(result_df: pd.DataFrame) -> tuple[str, str, str, str]:
    """未指定 x_col/y_col 时自动猜测，返回 (x_col, y_col, chart_type, reason)。"""
    cols     = list(result_df.columns)
    str_cols = [c for c in cols if result_df[c].dtype == object]
    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(result_df[c])]

    x_col = str_cols[0] if str_cols else cols[0]
    y_col = num_cols[0] if num_cols else cols[-1]

    chart_type, reason = recommend(result_df, x_col, y_col)
    return x_col, y_col, chart_type, reason
