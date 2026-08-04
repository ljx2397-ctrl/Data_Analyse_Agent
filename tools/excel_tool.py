# tools/excel_tool.py
# Excel 报告生成工具（对应架构中的 excel_tool，原名 report_tool）。
# 接收多个 Sheet 配置，生成带样式和内嵌图表的多 Sheet Excel 文件。
# 每个 Sheet 独立执行 data_expr，写入美化表格，可选渲染图表嵌入单元格下方。

import os
import re
import json
import uuid

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import xlsxwriter
from langchain.tools import tool

from config import CHARTS_DIR, OUTPUT_DIR, CHART_COLORS, BASE_DIR  # ← 直接从 config 导入
from chart_advisor import recommend
from tools.analyse_tool import run_sandboxed   # ← 复用统一的超时沙箱与静态检查

_df_accessor = None

def set_df_accessor(fn):
    """由 agent.py 注入，返回当前 DataFrame 的可调用对象"""
    global _df_accessor
    _df_accessor = fn


def _exec_expr(data_expr: str, df: pd.DataFrame) -> pd.DataFrame | str:
    """
    在安全沙箱中执行 data_expr。
    成功返回 DataFrame，失败返回 traceback 字符串。
    """
    # 静态检查 + 超时执行（复用 analyse_tool 的统一沙箱）
    try:
        ok, err, local_vars = run_sandboxed(data_expr, df)
    except ValueError as e:
        return f"代码未通过安全检查：{e}"
    if not ok:
        return err

    result = local_vars.get("result")
    if result is None:
        dfs = [v for v in local_vars.values() if isinstance(v, pd.DataFrame)]
        result = dfs[0] if dfs else pd.DataFrame({"提示": ["data_expr 未产生 result 变量"]})
    return result


def _make_chart_png(result_df: pd.DataFrame, x_col: str, y_col: str,
                    chart_type: str, title: str) -> str | None:
    """
    为 Excel Sheet 生成内嵌图表 PNG，返回文件路径；失败返回 None。
    图表类型不合法或列不存在时静默返回 None。
    """
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

        # 文件名消毒，并加 uuid 防止并发冲突
        safe = re.sub(r"[^\w]", "_", title)
        img_path = os.path.join(CHARTS_DIR, f"xl_{safe}_{uuid.uuid4().hex[:6]}.png")
        plt.savefig(img_path, dpi=130, bbox_inches="tight")
        plt.close()
        return img_path
    except Exception:
        return None


def _write_sheet(wb: xlsxwriter.Workbook, cfg: dict,
                 df_master: pd.DataFrame, fmts: dict,
                 used_names: set) -> str | None:
    """
    写入单个 Sheet。
    返回 None 表示成功，返回错误字符串表示失败。
    """
    data_expr  = cfg.get("data_expr", "")
    # 清洗 Excel 非法字符（[]:*?/\ 等），并确保 Sheet 名不重复
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
    chart_type = cfg.get("chart_type")          # 可为 None，表示不画图
    x_col      = cfg.get("x_col", "")
    y_col      = cfg.get("y_col", "")

    # 执行数据生成代码
    result = _exec_expr(data_expr, df_master)
    if isinstance(result, str):
        # 执行出错时，在 Sheet 里写入错误信息
        ws = wb.add_worksheet(sheet_name)
        ws.write(0, 0, f"⚠️ 数据生成出错：{result[:500]}", fmts["error"])
        return result

    # ── 数据清洗：处理 inf / NaT / NaN ──────────────────────────────────
    result = result.copy().replace([float("inf"), float("-inf")], None)
    for col in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[col]):
            result[col] = result[col].dt.strftime("%Y-%m-%d").fillna("")
        elif result[col].dtype == object:
            result[col] = result[col].fillna("").astype(str)
        else:
            result[col] = result[col].where(pd.notnull(result[col]), None)

    # ── 如果 chart_type 指定了但 x/y_col 未填，自动推断 ─────────────────
    if chart_type and (not x_col or not y_col):
        from chart_advisor import recommend_for_columns
        x_col, y_col, chart_type, _ = recommend_for_columns(result)

    ncols = len(result.columns)
    ws    = wb.add_worksheet(sheet_name)
    ws.set_zoom(95)

    # 标题行（合并单元格）
    ws.merge_range(0, 0, 0, max(ncols - 1, 0), title, fmts["title"])
    ws.set_row(0, 28)
    ws.set_row(1, 22)

    # 表头行
    for ci, col_name in enumerate(result.columns):
        ws.write(1, ci, str(col_name), fmts["header"])
        ws.set_column(ci, ci, max(len(str(col_name)) * 2 + 4, 14))

    # 数据行（斑马条纹）
    num_col_set = set(result.select_dtypes(include="number").columns)
    for ri, row in enumerate(result.itertuples(index=False)):
        is_even = ri % 2 == 0
        for ci, val in enumerate(row):
            col_name = result.columns[ci]
            # 对 NaN 进行安全检查
            if isinstance(val, float) and val != val:
                val = ""
            # numpy 标量转 Python 原生类型，否则 xlsxwriter 无法写入
            if isinstance(val, np.integer):
                val = int(val)
            elif isinstance(val, np.floating):
                val = float(val)
            elif isinstance(val, np.bool_):
                val = bool(val)
            fmt_key = (
                ("num_even" if is_even else "num_odd")
                if col_name in num_col_set
                else ("even" if is_even else "odd")
            )
            ws.write(ri + 2, ci, val, fmts[fmt_key])

    ws.freeze_panes(2, 0)   # 冻结前两行（标题+表头）

    # ── 可选：嵌入图表图片 ────────────────────────────────────────────────
    if chart_type:
        img_path = _make_chart_png(result, x_col, y_col, chart_type, title)
        if img_path:
            ws.insert_image(len(result) + 4, 0, img_path,
                            {"x_scale": 0.85, "y_scale": 0.85})
        else:
            ws.write(len(result) + 4, 0,
                     f"⚠️ 图表生成失败（x_col={x_col}, y_col={y_col}）",
                     fmts["error"])
    return None


