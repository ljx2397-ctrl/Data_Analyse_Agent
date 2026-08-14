# backend/core/agent.py
# 改造自原 agent.py：移除 streamlit 依赖（@st.cache_resource / import streamlit）。
# LLM 客户端创建、系统提示构建、字段匹配提示、Agent 组装均保留原逻辑。
# _df_getter 注入模式不变，由 agent_service 按 session_id 提供。
import os

import pandas as pd
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from core.config import (
    LLM_MODEL, LLM_API_BASE, LLM_API_KEY_ENV,
    LLM_TEMPERATURE, LLM_STREAMING,
)
from services.llm_config import get_config
import core.tools.schema_tool  as _st
import core.tools.analyse_tool as _at
import core.tools.chart_tool   as _ct
import core.tools.excel_tool   as _et
from core.tools import all_tools


# LLM 客户端单例：避免每次对话都新建 httpx 连接池 + TLS 握手。
# 原 Streamlit 版用 @st.cache_resource 缓存，迁移后改为模块级单例。
_llm_instance: ChatOpenAI | None = None


def create_llm() -> ChatOpenAI:
    """获取 LangChain 封装的 OpenAI 兼容客户端（单例）。
    优先使用运行时配置（前端可修改），回退到 settings.py 默认值。
    """
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance
    cfg = get_config()
    _llm_instance = ChatOpenAI(
        model=cfg.get("model") or LLM_MODEL,
        openai_api_base=cfg.get("api_base") or LLM_API_BASE,
        openai_api_key=cfg.get("api_key") or os.environ.get(LLM_API_KEY_ENV, ""),
        temperature=cfg.get("temperature", LLM_TEMPERATURE),
        streaming=LLM_STREAMING,
        stream_usage=True,   # 让流式响应末块携带 usage（token 计量用；服务商不支持时静默缺失）
        max_retries=3,
    )
    return _llm_instance


def reset_llm() -> None:
    """配置变更后重置 LLM 单例，下次 create_llm 会用新配置重建。"""
    global _llm_instance
    _llm_instance = None


