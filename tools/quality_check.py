# tools/quality_check.py
"""
tools/quality_check.py — 确定性数据质量检测与异常明细 Excel（只分析，不动值）。

检测（不走 LLM）：
  1. 重复值：完全重复行数 / 重复率 / 明细
  2. 空值：每列缺失数、缺失率、严重等级、含空值行
  3. 金额/数值异常：负数、零值、IQR 离群值、无法解析的数值格式
  4. 日期合法性：范围、未来日期

Excel 报告：export_quality_excel 把各类异常的具体行拎出来导出。
本工具只分析、不修改任何数据。

超出通用规则的自定义检查（跨字段一致性、特定业务取值域等）
由 LLM（analyse_tool）按用户要求生成代码处理。
"""
import os
import uuid

import numpy as np
import pandas as pd

from config import (
    QUALITY_NULL_WARN, QUALITY_NULL_BAD, QUALITY_IQR_MULT,
    QUALITY_EXCEL_MAX_ROWS, QUALITY_MONEY_KEYWORDS,
    OUTPUT_DIR,
)


def _money_cols(df: pd.DataFrame) -> list[str]:
    """候选金额列：列名命中金额关键词的列 + 全部数值列（去重）。"""
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
    """完全重复行检测。"""
    dup = df.duplicated()
    n = int(dup.sum())
    return {
        "dup_rows": n,
        "dup_rate": n / len(df) if len(df) else 0.0,
        "sample":   df[dup],
    }


