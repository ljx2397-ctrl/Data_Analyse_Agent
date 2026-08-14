# backend/core/tools/excel_tool.py
# 复用原 excel_tool.py，import 路径改为 core 包。
# 生成多 Sheet 样式 Excel 报告，返回 XLSX:<路径> 标记。
import os
import re
import json
import uuid

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import xlsxwriter
from langchain.tools import tool

from core.config import CHARTS_DIR, OUTPUT_DIR, CHART_COLORS
from core.chart_advisor import recommend
from core.tools.analyse_tool import run_sandboxed

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


# 长号码/编码类列名关键词：识别后按文本写入，避免科学计数法与前导 0 丢失
_TEXT_NUM_KEYWORDS = (
    "手机", "电话", "号码", "身份证", "邮编", "卡号", "账号", "工号",
    "编号", "id", "code", "卡号", "证件",
)
# 金额/小数类列名关键词：强制保留两位小数
_MONEY_KEYWORDS = (
    "金额", "价格", "费用", "成本", "余额", "aum", "amount", "amt",
    "price", "money", "利率", "比率", "占比", "百分比",
)


def _classify_column(col_name, series: pd.Series) -> str:
    """判定列在 Excel 中的数字格式类别，返回 'text' | 'int' | 'float'。
    - text: 长号码/编码类（手机号、身份证等），按文本写入避免科学计数法
    - int:  整数列或所有值均为整数的浮点列（年龄、天数、笔数、计数等）
    - float: 金额/小数类，保留两位小数
    """
    name = str(col_name).lower()
    # 1. 长号码/编码类 → 文本
    if any(k in name for k in _TEXT_NUM_KEYWORDS):
        return "text"
    # 2. 整数 dtype → 整数格式
    if pd.api.types.is_integer_dtype(series):
        return "int"
    # 3. 浮点 dtype：按列名与实际值判定
    if pd.api.types.is_float_dtype(series):
        non_null = series.dropna()
        # 金额/比率类 → 强制小数
        if any(k in name for k in _MONEY_KEYWORDS):
            return "float"
        if len(non_null) == 0:
            return "int"
        # 所有非空值都是整数 → 整数格式
        if (non_null == non_null.astype("int64")).all():
            return "int"
        return "float"
    return "text"


def _exec_expr(data_expr: str, df: pd.DataFrame, dfs: dict | None = None) -> pd.DataFrame | str:
    try:
        ok, err, local_vars = run_sandboxed(data_expr, df, dfs)
    except ValueError as e:
        return f"代码未通过安全检查：{e}"
    if not ok:
        return err
    result = local_vars.get("result")
    if result is None:
        # 兜底：查找 local_vars 中的 DataFrame（排除 df/dfs 本身与明显中间变量）
        candidates = [
            v for k, v in local_vars.items()
            if isinstance(v, pd.DataFrame)
            and k not in ("df", "dfs")
            and not k.startswith("_")
        ]
        if candidates:
            result = candidates[-1]  # 取最后一个赋值的 DataFrame
        else:
            return ("❌ data_expr 未产生 result 变量。"
                    "请确保在代码末尾将最终结果赋值给 result，例如：result = df.groupby('字段').sum().reset_index()")
    if not isinstance(result, pd.DataFrame):
        return f"❌ result 变量不是 DataFrame（实际类型：{type(result).__name__}），无法写入 Sheet。"
    if result.empty:
        return "❌ data_expr 产生的 result 为空 DataFrame，请检查过滤/聚合条件。"
    return result


