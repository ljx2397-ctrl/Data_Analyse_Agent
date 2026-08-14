# backend/core/skills/_quality_check_impl.py
# Skill「数据质量检测」的内部实现模块。
# 只被 quality_check_skill.py 调用，不作为 LangChain Tool 暴露给 Agent。
# 功能：重复值 / 空值 / 金额异常 / 日期合法性 检测 + Excel 报告导出。
import os
import uuid

import numpy as np
import pandas as pd

from core.config import (
    QUALITY_NULL_WARN, QUALITY_NULL_BAD,
    QUALITY_EXCEL_MAX_ROWS, QUALITY_MONEY_KEYWORDS,
    OUTPUT_DIR,
)

# Markdown 表格最多显示行数（避免表格过长影响阅读）
_DISPLAY_MAX_ROWS = 10


def _money_cols(df: pd.DataFrame) -> list[str]:
    hit = [c for c in df.columns
           if any(k in str(c).lower() for k in QUALITY_MONEY_KEYWORDS)]
    num = df.select_dtypes(include="number").columns.tolist()
    seen, cols = set(), []
    for c in hit + num:
        if c not in seen:
            seen.add(c)
            cols.append(c)
    return cols


def check_duplicates(df: pd.DataFrame) -> dict:
    """检测完全重复行。keep=False 标记所有参与重复的行（含原始行）。
    重复行按所有列排序，使相同的行两两相邻，方便对比。
    """
    dup_all = df.duplicated(keep=False)       # 所有重复组内的行（含原始）
    dup_cnt = int(df.duplicated().sum())       # 实际重复行数（不含原始）
    sample = df[dup_all].sort_values(by=list(df.columns))
    return {
        "dup_rows": dup_cnt,
        "dup_rate": dup_cnt / len(df) if len(df) else 0.0,
        "sample": sample,                       # 排序后，相同行相邻
    }


def check_nulls(df: pd.DataFrame) -> list[dict]:
    rows = []
    for col in df.columns:
        n = int(df[col].isna().sum())
        if n == 0:
            continue
        rate = n / len(df) if len(df) else 0.0
        level = ("严重" if rate >= QUALITY_NULL_BAD
                 else "偏高" if rate >= QUALITY_NULL_WARN else "轻微")
        rows.append({"字段": col, "空值数": n, "空值率": rate, "等级": level})
    return rows


def check_money(df: pd.DataFrame) -> list[dict]:
    """检测金额/数值列的负数和无法解析的值（已移除离群值检测）。"""
    rows = []
    for col in _money_cols(df):
        raw = df[col]
        if pd.api.types.is_numeric_dtype(raw):
            s = raw.dropna()
            if s.empty:
                continue
            neg  = int((s < 0).sum())
            zero = int((s == 0).sum())
            if neg > 0:
                rows.append({
                    "字段": col, "负数": neg, "零值": zero,
                    "最小值": s.min(), "最大值": s.max(),
                })
        else:
            conv = pd.to_numeric(raw, errors="coerce")
            bad = int(conv.isna().sum() - raw.isna().sum())
            if bad > 0:
                sample = raw[conv.isna() & raw.notna()].astype(str).head(3).tolist()
                rows.append({"字段": col, "无法解析": bad, "样例": "、".join(sample)})
    return rows


def check_dates(df: pd.DataFrame) -> list[dict]:
    rows = []
    today = pd.Timestamp.now().normalize()
    for col in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        s = df[col].dropna()
        rows.append({
            "字段": col,
            "范围": f"{s.min().date()} ~ {s.max().date()}" if len(s) else "全部为空",
            "未来日期": int((s > today).sum()),
            "空值": int(df[col].isna().sum()),
        })
    return rows