def check_nulls(df: pd.DataFrame) -> list[dict]:
    """每列空值数 / 空值率 / 等级。"""
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
    """金额/数值异常：负数、零值、IQR 离群值、无法解析的数值格式。"""
    rows = []
    for col in _money_cols(df):
        raw = df[col]
        if pd.api.types.is_numeric_dtype(raw):
            s = raw.dropna()
            if s.empty:
                continue
            neg  = int((s < 0).sum())
            zero = int((s == 0).sum())
            q1, q3 = s.quantile([0.25, 0.75]).values
            iqr = q3 - q1
            lo, hi = q1 - QUALITY_IQR_MULT * iqr, q3 + QUALITY_IQR_MULT * iqr
            out = int(((s < lo) | (s > hi)).sum())
            if neg > 0 or out > 0:
                rows.append({
                    "字段": col, "负数": neg, "零值": zero, "离群值": out,
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
    """日期列范围与未来日期检查。"""
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
    """汇总各维度检查结果并生成总体结论。"""
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
        if r.get("离群值"):
            parts.append(f"离群值 {r['离群值']} 个")
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


# ── Excel 报告 ───────────────────────────────────────────────────────────────

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
    """把 DataFrame 写成一个带样式的工作表（标题 + 表头 + 斑马纹）。"""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")

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


def _anomaly_rows(df: pd.DataFrame, report: dict) -> pd.DataFrame:
    """把数值异常的具体行拎出来：负数、离群值、无法解析的值。"""
    rows = []
    for r in report["money"]:
        col = r["字段"]
        raw = df[col]
        if "无法解析" in r:
            conv = pd.to_numeric(raw, errors="coerce")
            mask = conv.isna() & raw.notna()
            for idx in raw[mask].index:
                rows.append({"原行号": idx, "字段": col, "异常类型": "无法解析",
                             "值": raw[idx]})
        else:
            s = raw.dropna()
            if s.empty:
                continue
            q1, q3 = s.quantile([0.25, 0.75]).values
            iqr = q3 - q1
            lo, hi = q1 - QUALITY_IQR_MULT * iqr, q3 + QUALITY_IQR_MULT * iqr
            mask = (raw < 0) | (raw < lo) | (raw > hi)
            for idx in raw[mask].index:
                v = raw[idx]
                typ = "负数" if v < 0 else "离群值"
                rows.append({"原行号": idx, "字段": col, "异常类型": typ, "值": v})
    return pd.DataFrame(rows)


def _date_anomaly_rows(df: pd.DataFrame, report: dict) -> list[dict]:
    """日期异常的具体行：未来日期、空日期（NaT）。"""
    rows = []
    today = pd.Timestamp.now().normalize()
    for r in report["dates"]:
        col = r["字段"]
        raw = df[col]
        for idx in raw[raw > today].index:
            rows.append({"原行号": idx, "字段": col, "异常类型": "未来日期",
                         "值": raw[idx].strftime("%Y-%m-%d")})
        for idx in raw[raw.isna()].index:
            rows.append({"原行号": idx, "字段": col, "异常类型": "空日期", "值": ""})
    return rows


def export_quality_excel(report: dict, df: pd.DataFrame,
                         data_desc: str = "数据集") -> str:
    """把检测结果与各类异常明细导出为 Excel，返回文件路径。"""
    import xlsxwriter
    path = os.path.join(OUTPUT_DIR, f"数据质量检测报告_{uuid.uuid4().hex[:6]}.xlsx")
    wb = xlsxwriter.Workbook(path, {"nan_inf_to_errors": True})
    fmts = _excel_fmts(wb)
    cap = QUALITY_EXCEL_MAX_ROWS

    # 1) 总览
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

    # 2) 重复值明细
    dup = report["duplicates"]["sample"]
    if len(dup):
        dup_tbl = dup.reset_index().rename(columns={"index": "原行号"})
        title = f"重复值明细（共 {len(dup)} 行，展示前 {min(len(dup), cap)} 行）"
        _write_df_sheet(wb, "重复值明细", dup_tbl.head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "重复值明细", pd.DataFrame({"提示": ["未发现完全重复行"]}),
                        fmts, "重复值明细")

    # 3) 空值明细
    null_tbl = pd.DataFrame([
        {"字段": r["字段"], "空值数": r["空值数"],
         "空值率": f"{r['空值率']:.1%}", "等级": r["等级"]}
        for r in report["nulls"]
    ]) if report["nulls"] else pd.DataFrame({"提示": ["各字段均无空值"]})
    _write_df_sheet(wb, "空值明细", null_tbl, fmts, "空值汇总（各字段缺失情况）")

    # 4) 数值异常明细
    ano = _anomaly_rows(df, report)
    if len(ano):
        title = f"金额数值异常明细（共 {len(ano)} 行，展示前 {min(len(ano), cap)} 行）"
        _write_df_sheet(wb, "金额数值异常明细", ano.head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "金额数值异常明细",
                        pd.DataFrame({"提示": ["未发现金额/数值异常"]}), fmts,
                        "金额数值异常明细")

    # 5) 日期异常明细
    date_rows = _date_anomaly_rows(df, report)
    if date_rows:
        title = f"日期异常明细（共 {len(date_rows)} 行，展示前 {min(len(date_rows), cap)} 行）"
        _write_df_sheet(wb, "日期异常明细", pd.DataFrame(date_rows).head(cap), fmts, title)
    else:
        _write_df_sheet(wb, "日期异常明细",
                        pd.DataFrame({"提示": ["未发现日期异常"]}), fmts, "日期异常明细")

    wb.close()
    return path


# ── 报告文本 ─────────────────────────────────────────────────────────────────

def _table_md(df: pd.DataFrame) -> str:
    """DataFrame → Markdown 表格；无 tabulate 时降级为手工拼表。"""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    try:
        return df.to_markdown(index=False)
    except ImportError:
        cols  = [str(c) for c in df.columns]
        lines = ["| " + " | ".join(cols) + " |",
                 "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in df.itertuples(index=False):
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        return "\n".join(lines)


def format_quality_report(report: dict, data_desc: str = "数据集") -> str:
    """把检测结果渲染为带 TABLE: 标记的 Markdown 文本（供 UI 解析渲染）。"""
    L = []
    L.append("📋 数据质量检测报告")
    L.append(f"数据集：{data_desc} ｜ {report['rows']:,} 行 × {report['cols']} 列")
    L.append("")
    L.append(f"**总体结论：** {report['conclusion']}")
    L.append("")

    # 1️⃣ 重复值
    L.append("### 1️⃣ 重复值")
    d = report["duplicates"]
    if d["dup_rows"]:
        L.append(f"发现 **{d['dup_rows']}** 行完全重复，重复率 **{d['dup_rate']:.2%}**。")
        sample = d["sample"].reset_index().rename(columns={"index": "原行号"}).head(5)
        L.append("")
        L.append("TABLE:")
        L.append(_table_md(sample))
        L.append(f"（重复行示例，前 {len(sample)} 行）")
    else:
        L.append("✅ 未发现完全重复行。")
    L.append("")

    # 2️⃣ 空值
    L.append("### 2️⃣ 空值")
    if report["nulls"]:
        tbl = pd.DataFrame([
            {"字段": r["字段"], "空值数": r["空值数"],
             "空值率": f"{r['空值率']:.1%}", "等级": r["等级"]}
            for r in report["nulls"]
        ])
        L.append("TABLE:")
        L.append(_table_md(tbl))
    else:
        L.append("✅ 各字段均无空值。")
    L.append("")

    # 3️⃣ 金额/数值异常
    L.append("### 3️⃣ 金额/数值异常")
    if report["money"]:
        rows = []
        for r in report["money"]:
            if "无法解析" in r:
                rows.append({"字段": r["字段"],
                             "问题": f"{r['无法解析']} 个值无法解析为数字",
                             "样例": r["样例"]})
            else:
                prob = []
                if r["负数"]:
                    prob.append(f"负数 {r['负数']} 个")
                if r["离群值"]:
                    prob.append(f"离群值 {r['离群值']} 个")
                rows.append({
                    "字段": r["字段"],
                    "问题": "；".join(prob),
                    "样例": (f"范围 {r['最小值']:,.2f} ~ {r['最大值']:,.2f}"
                             f"（零值 {r['零值']} 个）"),
                })
        L.append("TABLE:")
        L.append(_table_md(pd.DataFrame(rows)))
    else:
        L.append("✅ 未发现金额/数值异常。")
    L.append("")

    # 4️⃣ 日期合法性
    L.append("### 4️⃣ 日期合法性")
    if report["dates"]:
        L.append("TABLE:")
        L.append(_table_md(pd.DataFrame(report["dates"])))
    else:
        L.append("ℹ️ 未识别到日期字段。")
    L.append("")

    L.append("---")
    L.append("💡 本轮仅分析，不会修改任何数据；可点击下方按钮导出 Excel 分析报告。"
             "如需自定义规则（如跨字段一致性、特定业务取值域），直接说明需求，"
             "AI 将按你的规则生成代码检查。")
    return "\n".join(L)
