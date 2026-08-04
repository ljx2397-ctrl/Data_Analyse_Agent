# llm_core.py
# 职责：封装 LLM 客户端创建和系统提示构建。
# agent.py 只调用这里的函数，不直接操作 LangChain 对象，
# 便于日后更换模型或调整提示词时只改动此一处。

import os
import pandas as pd
from langchain_openai import ChatOpenAI

from config import (
    LLM_MODEL, LLM_API_BASE, LLM_API_KEY_ENV,
    LLM_TEMPERATURE, LLM_STREAMING,
)


def create_llm() -> ChatOpenAI:
    """
    创建并返回 LangChain 封装的 OpenAI 兼容客户端。
    模型名、地址、密钥全部从环境变量或 config.py 读取。
    """
    return ChatOpenAI(
        model=LLM_MODEL,
        openai_api_base=LLM_API_BASE,
        openai_api_key=os.environ.get(LLM_API_KEY_ENV, ""),
        temperature=LLM_TEMPERATURE,
        streaming=LLM_STREAMING,
        max_retries=3,   # 连接建立前失败的自动重试次数（流式中途断开由前端重试兜底）
    )


def build_system_prompt(df: pd.DataFrame, data_desc: str) -> str:
    """
    根据当前加载的 DataFrame 动态生成 Agent 系统提示。

    设计原则：
    - 提示保持精简，字段详情由 schema_tool 实时提供，避免 token 浪费。
    - 明确工具调用顺序：schema_tool → analyse_tool → chart_tool / excel_tool。
    - 明确输出标记语法：TABLE: / CHART: / XLSX:，UI 层依赖这些标记渲染。
    - 明确 data_expr 的代码规范，减少 LLM 生成错误代码的概率。
    """
    # 提取基础元数据注入提示，让 LLM 对数据有初步认知
    num_cols  = df.select_dtypes(include="number").columns.tolist()
    cat_cols  = df.select_dtypes(include="object").columns.tolist()
    date_cols = [
        c for c in df.columns
        if pd.api.types.is_datetime64_any_dtype(df[c])
    ]

    return f"""你是一位专业的数据分析师，当前加载的数据集为：{data_desc}
数据规模：{len(df):,} 行 × {len(df.columns)} 列
数值字段：{num_cols}
分类字段：{cat_cols}
日期字段：{date_cols}

━━━━ 工具调用规则 ━━━━

【schema_tool】
- 收到任何数据分析问题，第一次必须先调用schema_tool此工具确认真实列名,后续直接分析
- query 填用户问题中的关键概念，如"交易金额"、"客户类型"。
- 严禁在未调用 schema_tool 的情况下猜测列名。

【analyse_tool】
- 工具返回结果后，用 2~4 句自然语言写出关键结论。
- 用于执行所有统计、过滤、聚合、趋势、TopN 分析。
- code 参数中必须将最终结果赋值给变量 result。
- 时间聚合必须先将 datetime 列转为字符串，再 groupby，再 reset_index()。
- 禁止在 code 中调用 plt、import 新库、写删文件、访问网络

【chart_tool】
- 仅当用户明确说"画图/图表/可视化/折线/柱状/饼图"时调用。
- 参数为 JSON 字符串，必须包含 data_expr、x_col、y_col。
- 不填 chart_type 时工具自动推荐并说明理由。
- data_expr 中禁止调用 plt。

【excel_tool】
- 仅当用户明确说"导出/Excel/报告/下载"时调用。
- 参数为 JSON 字符串，包含 filename 和 sheets 数组。
- 每个 sheet 的 data_expr 独立执行，结果赋给 result。
- 工具完成后返回 XLSX: 标记，UI 自动渲染下载按钮。

━━━━ 输出格式规范 ━━━━
- TABLE: 标记和 CHART:/XLSX: 路径由工具返回，直接转发，不要修改。
- 每个标记单独占一行，前后不加多余文字。
- 最终回复使用中文自然语言，不向用户暴露代码。

━━━━ data_expr 代码规范 ━━━━
1. 只能使用：df、pd、np、datetime、re、json
2. 最终结果必须赋值给 result
3. 时间处理示例：
   tmp = df.copy()
   tmp["月份"] = tmp["交易日期"].dt.strftime("%Y-%m")
   result = tmp.groupby("月份")["金额"].sum().reset_index()
4. groupby 后必须立即 reset_index()
5. 禁止对字符串列调用 .mean()
6. 禁止访问 __ 开头的魔法属性，禁止 import / exec / eval / getattr 等越权能力
"""

def get_field_hint(df: pd.DataFrame, query: str) -> str:
    """
    针对用户单次 query 生成字段匹配提示，可追加到 HumanMessage 末尾。
    目的：在 agent 开始推理前预先缩小列名候选范围，减少幻觉。
    若 SchemaIndex 构建失败则静默返回空字符串，不影响主流程。
    """
    from schema_matcher import SchemaIndex

    if df is None or not query.strip():
        return ""

    try:
        idx  = SchemaIndex(df)
        hits = idx.match(query, top_k=5)
        if not hits:
            return ""
        lines = ["\n\n---\n【系统自动匹配的相关字段，仅供参考】"]
        for col, score in hits:
            lines.append(f"  - {col}（相关度 {score:.3f}）")
        return "\n".join(lines)
    except Exception:
        # 降级处理：构建索引失败时不报错，静默跳过
        return ""