def run_quality_check(df: pd.DataFrame) -> dict:
    dup   = check_duplicates(df)
    nulls = check_nulls(df)
    money = check_money(df)
    dates = check_dates(df)

    issues = []
    if dup["dup_rows"]:
        issues.append(f"发现 {dup['dup_rows']} 行完全重复（重复率 {dup['dup_rate']:.2%}）")
    for r in nulls:
        if r["等级"] != "轻微":
            issues.append(f"字段「{r['字段']}」空值率 {r['空值率']:.1%}（{r['等级']}）")
    for r in money:
        parts = []
        if r.get("负数"):
            parts.append(f"负数 {r['负数']} 个")
        if r.get("无法解析"):
            parts.append(f"无法解析 {r['无法解析']} 个")
        if parts:
            issues.append(f"字段「{r['字段']}」：{'、'.join(parts)}")
    for r in dates:
        if r["未来日期"]:
            issues.append(f"字段「{r['字段']}」含 {r['未来日期']} 个未来日期")

    if not issues:
        conclusion = "✅ 未发现明显数据质量问题"
    elif len(issues) <= 3:
        conclusion = "⚠️ 发现少量数据质量问题，可选择性处理"
    else:
        conclusion = "🔴 发现较多数据质量问题，建议核对原始数据或导出明细进一步确认"

    return {
        "rows": len(df), "cols": len(df.columns),
        "duplicates": dup, "nulls": nulls, "money": money, "dates": dates,
        "issues": issues, "conclusion": conclusion,
    }


# ── Excel 导出 ──────────────────────────────────────────────────────────────

def _excel_fmts(wb):
    return {
        "title":  wb.add_format({"bold": True, "font_size": 14, "font_color": "#1F4E79",
                                 "font_name": "微软雅黑", "bottom": 2, "bottom_color": "#2E75B6"}),
        "header": wb.add_format({"bold": True, "bg_color": "#2E75B6", "font_color": "#FFFFFF",
                                 "border": 1, "align": "center", "valign": "vcenter",
                                 "font_name": "微软雅黑"}),
        "even":   wb.add_format({"bg_color": "#EBF3FB", "border": 1, "border_color": "#D6E4F0",
                                 "font_name": "微软雅黑"}),
        "odd":    wb.add_format({"bg_color": "#FFFFFF", "border": 1, "border_color": "#D6E4F0",
                                 "font_name": "微软雅黑"}),
        "num_even": wb.add_format({"bg_color": "#EBF3FB", "border": 1, "border_color": "#D6E4F0",
                                   "font_name": "微软雅黑", "num_format": "#,##0.00"}),
        "num_odd":  wb.add_format({"bg_color": "#FFFFFF", "border": 1, "border_color": "#D6E4F0",
                                   "font_name": "微软雅黑", "num_format": "#,##0.00"}),
        "warn":   wb.add_format({"font_color": "#C00000", "font_name": "微软雅黑"}),
    }


def _write_df_sheet(wb, name: str, df: pd.DataFrame, fmts: dict, title: str) -> None:
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
        if isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype(str)
    ws = wb.add_worksheet(name)
    ws.set_zoom(95)
    ws.merge_range(0, 0, 0, max(len(df.columns) - 1, 0), title, fmts["title"])
    ws.set_row(0, 24)
    for ci, c in enumerate(df.columns):
        ws.write(1, ci, str(c), fmts["header"])
        width = max(len(str(c)) * 2 + 4, 14)
        if df[c].dtype == object:
            try:
                width = max(width, int(df[c].astype(str).str.len().max()) + 2)
            except Exception:
                pass
        ws.set_column(ci, ci, min(width, 40))
    num_cols = set(df.select_dtypes(include="number").columns)
    for ri, row in enumerate(df.itertuples(index=False)):
        is_even = ri % 2 == 0
        for ci, val in enumerate(row):
            col = df.columns[ci]
            if isinstance(val, float) and val != val:
                val = ""
            elif isinstance(val, np.integer):
                val = int(val)
            elif isinstance(val, np.floating):
                val = float(val)
            elif isinstance(val, np.bool_):
                val = bool(val)
            fmt_key = ("num_even" if is_even else "num_odd") if col in num_cols \
                else ("even" if is_even else "odd")
            ws.write(ri + 2, ci, val, fmts[fmt_key])
    ws.freeze_panes(2, 0)