def _make_chart_png(result_df: pd.DataFrame, x_col: str, y_col: str,
                    chart_type: str, title: str) -> str | None:
    if x_col not in result_df.columns or y_col not in result_df.columns:
        return None
    try:
        fig, ax = plt.subplots(figsize=(8, 4))
        fig.patch.set_facecolor("#FFFFFF")
        x_vals = result_df[x_col].astype(str)
        y_vals = pd.to_numeric(result_df[y_col], errors="coerce").fillna(0)
        colors = (CHART_COLORS * ((len(x_vals) // len(CHART_COLORS)) + 1))[:len(x_vals)]

        if chart_type == "bar":
            ax.bar(x_vals, y_vals, color=colors, edgecolor="white")
            plt.xticks(rotation=15, ha="right")
        elif chart_type == "barh":
            ax.barh(x_vals, y_vals, color=colors, edgecolor="white")
        elif chart_type == "pie":
            ax.pie(y_vals, labels=x_vals, autopct="%1.1f%%",
                   colors=colors, startangle=90,
                   wedgeprops={"edgecolor": "white", "linewidth": 1.5})
        elif chart_type == "line":
            ax.plot(x_vals, y_vals, marker="o", color=CHART_COLORS[0],
                    linewidth=2, markersize=5)
            ax.fill_between(range(len(x_vals)), y_vals,
                            alpha=0.1, color=CHART_COLORS[0])
            plt.xticks(rotation=15, ha="right")
        else:
            plt.close()
            return None

        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        plt.tight_layout()

        safe = re.sub(r"[^\w]", "_", title)
        img_path = os.path.join(CHARTS_DIR, f"xl_{safe}_{uuid.uuid4().hex[:6]}.png")
        plt.savefig(img_path, dpi=130, bbox_inches="tight")
        plt.close()
        return img_path
    except Exception:
        return None


def _write_sheet(wb: xlsxwriter.Workbook, cfg: dict,
                 df_master: pd.DataFrame, dfs: dict, fmts: dict,
                 used_names: set) -> str | None:
    data_expr  = cfg.get("data_expr", "")
    use_last   = bool(cfg.get("use_last_result", False))
    res_name   = (cfg.get("result_name") or "").strip()
    sheet_name = re.sub(r"[\[\]:*?/\\]", "_",
                        str(cfg.get("sheet_name", "Sheet")).strip())[:31] or "Sheet"
    if sheet_name in used_names:
        base = sheet_name[:28]
        i    = 2
        while f"{base}_{i}" in used_names:
            i += 1
        sheet_name = f"{base}_{i}"
    used_names.add(sheet_name)
    title      = cfg.get("title", sheet_name)
    chart_type = cfg.get("chart_type")
    x_col      = cfg.get("x_col", "")
    y_col      = cfg.get("y_col", "")
    text_analysis = cfg.get("text", "").strip()

    # 复用优先级：result_name（按名取）> use_last_result / 空 data_expr（取上次）
    if res_name or use_last or not data_expr.strip():
        if _sid_getter is None:
            result = "❌ 无法复用结果：sid 访问器未配置。"
        else:
            from services import df_registry
            sid = _sid_getter()
            if res_name:
                last = df_registry.get_named_result(sid, res_name)
                not_found_hint = f"❌ 未找到名为 '{res_name}' 的结果。请确认 analyse_tool 时已用相同 result_name 命名。"
            else:
                last = df_registry.get_last_result(sid)
                not_found_hint = "❌ 没有可复用的上次分析结果，请先调用 analyse_tool 或提供 data_expr。"
            if last is None:
                result = not_found_hint
            elif not isinstance(last, pd.DataFrame):
                result = (f"❌ 复用的结果不是 DataFrame（实际类型：{type(last).__name__}），"
                          "无法写入 Sheet。请提供 data_expr 重新计算。")
            elif last.empty:
                result = "❌ 复用的结果为空 DataFrame，无法写入 Sheet。"
            else:
                result = last
    else:
        result = _exec_expr(data_expr, df_master, dfs)
    if isinstance(result, str):
        ws = wb.add_worksheet(sheet_name)
        ws.write(0, 0, f"⚠️ 数据生成出错：{result[:500]}", fmts["error"])
        return result

    result = result.copy().replace([float("inf"), float("-inf")], None)
    # 按列分类决定 Excel 数字格式：text（文本）/ int（整数）/ float（小数）
    col_class: dict[str, str] = {}
    for col in result.columns:
        if isinstance(result[col].dtype, pd.CategoricalDtype):
            result[col] = result[col].astype(str).fillna("")
            col_class[col] = "text"
        elif pd.api.types.is_datetime64_any_dtype(result[col]):
            result[col] = result[col].dt.strftime("%Y-%m-%d").fillna("")
            col_class[col] = "text"
        elif result[col].dtype == object:
            result[col] = result[col].fillna("").astype(str)
            col_class[col] = "text"
        else:
            cls = _classify_column(col, result[col])
            col_class[col] = cls
            if cls == "text" and pd.api.types.is_numeric_dtype(result[col]):
                # 手机号/身份证等数值列 → 转字符串，避免科学计数法与前导 0 丢失
                result[col] = result[col].where(pd.notnull(result[col]), None)
                result[col] = result[col].apply(
                    lambda x: str(int(x)) if x is not None and x == x else ""
                )
            else:
                result[col] = result[col].where(pd.notnull(result[col]), None)

    if chart_type and (not x_col or not y_col):
        from core.chart_advisor import recommend_for_columns
        x_col, y_col, chart_type, _ = recommend_for_columns(result)

    ncols = len(result.columns)
    ws    = wb.add_worksheet(sheet_name)
    ws.set_zoom(95)
    ws.merge_range(0, 0, 0, max(ncols - 1, 0), title, fmts["title"])
    ws.set_row(0, 28)
    ws.set_row(1, 22)

    for ci, col_name in enumerate(result.columns):
        ws.write(1, ci, str(col_name), fmts["header"])
        ws.set_column(ci, ci, max(len(str(col_name)) * 2 + 4, 14))

    for ri, row in enumerate(result.itertuples(index=False)):
        is_even = ri % 2 == 0
        for ci, val in enumerate(row):
            col_name = result.columns[ci]
            if isinstance(val, float) and val != val:
                val = ""
            if isinstance(val, np.integer):
                val = int(val)
            elif isinstance(val, np.floating):
                val = float(val)
            elif isinstance(val, np.bool_):
                val = bool(val)
            # 按列类型选格式：text/int/float 分别有 even/odd 两种底色
            cls = col_class.get(col_name, "text")
            if cls == "int":
                fmt_key = "int_even" if is_even else "int_odd"
            elif cls == "float":
                fmt_key = "float_even" if is_even else "float_odd"
            else:
                fmt_key = "even" if is_even else "odd"
            ws.write(ri + 2, ci, val, fmts[fmt_key])

    ws.freeze_panes(2, 0)

    if chart_type:
        img_path = _make_chart_png(result, x_col, y_col, chart_type, title)
        if img_path:
            ws.insert_image(len(result) + 4, 0, img_path,
                            {"x_scale": 0.85, "y_scale": 0.85})
        else:
            ws.write(len(result) + 4, 0,
                     f"⚠️ 图表生成失败（x_col={x_col}, y_col={y_col}）",
                     fmts["error"])

    # 文字分析放在 sheet 最下面（图表下方留 20 行空间）
    if text_analysis:
        text_start = len(result) + 4 + (20 if chart_type else 2)
        ws.merge_range(text_start, 0, text_start, max(ncols - 1, 0),
                       "文字分析", fmts["text_title"])
        for i, line in enumerate(text_analysis.split("\n")):
            row = text_start + 1 + i
            ws.merge_range(row, 0, row, max(ncols - 1, 0), line, fmts["text_body"])
            ws.set_row(row, 22)
    return None


@tool
def excel_tool(params: str) -> str:
    """
    生成样式精美的多 Sheet Excel 报告，返回 XLSX:<路径> 供 UI 渲染下载按钮。
    params 为 JSON：{
        "filename": "...",
        "sheets": [
            {
                "sheet_name": "...",        # Sheet 名（必填）
                "title": "...",             # 标题（可选，默认用 sheet_name）
                # 数据来源三选一（按优先级）：
                "result_name": "...",       # ① 复用已命名的 analyse_tool 结果（推荐）
                "use_last_result": true,    # ② 复用最后一次 analyse_tool 结果（单表场景）
                "data_expr": "result = ...",# ③ 重新计算（仅在无可复用结果时用）
                # 可选图表：
                "chart_type": "bar",        # bar / barh / pie / line
                "x_col": "...", "y_col": "...",
                # 可选文字分析（放在 sheet 最下面，图表下方）：
                "text": "分析结论文字...\n可多行"
            }
        ]
    }
    复用已有结果时严禁同时写 data_expr。推荐优先用 result_name，省 token 且能导出完整数据。
    """
    try:
        p = json.loads(params)
    except Exception:
        return "❌ 参数解析失败，请传入合法 JSON。"

    filename      = re.sub(r"[^\w\-_]", "_", p.get("filename", "分析报告"))
    sheets_config = p.get("sheets", [])
    if not sheets_config:
        return "❌ sheets 列表不能为空。"

    df = _df_accessor()
    if df is None:
        return "❌ 没有加载数据集。"

    dfs = {}
    if _dfs_accessor is not None:
        try:
            dfs = _dfs_accessor() or {}
        except Exception:
            dfs = {}

    output_path = os.path.join(OUTPUT_DIR, f"{filename}.xlsx")
    wb = None
    try:
        wb = xlsxwriter.Workbook(output_path, {"nan_inf_to_errors": True})
        fmts = {
            "title":    wb.add_format({
                            "bold": True, "font_size": 15, "font_color": "#1F4E79",
                            "font_name": "微软雅黑", "bottom": 2, "bottom_color": "#2E75B6",
                        }),
            "header":   wb.add_format({
                            "bold": True, "bg_color": "#2E75B6", "font_color": "#FFFFFF",
                            "border": 1, "border_color": "#FFFFFF",
                            "align": "center", "valign": "vcenter", "font_name": "微软雅黑",
                        }),
            "even":     wb.add_format({
                            "bg_color": "#EBF3FB", "border": 1, "border_color": "#D6E4F0",
                            "font_name": "微软雅黑", "valign": "vcenter",
                        }),
            "odd":      wb.add_format({
                            "bg_color": "#FFFFFF", "border": 1, "border_color": "#D6E4F0",
                            "font_name": "微软雅黑", "valign": "vcenter",
                        }),
            # 整数列：千分位无小数（年龄、天数、笔数、计数等）
            "int_even": wb.add_format({
                            "bg_color": "#EBF3FB", "border": 1, "border_color": "#D6E4F0",
                            "font_name": "微软雅黑", "num_format": "#,##0", "align": "right",
                        }),
            "int_odd":  wb.add_format({
                            "bg_color": "#FFFFFF", "border": 1, "border_color": "#D6E4F0",
                            "font_name": "微软雅黑", "num_format": "#,##0", "align": "right",
                        }),
            # 小数列：千分位两位小数（金额、比率等）
            "float_even": wb.add_format({
                            "bg_color": "#EBF3FB", "border": 1, "border_color": "#D6E4F0",
                            "font_name": "微软雅黑", "num_format": "#,##0.00", "align": "right",
                        }),
            "float_odd":  wb.add_format({
                            "bg_color": "#FFFFFF", "border": 1, "border_color": "#D6E4F0",
                            "font_name": "微软雅黑", "num_format": "#,##0.00", "align": "right",
                        }),
            "error":    wb.add_format({"font_color": "#C00000", "font_name": "微软雅黑"}),
            "text_title": wb.add_format({
                            "bold": True, "font_size": 12, "font_color": "#1F4E79",
                            "font_name": "微软雅黑", "bottom": 1, "bottom_color": "#B4C7E7",
                        }),
            "text_body": wb.add_format({
                            "font_name": "微软雅黑", "font_size": 11,
                            "font_color": "#333333", "valign": "top",
                            "text_wrap": True,
                        }),
        }

        errors = []
        used_names = set()
        for cfg in sheets_config:
            try:
                err = _write_sheet(wb, cfg, df, dfs, fmts, used_names)
            except Exception as e:
                err = f"Sheet 生成异常：{e}"
            if err:
                errors.append(err)

        result_line = f"XLSX:{output_path}"
        if errors:
            result_line += f"\n⚠️ {len(errors)} 个 Sheet 生成时有错误，请检查 data_expr。"
        return result_line
    except Exception as e:
        return f"❌ Excel 生成失败：{e}"
    finally:
        if wb is not None:
            wb.close()
