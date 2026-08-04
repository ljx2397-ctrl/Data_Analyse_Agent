# chart_advisor.py
# 职责：根据数据特征推荐最合适的图表类型。
# 从 chart_tool.py 中独立出来，使推荐逻辑可以单独测试和扩展，
# 也可以在未来被其他模块（如 excel_tool）复用。

import pandas as pd


# 图表类型常量，避免拼写错误
CHART_BAR     = "bar"
CHART_BARH    = "barh"
CHART_PIE     = "pie"
CHART_LINE    = "line"
CHART_SCATTER = "scatter"

# 时间相关的列名关键词，命中则推荐折线图
_TIME_KEYWORDS = ("月", "年", "季", "周", "日期", "时间", "date", "time", "period")


def recommend(
    result_df: pd.DataFrame,
    x_col: str,
    y_col: str,
) -> tuple[str, str]:
    """
    根据 DataFrame 的数据特征推荐图表类型。

    参数：
        result_df — 已聚合好的分析结果 DataFrame
        x_col     — 拟用作 x 轴（标签）的列名
        y_col     — 拟用作 y 轴（数值）的列名

    返回：
        (chart_type, reason) — 推荐类型字符串和中文说明原因
    """
    # 列名不存在时返回默认值，避免 KeyError
    if x_col not in result_df.columns or y_col not in result_df.columns:
        return CHART_BAR, "列名未找到，使用默认柱状图"

    x_series = result_df[x_col]
    x_nuniq  = int(x_series.nunique())

    # ── 判断 x 轴是否为时间/周期型 ───────────────────────────────────────
    x_is_datetime = pd.api.types.is_datetime64_any_dtype(x_series)
    x_looks_like_time = (
        x_series.dtype == object
        and x_series.dropna().astype(str)
            .str.match(r"\d{4}[-/]\d{2}").any()
    )
    x_col_name_is_time = any(kw in str(x_col) for kw in _TIME_KEYWORDS)

    if x_is_datetime or x_looks_like_time or x_col_name_is_time:
        return CHART_LINE, "x 轴为时间/周期型字段，折线图最能体现趋势变化"

    # ── 根据分类数量选择图表 ──────────────────────────────────────────────
    if x_nuniq <= 2:
        return CHART_PIE, f"仅 {x_nuniq} 个分类，饼图直观展示占比关系"

    if x_nuniq <= 6:
        return CHART_PIE, f"{x_nuniq} 个分类（≤6），饼图清晰展示各类占比"

    if x_nuniq <= 12:
        return CHART_BAR, f"{x_nuniq} 个分类（≤12），竖向柱状图对比效果清晰"

    if x_nuniq <= 30:
        return CHART_BARH, f"{x_nuniq} 个分类（12~30），横向柱状图避免标签重叠"

    return CHART_BARH, f"分类数达 {x_nuniq} 个，横向柱状图配合滚动查看更易读"


def recommend_for_columns(
    result_df: pd.DataFrame,
) -> tuple[str, str, str, str]:
    """
    当调用方没有明确指定 x_col / y_col 时，自动猜测最合适的列组合。

    策略：
    - x_col：优先选第一个字符串列（分类标签），其次选第一列
    - y_col：优先选第一个数值列，其次选最后一列

    返回：
        (x_col, y_col, chart_type, reason)
    """
    cols     = list(result_df.columns)
    str_cols = [c for c in cols if result_df[c].dtype == object]
    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(result_df[c])]

    x_col = str_cols[0] if str_cols else cols[0]
    y_col = num_cols[0] if num_cols else cols[-1]

    chart_type, reason = recommend(result_df, x_col, y_col)
    return x_col, y_col, chart_type, reason