def export_quality_excel(report: dict, df: pd.DataFrame,
                         data_desc: str = "数据集") -> str:
    import xlsxwriter
    path = os.path.join(OUTPUT_DIR, f"数据质量检测报告_{uuid.uuid4().hex[:6]}.xlsx")
    wb = xlsxwriter.Workbook(path, {"nan_inf_to_errors": True})
    fmts = _excel_fmts(wb)
    cap = QUALITY_EXCEL_MAX_ROWS

    ws = wb.add_worksheet("总览")
    ws.set_zoom(95)
    ws.merge_range(0, 0, 0, 3, "📋 数据质量检测报告", fmts["title"])
    ws.set_row(0, 26)
    overview = [
        ("数据集",   data_desc),
        ("规模",     f"{report['rows']:,} 行 × {report['cols']} 列"),
        ("总体结论", report["conclusion"]),
        ("检测时间", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    for i, (k, v) in enumerate(overview):
        ws.write(2 + i, 0, k, fmts["header"])
        ws.write(2 + i, 1, v)
    ws.write(6, 0, "问题清单", fmts["header"])
    if report["issues"]:
        for i, issue in enumerate(report["issues"]):
            ws.write(7 + i, 0, issue, fmts["warn"] if i < 3 else fmts["odd"])
    else:
        ws.write(7, 0, "未发现明显数据质量问题", fmts["odd"])

    # 重复值明细：原始数据（含原始行和重复行）
    dup_sample = report["duplicates"]["sample"]
    if len(dup_sample):
        title = f"重复值明细（共 {report['duplicates']['dup_rows']} 行重复，"
        title += f"涉及 {len(dup_sample)} 行，展示前 {min(len(dup_sample), cap)} 行）"
        _write_df_sheet(wb, "重复值明细", dup_sample.head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "重复值明细", pd.DataFrame({"提示": ["未发现完全重复行"]}),
                        fmts, "重复值明细")

    # 空值明细：含空值的原始数据行
    null_mask = df.isna().any(axis=1)
    null_rows = df[null_mask]
    if len(null_rows):
        title = f"空值明细（共 {len(null_rows)} 行含空值，展示前 {min(len(null_rows), cap)} 行）"
        _write_df_sheet(wb, "空值明细", null_rows.head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "空值明细", pd.DataFrame({"提示": ["各字段均无空值"]}),
                        fmts, "空值明细")

    # 金额异常明细：含负数的原始数据行
    neg_rows_list = []
    for r in report["money"]:
        col = r["字段"]
        if r.get("负数"):
            neg_rows_list.append(df[df[col] < 0])
    if neg_rows_list:
        neg_rows = pd.concat(neg_rows_list).drop_duplicates()
        title = f"金额数值异常明细（共 {len(neg_rows)} 行，展示前 {min(len(neg_rows), cap)} 行）"
        _write_df_sheet(wb, "金额数值异常明细", neg_rows.head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "金额数值异常明细",
                        pd.DataFrame({"提示": ["未发现金额/数值异常"]}), fmts,
                        "金额数值异常明细")

    # 日期异常明细：含未来日期的原始数据行
    today = pd.Timestamp.now().normalize()
    date_rows_list = []
    for r in report["dates"]:
        col = r["字段"]
        if r["未来日期"]:
            date_rows_list.append(df[df[col] > today])
    if date_rows_list:
        date_rows = pd.concat(date_rows_list).drop_duplicates()
        title = f"日期异常明细（共 {len(date_rows)} 行，展示前 {min(len(date_rows), cap)} 行）"
        _write_df_sheet(wb, "日期异常明细", date_rows.head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "日期异常明细",
                        pd.DataFrame({"提示": ["未发现日期异常"]}), fmts,
                        "日期异常明细")

    wb.close()
    return path


# ── Markdown 报告格式化 ─────────────────────────────────────────────────────

def _table_md(df: pd.DataFrame) -> str:
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
        if isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype(str)
    try:
        return df.to_markdown(index=False)
    except ImportError:
        cols  = [str(c) for c in df.columns]
        lines = ["| " + " | ".join(cols) + " |",
                 "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in df.itertuples(index=False):
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        return "\n".join(lines)


def _fmt_df_for_display(df: pd.DataFrame, cap: int = _DISPLAY_MAX_ROWS) -> pd.DataFrame:
    """准备 DataFrame 用于 Markdown 展示：截断行数、转换日期/分类列。"""
    df = df.head(cap).copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
        if isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype(str)
    return df


def format_quality_report(report: dict, df: pd.DataFrame,
                          data_desc: str = "数据集") -> str:
    """把检测结果渲染为 Markdown 文本，直接展示原始数据行。"""
    L = []
    L.append("📋 数据质量检测报告")
    L.append(f"数据集：{data_desc} ｜ {report['rows']:,} 行 × {report['cols']} 列")
    L.append("")
    L.append(f"**总体结论：** {report['conclusion']}")
    L.append("")

    # ── 1️⃣ 重复值 ──
    L.append("### 1️⃣ 重复值")
    d = report["duplicates"]
    if d["dup_rows"]:
        L.append(f"发现 **{d['dup_rows']}** 行完全重复，重复率 **{d['dup_rate']:.2%}**。")
        L.append("以下为所有重复行（含原始行，可对比）：")
        L.append("")
        L.append(_table_md(_fmt_df_for_display(d["sample"])))
        total = len(d["sample"])
        if total > _DISPLAY_MAX_ROWS:
            L.append(f"（共 {total} 行，仅展示前 {_DISPLAY_MAX_ROWS} 行）")
    else:
        L.append("✅ 未发现完全重复行。")
    L.append("")

    # ── 2️⃣ 空值 ──
    L.append("### 2️⃣ 空值")
    if report["nulls"]:
        summary = "、".join(
            f"「{r['字段']}」{r['空值数']} 个（{r['空值率']:.1%}，{r['等级']}）"
            for r in report["nulls"]
        )
        L.append(summary)
        L.append("以下为含空值的原始数据：")
        L.append("")
        null_rows = df[df.isna().any(axis=1)]
        L.append(_table_md(_fmt_df_for_display(null_rows)))
        total = len(null_rows)
        if total > _DISPLAY_MAX_ROWS:
            L.append(f"（共 {total} 行，仅展示前 {_DISPLAY_MAX_ROWS} 行）")
    else:
        L.append("✅ 各字段均无空值。")
    L.append("")

    # ── 3️⃣ 金额/数值异常 ──
    L.append("### 3️⃣ 金额/数值异常")
    if report["money"]:
        for r in report["money"]:
            if "无法解析" in r:
                L.append(f"- 字段「{r['字段']}」：{r['无法解析']} 个值无法解析为数字（样例：{r['样例']}）")
            else:
                L.append(f"- 字段「{r['字段']}」：负数 {r['负数']} 个，零值 {r['零值']} 个"
                         f"（范围 {r['最小值']:,.2f} ~ {r['最大值']:,.2f}）")
        # 收集含负数的原始数据行
        neg_parts = []
        for r in report["money"]:
            col = r["字段"]
            if r.get("负数"):
                neg_parts.append(df[df[col] < 0])
        if neg_parts:
            neg_rows = pd.concat(neg_parts).drop_duplicates()
            L.append("")
            L.append("以下为异常数据：")
            L.append("")
            L.append(_table_md(_fmt_df_for_display(neg_rows)))
            total = len(neg_rows)
            if total > _DISPLAY_MAX_ROWS:
                L.append(f"（共 {total} 行，仅展示前 {_DISPLAY_MAX_ROWS} 行）")
    else:
        L.append("✅ 未发现金额/数值异常。")
    L.append("")

    # ── 4️⃣ 日期合法性 ──
    L.append("### 4️⃣ 日期合法性")
    if report["dates"]:
        for r in report["dates"]:
            L.append(f"- 字段「{r['字段']}」：范围 {r['范围']}，"
                     f"未来日期 {r['未来日期']} 个，空值 {r['空值']} 个")
        # 收集含未来日期的原始数据行
        today = pd.Timestamp.now().normalize()
        date_parts = []
        for r in report["dates"]:
            col = r["字段"]
            if r["未来日期"]:
                date_parts.append(df[df[col] > today])
        if date_parts:
            date_rows = pd.concat(date_parts).drop_duplicates()
            L.append("")
            L.append("以下为异常数据：")
            L.append("")
            L.append(_table_md(_fmt_df_for_display(date_rows)))
            total = len(date_rows)
            if total > _DISPLAY_MAX_ROWS:
                L.append(f"（共 {total} 行，仅展示前 {_DISPLAY_MAX_ROWS} 行）")
    else:
        L.append("ℹ️ 未识别到日期字段。")
    L.append("")

    L.append("---")
    L.append("💡 本轮仅分析，不会修改任何数据；可点击下方按钮导出 Excel 分析报告。"
             "如需自定义规则（如跨字段一致性、特定业务取值域），直接说明需求，"
             "AI 将按你的规则生成代码检查。")
    return "\n".join(L)
