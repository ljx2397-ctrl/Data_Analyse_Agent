# tools/quality_check.py
"""
tools/quality_check.py — 确定性数据质量检测与清洗预览（不走 LLM）。

用通用 pandas 规则覆盖常见问题：
  1. 重复值：完全重复行数 / 重复率 / 示例
  2. 空值：每列缺失数、缺失率、严重等级
  3. 金额/数值异常：负数、零值、IQR 离群值、无法解析的数值格式
  4. 日期合法性：范围、未来日期

并提供通用清洗预览：完全去重 + 数值列按中位数填充 + 分类列按众数填充。

仅覆盖通用规则；跨字段一致性、特定业务取值域等自定义检查
超出本模块能力，由 LLM（analyse_tool）按用户要求生成代码处理。
"""
import pandas as pd

from config import (
    QUALITY_NULL_WARN, QUALITY_NULL_BAD, QUALITY_IQR_MULT,
    QUALITY_CLEAN_PREVIEW, QUALITY_MONEY_KEYWORDS,
)


CLEAN_KEYWORDS = ("清洗", "去重", "删掉", "删除重复", "去掉重复",
                  "填充", "补全", "处理空值", "处理缺失")


def wants_clean(text: str) -> bool:
    """判断用户意图是否偏向"清洗"（否则视为"检测"）。"""
    return any(k in text for k in CLEAN_KEYWORDS)


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
        conclusion = "🔴 发现较多数据质量问题，建议先进行数据清洗"

    return {
        "rows": len(df), "cols": len(df.columns),
        "duplicates": dup, "nulls": nulls, "money": money, "dates": dates,
        "issues": issues, "conclusion": conclusion,
    }


def run_clean_preview(df: pd.DataFrame) -> dict:
    """通用清洗预览：完全去重 + 数值列中位数填充 + 分类列众数填充。"""
    notes = []
    cleaned = df.copy()
    before = len(cleaned)

    cleaned = cleaned.drop_duplicates()
    removed = before - len(cleaned)
    if removed:
        notes.append(f"删除完全重复行 **{removed}** 行（重复率 {removed / before:.2%}）")
    else:
        notes.append("未发现完全重复行。")

    for col in cleaned.columns:
        n_null = int(cleaned[col].isna().sum())
        if n_null == 0:
            continue
        if pd.api.types.is_numeric_dtype(cleaned[col]):
            med = cleaned[col].median()
            cleaned[col] = cleaned[col].fillna(med)
            notes.append(f"字段「{col}」{n_null} 个空值 → 按中位数 {med:,.2f} 填充")
        else:
            mode_vals = cleaned[col].mode()
            fill = mode_vals.iloc[0] if len(mode_vals) else "未知"
            cleaned[col] = cleaned[col].fillna(fill)
            notes.append(f"字段「{col}」{n_null} 个空值 → 按众数「{fill}」填充")

    if not any("空值" in n for n in notes):
        notes.append("各字段均无空值，无需填充。")
    return {"notes": notes, "before": before, "cleaned": cleaned}


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
    L.append("💡 以上为通用规则检查（不走大模型）。如需自定义规则（如跨字段一致性、"
             "特定业务取值域），直接说明需求，AI 将按你的规则生成代码检查。")
    return "\n".join(L)


def format_clean_report(res: dict, data_desc: str = "数据集") -> str:
    """把清洗预览渲染为 Markdown 文本。"""
    cleaned = res["cleaned"]
    L = []
    L.append("🧹 数据清洗预览")
    L.append(f"数据集：{data_desc} ｜ 清洗前 {res['before']:,} 行 × {cleaned.shape[1]} 列")
    L.append("")
    L.append("**处理步骤：**")
    for n in res["notes"]:
        L.append(f"- {n}")
    L.append("")
    L.append(f"**清洗后：** {len(cleaned):,} 行 × {cleaned.shape[1]} 列")
    L.append("")
    L.append("TABLE:")
    L.append(_table_md(cleaned.head(QUALITY_CLEAN_PREVIEW)))
    L.append(f"（清洗后数据预览，前 {min(len(cleaned), QUALITY_CLEAN_PREVIEW)} 行）")
    L.append("")
    L.append("⚠️ 当前仅为预览，**不会写回原始数据**；如需保存清洗结果，可让 AI 导出为 Excel。")
    return "\n".join(L)


def build_quality_report_text(df: pd.DataFrame, data_desc: str = "数据集",
                              user_input: str = "") -> str:
    """按用户意图返回检测报告或清洗预览（均为确定性输出，不调用 LLM）。"""
    if wants_clean(user_input):
        return format_clean_report(run_clean_preview(df), data_desc)
    return format_quality_report(run_quality_check(df), data_desc)