@tool
def excel_tool(params: str) -> str:
    """
    生成样式精美的多 Sheet Excel 报告，返回 XLSX:<路径> 供 UI 渲染下载按钮。

    params 为 JSON 字符串：
    {{
      "filename": "报告文件名（不含扩展名）",
      "sheets": [
        {{
          "data_expr":  "Python代码，结果赋值给 result",
          "sheet_name": "Sheet标签名（最多31字符）",
          "title":      "Sheet内标题",
          "chart_type": "bar | barh | pie | line | null",
          "x_col":      "图表x轴列名（有chart_type时填）",
          "y_col":      "图表y轴列名（有chart_type时填）"
        }}
      ]
    }}

    data_expr 规范（与 analyse_tool 相同）：
    - 结果赋值给 result（DataFrame）
    - 只用 df、pd、np
    - 时间列先转字符串再 groupby
    - groupby 后立即 reset_index()
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

    output_path = os.path.join(OUTPUT_DIR, f"{filename}.xlsx")
    wb = None
    try:
        wb = xlsxwriter.Workbook(output_path, {"nan_inf_to_errors": True})

        # ── 定义共享格式（仅创建一次，所有 Sheet 复用）──────────────────────
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
        "num_even": wb.add_format({
                        "bg_color": "#EBF3FB", "border": 1, "border_color": "#D6E4F0",
                        "font_name": "微软雅黑", "num_format": "#,##0.00", "align": "right",
                    }),
        "num_odd":  wb.add_format({
                        "bg_color": "#FFFFFF", "border": 1, "border_color": "#D6E4F0",
                        "font_name": "微软雅黑", "num_format": "#,##0.00", "align": "right",
                    }),
        "error":    wb.add_format({"font_color": "#C00000", "font_name": "微软雅黑"}),
        }

        errors = []
        used_names = set()
        for cfg in sheets_config:
            try:
                err = _write_sheet(wb, cfg, df, fmts, used_names)
            except Exception as e:
                err = f"Sheet 生成异常：{e}"
            if err:
                errors.append(err)

        # ← 统一返回绝对路径，与 chart_tool 的 CHART: 标记保持一致，
        #    渲染端 os.path.exists 不再依赖进程工作目录。
        result_line = f"XLSX:{output_path}"
        if errors:
            result_line += f"\n⚠️ {len(errors)} 个 Sheet 生成时有错误，请检查 data_expr。"
        return result_line
    except Exception as e:
        return f"❌ Excel 生成失败：{e}"
    finally:
        if wb is not None:
            wb.close()