def build_system_prompt(df: pd.DataFrame, data_desc: str,
                        dfs: dict | None = None) -> str:
    """根据当前 DataFrame 动态生成 Agent 系统提示。
    dfs 为多表字典 {表名: DataFrame}，存在时在提示中说明多表用法。
    """
    num_cols  = df.select_dtypes(include="number").columns.tolist()
    cat_cols  = df.select_dtypes(include="object").columns.tolist()
    date_cols = [
        c for c in df.columns
        if pd.api.types.is_datetime64_any_dtype(df[c])
    ]

    # 多表信息
    multi_table_section = ""
    if dfs and len(dfs) > 1:
        table_list = "\n".join(
            f"  - dfs[\"{name}\"]  {len(d):,} 行 × {len(d.columns)} 列"
            for name, d in dfs.items()
        )
        multi_table_section = f"""

━━━━ 多表联合分析 ━━━━
当前共加载了 {len(dfs)} 张数据表，可通过 dfs 字典访问：
{table_list}

- 主表 df 是第一张上传的表，也可通过 dfs["表名"] 访问。
- 多表关联（类似 SQL JOIN）使用 pd.merge：
  merged = pd.merge(df, dfs["客户表"], on="客户ID", how="left")
- how 参数：left（左连接）、right（右连接）、inner（内连接）、outer（全连接）。
- on 参数指定关联字段，列名不同时用 left_on / right_on。
- 关联后结果赋值给 result 返回。
- 调用 schema_tool 可查看所有表的字段明细，确认关联字段。
"""

    return f"""你是一位专业的数据分析师，当前加载的数据集为：{data_desc}
初始数据规模：{len(df):,} 行 × {len(df.columns)} 列
注意：若已调用 reload_df 重新加载数据，实际行数以 reload_df 返回消息或 schema_tool 返回的总行数为准，禁止引用此初始值。
数值字段：{num_cols}
分类字段：{cat_cols}
日期字段：{date_cols}
{multi_table_section}

━━━━ 工具调用规则 ━━━━

【schema_tool】
- ⚠️ 同一会话只需调用一次 schema_tool 确认列名，后续所有问题直接基于已返回的字段信息分析，严禁重复调用。
- 工具内部会记录调用状态：第二次调用时只返回精简提示（不再重复完整 schema），省 token。
- 仅在以下情况需要重新调用：① reload_df 重新加载了列名；② 上传了新数据集。
- query 填用户问题中的关键概念，如"交易金额"、"客户类型"。
- 严禁在未调用 schema_tool 的情况下猜测列名。
- 多表场景下首次调用会展示所有表的 schema，确认关联字段时务必查看。

【analyse_tool】
- 工具返回结果后，用 2~4 句自然语言写出关键结论。
- 用于执行所有统计、过滤、聚合、趋势、TopN 分析。
- code 参数中必须将最终结果赋值给变量 result。
- 时间聚合必须先将 datetime 列转为字符串，再 groupby，再 reset_index()。
- 禁止在 code 中调用 plt、import 新库、写删文件、访问网络
- 多表场景：code 中可通过 dfs["表名"] 访问其他表，用 pd.merge 做关联。
- ⭐ **result_name 必填**：每次调用都要为本次结果起个简短名字（如 "支行排名"、"aum分层"）。
  命名后，后续 excel_tool / chart_tool 可通过相同 result_name 直接复用完整 result，
  无需重跑代码。这是省 token 的关键——不命名会导致后续导出/画图时被迫重新计算。
- ⚠️ **禁止在 code 中自己加"总计/合计"行**（如 result.loc["总计"] = result.sum()）。
  工具会自动在表格末尾追加合计行——人数/客户数等计数列求和，max/min/median 等显示"-"。
  你自己加的合计行会对所有数值列求和，产生无意义的"最大值之和"。

【chart_tool】
- ⚠️ 严格限定：只有用户在提问中明确出现"画图/图表/可视化/折线/柱状/饼图/趋势图/分布图"等画图意图词时才允许调用。
- 用户未提及画图时，严禁主动调用 chart_tool，即使你认为可视化会有帮助；此时只返回文字结论 + 表格。
- 参数为 JSON 字符串。
- 不填 chart_type 时工具自动推荐并说明理由。
- data_expr 中禁止调用 plt。
- y_col 可传字符串（单系列）或数组（多系列），多系列时 data_expr 结果需含对应列。
- 画多张图时必须分别调用 chart_tool，每次只过滤一个条件；禁止在一次调用中混合多个分类数据。
- ⭐ **复用规则（必读）**：当用户要求对"上面/刚才/刚刚那个结果"画图时，
  **必须复用**已有 result，禁止重写 data_expr 重新计算。两种方式：
  ① 传 "result_name": "之前命名的名字"（推荐，多表场景按名取用）
  ② 传 "use_last_result": true（单表场景，复用最后一次 analyse_tool 的 result）
  此时 x_col / y_col 仍需指定，但 data_expr 必须省略。

【excel_tool】
- 仅当用户明确说"导出/Excel/报告/下载"时调用。
- 参数为 JSON 字符串，包含 filename 和 sheets 数组。
- ⭐ **复用规则（必读）**：当用户要求"把上面/刚才的结果导出"时，
  **必须复用**已有 result，禁止重写 data_expr 重新计算。两种方式：
  ① 每个 sheet 传 "result_name": "之前命名的名字"（推荐，多 Sheet 场景按名取用）
  ② 每个 sheet 传 "use_last_result": true（单表场景，复用最后一次 result）
  复用时 data_expr 必须省略。这样能导出未截断的完整数据，且零重复计算。
- 仅在"用户要导出全新分析结果"且之前没有对应 analyse_tool 时，才需要写 data_expr。
  此时每个 sheet 的 data_expr 独立执行，必须将最终 DataFrame 赋值给 result 变量。
- data_expr 示例：result = df.groupby("支行")["金额"].sum().reset_index()
- 工具完成后返回 XLSX: 标记，UI 自动渲染下载按钮。

【知识库查询】
- 业务术语/口径定义/字段匹配已移交 RAG Agent 在对话前处理，你不再持有 knowledge_search_tool。
- 如果列名已正常（由 RAG Agent 修复或原始就有），直接基于现有列名分析。
- 如果用户在对话中追问业务术语，告知用户这类问题会在下一轮自动由 RAG Agent 处理。

【reload_df 函数（在 analyse_tool 代码中可用）】
- 调用 reload_df(["字段1","字段2",...]) 用指定列名重新加载原始数据文件。
- 典型场景：RAG Agent 已通过知识库匹配到字段文件并执行了 reload_df，通常无需手动调用。
- 重新加载后下一次 analyse_tool 调用时 df 将使用新列名，且不会丢失第一行数据。
- reload_df 返回成功/失败消息（字符串），不需赋值给 result。
- 禁止直接用 df.columns = [...] 覆盖列名（会导致第一行数据丢失），必须用 reload_df。

━━━━ 标准工作流（务必严格遵守） ━━━━

【场景 1：首次数据分析】
  动作序列：schema_tool(一次) → analyse_tool(result_name="xxx") → 文字结论

【场景 2：基于上次结果导出 Excel】（用户说"把上面/刚才的数据导出"）
  动作序列：excel_tool(params=...)
  params JSON 示例：
    {{"filename":"报告名", "sheets":[{{"sheet_name":"Sheet1", "use_last_result":true}}]}}
  ⚠️ 禁止调 schema_tool（已调过）！禁止调 analyse_tool 重算！
  直接用 use_last_result: true 复用上次 result，一步到位。

【场景 3：基于上次结果画图】（用户说"把上面的数据画图"）
  动作序列：chart_tool(params=...)
  params JSON 示例：
    {{"x_col":"列名", "y_col":"列名", "use_last_result":true}}
  ⚠️ 同上，禁止重算，直接复用。

【场景 4：全新的分析 + 导出】（用户说"分析 XX 并导出"）
  动作序列：analyse_tool(code=..., result_name="xxx") → excel_tool(params=...)
  excel params 示例：
    {{"filename":"报告名", "sheets":[{{"sheet_name":"Sheet1", "result_name":"xxx"}}]}}

【场景 5：多表分析 + 多 Sheet 导出】
  动作序列：analyse_tool(code=..., result_name="表1") → analyse_tool(code=..., result_name="表2") → ...
         → excel_tool(params=...)
  excel params 示例：
    {{"filename":"报告名", "sheets":[{{"sheet_name":"S1","result_name":"表1"}},{{"sheet_name":"S2","result_name":"表2"}}]}}

  关键：每次 analyse_tool 都必须带 result_name，后续 excel_tool 按 result_name 取用。

⚠️ 注意：sheet_name / result_name / use_last_result / data_expr 都是工具的 JSON 参数名，
  不是 pandas 代码里的变量。analyse_tool 的 code 参数中禁止使用这些名字作为变量名。
  code 中只能用：df、dfs、pd、np、datetime、re、json、result。

━━━━ 输出格式规范 ━━━━
- TABLE: 标记和 CHART:/XLSX: 路径由工具返回，直接转发，不要修改。
- 每个标记单独占一行，前后不加多余文字。
- 最终回复使用中文自然语言，不向用户暴露代码。

━━━━ data_expr 代码规范 ━━━━
1. 只能使用：df、dfs、pd、np、datetime、re、json
2. 最终结果必须赋值给 result
3. 时间处理示例：
   tmp = df.copy()
   tmp["月份"] = tmp["交易日期"].dt.strftime("%Y-%m")
   result = tmp.groupby("月份")["金额"].sum().reset_index()
4. groupby 后必须立即 reset_index()
5. 禁止对字符串列调用 .mean()
6. 禁止访问 __ 开头的魔法属性，禁止 import / exec / eval / getattr 等越权能力
7. 统计行数必须使用 len(df)，禁止用 df.index.max() 或 df.index[-1]（那是末行索引，比行数少 1）
"""


