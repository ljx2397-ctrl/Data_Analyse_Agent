# agent.py
# 组装 LangGraph ReAct Agent。
# 通过 _df_getter 注入模式，确保工具始终操作当前会话的 DataFrame，
# 避免多会话切换时出现数据错乱。

import streamlit as st
from langgraph.prebuilt import create_react_agent

from llm_core import create_llm, build_system_prompt  # ← 从 llm_core 读取
import tools.schema_tool  as _st
import tools.analyse_tool as _at
import tools.chart_tool   as _ct
import tools.excel_tool   as _et   # ← 修复：原来错写成 report_tool
from tools import all_tools


@st.cache_resource
def get_agent(session_id: str, data_desc: str, columns: tuple, num_cols: tuple,
              cat_cols: tuple, date_cols: tuple, _df_getter):
    """
    构建并缓存 Agent 实例。
    columns 等参数使用 tuple 而非 list，因为 st.cache_resource 要求参数可哈希。
    _df_getter 以下划线开头，告知 Streamlit 不参与缓存 key 计算。

    每次文件上传或会话切换后，需要在外部调用 get_agent.clear() 清除缓存，
    使 set_df_accessor 重新注入新的 DataFrame。
    """
    import pandas as pd

    # 构造临时 df 仅用于生成系统提示（实际分析用 _df_getter）
    df = _df_getter()

    # ── 注入 df 访问器到各工具模块 ────────────────────────────────────────
    _st.set_df_accessor(_df_getter)
    _at.set_df_accessor(_df_getter)
    _ct.set_df_accessor(_df_getter)
    _et.set_df_accessor(_df_getter)   # ← 修复：原来是 _rt

    # ── 创建 LLM 和系统提示 ───────────────────────────────────────────────
    llm    = create_llm()
    prompt = build_system_prompt(df, data_desc) if df is not None else "暂无数据"

    return create_react_agent(model=llm, tools=all_tools, prompt=prompt)