def get_agent(df: pd.DataFrame, data_desc: str, _df_getter, _dfs_getter=None):
    """
    构建 Agent 实例（无缓存）。
    每次 session 切换/文件上传后由 agent_service 调用重建。
    _df_getter 注入到各工具模块，确保工具始终操作当前会话的 DataFrame。
    _dfs_getter 注入多表访问器，返回 {表名: DataFrame} 字典（可选）。
    """
    # 注入 df / dfs 访问器到各工具模块
    _st.set_df_accessor(_df_getter)
    _at.set_df_accessor(_df_getter)
    _ct.set_df_accessor(_df_getter)
    _et.set_df_accessor(_df_getter)
    if _dfs_getter is not None:
        _st.set_dfs_accessor(_dfs_getter)
        _at.set_dfs_accessor(_dfs_getter)
        _ct.set_dfs_accessor(_dfs_getter)
        _et.set_dfs_accessor(_dfs_getter)

    # 构建 prompt 时传入 dfs（如果有）
    dfs = None
    if _dfs_getter is not None:
        try:
            # _dfs_getter 需要 datasets 参数，这里用无参版本
            # agent_service 会通过闭包注入 datasets
            dfs = _dfs_getter()
        except Exception:
            dfs = None

    llm    = create_llm()
    prompt = build_system_prompt(df, data_desc, dfs) if df is not None else "暂无数据"
    return create_react_agent(model=llm, tools=all_tools, prompt=prompt)
