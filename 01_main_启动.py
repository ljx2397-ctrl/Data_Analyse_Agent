# 01_main_启动.py
"""
01_main_启动.py — Streamlit 的入口文件。
责任：布局、侧边栏、文件上传流程、聊天输入/输出。
所有业务逻辑都位于其他模块中。
"""
import json
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from config import MAX_HISTORY_TURNS
from session import (init_session_state, current_session, current_messages,
                     append_message, create_new_session,
                     switch_session, delete_session, save_all_sessions)
from data_loader import (load_dataframe, load_excel, detect_file_kind,
                         list_excel_sheets)
from agent import get_agent
from render import parse_output, render_message, render_table
from tools.quality_check import build_quality_report_text

# ── 数据质量检测：确定性通用检查（不走 LLM）──────────────────────────────
# 命中质量关键词 → 直接执行通用 pandas 检查；
# 若同时命中自定义规则关键词（跨字段一致性、业务规则等），仍交给 LLM Agent。
QUALITY_KEYWORDS = (
    "数据质量", "质检", "数据清洗", "清洗", "去重",
    "重复值", "重复行", "空值", "缺失", "金额异常", "异常值", "离群",
)
CUSTOM_QUALITY_KEYWORDS = ("一致", "匹配", "关联", "关系", "规则", "自定义", "联动")


def _is_quality_intent(text: str) -> bool:
    return any(k in text for k in QUALITY_KEYWORDS)


def _is_custom_quality(text: str) -> bool:
    return any(k in text for k in CUSTOM_QUALITY_KEYWORDS)

# ── 页面配置 ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="数据分析 Agent", page_icon="🏦", layout="wide")
st.markdown("""
<style>
.chat-row { display:flex; align-items:flex-start; margin:8px 0; gap:10px; width:100%; }
.chat-row.user { flex-direction:row-reverse; }
.chat-avatar { font-size:22px; flex-shrink:0; line-height:1.2; padding-top:2px; }
.chat-bubble { max-width:80%; padding:10px 14px; font-size:14px; line-height:1.6;
               word-break:break-word; border-radius:12px; box-sizing:border-box; }
.chat-bubble p { margin-top:0!important; margin-bottom:6px!important; }
.chat-bubble p:last-child { margin-bottom:0!important; }
.chat-bubble.user { background:#DCF8C6; border-top-right-radius:2px; color:#1A1A1A; }
.chat-bubble.assistant { background:#F0F2F6; border-top-left-radius:2px; color:#1A1A1A; }
</style>
""", unsafe_allow_html=True)

# ── 会话状态 ───────────────────────────────────────────────────────────────
init_session_state()

# ── 加载当前会话的 DataFrame ───────────────────────────────────────────────
sess = current_session()
df   = None
if sess and sess.get("df_bytes"):
    kind = sess.get("df_kind") or detect_file_kind(sess.get("df_name", ""))
    if kind == "xlsx":
        df = load_excel(sess["df_bytes"],
                        sheet_name=sess.get("df_sheet", 0),
                        column_names=sess.get("df_columns"))
    else:
        df = load_dataframe(sess["df_bytes"],
                            column_names=sess.get("df_columns"))

def _df_getter():
    return df

data_desc = sess.get("df_name", "数据集") if sess else "未加载数据"

# ── 侧边栏 ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🏦 数据分析 Agent")
    if df is not None:
        st.caption(f"样本量：{len(df):,} 条 | 字段数：{len(df.columns)} 个")
    else:
        st.caption("未加载数据")
    st.divider()

    if st.button("➕ 新建对话", use_container_width=True):
        create_new_session()
        st.rerun()

    if st.button("🔍 数据质量检测", use_container_width=True,
                 disabled=(df is None),
                 help="通用规则检查重复值、空值、金额异常，不走大模型"):
        report_text = build_quality_report_text(df, data_desc, "")
        clean_text, _, _, tables = parse_output(report_text)
        append_message("user", "🔍 执行数据质量检测")
        append_message("assistant", clean_text, tables=tables)
        st.session_state.lc_messages.append(HumanMessage(content="🔍 执行数据质量检测"))
        st.session_state.lc_messages.append(AIMessage(content=clean_text))
        st.rerun()

    st.subheader("📋 历史会话")
    for sid, s in sorted(st.session_state.all_sessions.items(), reverse=True):
        is_active = (sid == st.session_state.current_session_id)
        col1, col2 = st.columns([5, 1])
        with col1:
            icon = "▶️" if is_active else "💬"
            if st.button(f"{icon} {s['title']}", key=f"sess_{sid}",
                         use_container_width=True):
                switch_session(sid)
                st.rerun()
        with col2:
            if st.button("🗑️", key=f"del_{sid}", help="删除"):
                delete_session(sid)
                st.rerun()

    st.divider()
    st.subheader("🗂️ 字段参考")
    if df is not None:
        st.code("\n".join(df.columns.tolist()), language=None)
    else:
        st.caption("上传数据文件后显示")

# ── 主区域 ─────────────────────────────────────────────────────────────────
st.header("💬 数据智能分析")

# ── 文件上传流程 ───────────────────────────────────────────────────────────
if df is None:
    if not st.session_state.get("show_upload"):
        st.info("👈 点击左侧「➕ 新建对话」开始使用")
        st.stop()

    st.subheader("📂 上传数据文件")

    data_file   = st.file_uploader(
        "上传数据文件（CSV / Excel，必选）",
        type=["csv", "xlsx", "xlsm"],
        key="data_upload",
    )
    header_file = st.file_uploader(
        "上传字段说明文件（可选）",
        type=["csv", "txt"],
        key="header_upload",
        help="支持任意格式，大模型将自动解析字段名。"
    )

    # ── 缓存上传的原始字节（UploadedFile 只能 read 一次）─────────────────
    upload_kind, sheet_choice = None, None
    if data_file is not None:
        cache_key = f"{getattr(data_file, 'file_id', data_file.name)}|{data_file.size}"
        if st.session_state.get("upload_cache_key") != cache_key:
            st.session_state.upload_raw_bytes = data_file.read()
            st.session_state.upload_cache_key = cache_key
            st.session_state.pop("upload_sheets", None)

        upload_kind = detect_file_kind(data_file.name,
                                       st.session_state.get("upload_raw_bytes"))
        if upload_kind == "xls":
            st.warning("⚠️ 暂不支持旧版 .xls，请在 Excel 中另存为 .xlsx 后重传。")
        elif upload_kind == "xlsx":
            if "upload_sheets" not in st.session_state:
                st.session_state.upload_sheets = list_excel_sheets(
                    st.session_state.upload_raw_bytes
                )
            sheet_choice = st.selectbox(
                "📑 选择工作表",
                st.session_state.upload_sheets,
                key="upload_sheet_select",
            )
    else:
        for _k in ("upload_raw_bytes", "upload_cache_key", "upload_sheets"):
            st.session_state.pop(_k, None)

    # ── 步骤 1：LLM 解析字段说明文件 ───────────────────────────────────
    if header_file and "llm_parsed_columns" not in st.session_state:
        raw_bytes = header_file.read()
        with st.spinner("🤖 大模型正在解析字段说明文件..."):
            from data_loader import llm_parse_header_file
            parsed = llm_parse_header_file(raw_bytes)

        if parsed:
            st.session_state.llm_parsed_columns  = parsed
            st.session_state.llm_header_raw      = raw_bytes
        else:
            st.warning("⚠️ 大模型解析失败，请手动填写字段名或检查文件内容。")
            st.session_state.llm_parsed_columns  = []
            st.session_state.llm_header_raw      = raw_bytes

    # ── 步骤 2：可编辑确认界面 ─────────────────────────────────────────
    confirmed_columns = None

    if "llm_parsed_columns" in st.session_state:
        cols_draft = st.session_state.llm_parsed_columns

        st.markdown("### 🤖 大模型解析结果 — 请确认或修改字段名")
        st.caption(f"共识别到 {len(cols_draft)} 个字段，可直接编辑下方表格后点击确认导入。")

        # 构建一个只有"字段名"列的可编辑 DataFrame
        import pandas as pd
        edit_df = pd.DataFrame({"字段名": cols_draft})
        edited  = st.data_editor(
            edit_df,
            num_rows="dynamic",          # 允许增删行
            use_container_width=True,
            key="col_editor",
        )
        confirmed_columns = (
            edited["字段名"]
            .dropna()
            .astype(str)
            .str.strip()
            .loc[lambda s: s != ""]
            .tolist()
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("🔄 重新解析", use_container_width=True):
                del st.session_state["llm_parsed_columns"]
                st.rerun()
        with col_b:
            confirm_disabled = not (data_file and confirmed_columns
                                    and upload_kind != "xls")
            confirm_clicked  = st.button(
                "✅ 确认导入", use_container_width=True,
                disabled=confirm_disabled,
            )

        if (confirm_clicked and data_file and confirmed_columns
                and upload_kind != "xls"):
            sess = current_session()
            sess["df_bytes"]   = st.session_state.get("upload_raw_bytes")
            sess["df_name"]    = data_file.name
            sess["df_columns"] = confirmed_columns
            sess["df_kind"]    = upload_kind
            sess["df_sheet"]   = sheet_choice if upload_kind == "xlsx" else None
            save_all_sessions(st.session_state.all_sessions,
                              only=[st.session_state.current_session_id])
            # 清理临时状态
            del st.session_state["llm_parsed_columns"]
            st.session_state.show_upload = False
            st.rerun()

    elif data_file and upload_kind != "xls":
        # 无字段说明文件，直接导入不做列名覆盖
        if st.button("✅ 确认导入", use_container_width=True):
            sess = current_session()
            sess["df_bytes"]   = st.session_state.get("upload_raw_bytes")
            sess["df_name"]    = data_file.name
            sess["df_columns"] = None
            sess["df_kind"]    = upload_kind
            sess["df_sheet"]   = sheet_choice if upload_kind == "xlsx" else None
            save_all_sessions(st.session_state.all_sessions,
                              only=[st.session_state.current_session_id])
            st.session_state.show_upload = False
            st.rerun()

    st.stop()

# ── Agent ─────────────────────────────────────────────────────────────────
num_cols  = df.select_dtypes(include="number").columns.tolist()
cat_cols  = df.select_dtypes(include="object").columns.tolist()
date_cols = [c for c in df.columns
             if __import__("pandas").api.types.is_datetime64_any_dtype(df[c])]

agent = get_agent(
    session_id=st.session_state.current_session_id,
    data_desc=data_desc,
    columns=list(df.columns),
    num_cols=num_cols,
    cat_cols=cat_cols,
    date_cols=date_cols,
    _df_getter=_df_getter,
)

# ── 渲染历史消息 ───────────────────────────────────────────────────────────
for msg in current_messages():
    render_message(msg)

# ── 聊天输入 ───────────────────────────────────────────────────────────────
if user_input := st.chat_input("请描述你的分析需求..."):
    render_message({"role": "user", "content": user_input,
                    "charts": [], "xlsxs": [], "tables": []})
    append_message("user", user_input)
    st.session_state.lc_messages.append(HumanMessage(content=user_input))

    # ── 数据质量检测/清洗：确定性通用检查，不走 LLM ──────────────────────
    if _is_quality_intent(user_input) and not _is_custom_quality(user_input):
        with st.chat_message("assistant", avatar="🏦"):
            with st.spinner("🔍 正在执行数据质量检测..."):
                report_text = build_quality_report_text(df, data_desc, user_input)
            clean_text, _, _, tables = parse_output(report_text)
            if clean_text:
                st.markdown(clean_text)
            for tbl in tables:
                render_table(tbl)
        st.session_state.lc_messages.append(AIMessage(content=clean_text))
        append_message("assistant", clean_text, tables=tables)
        st.stop()

    with st.chat_message("assistant", avatar="🏦"):
        status_ph = st.empty()
        stream_ph = st.empty()
        last_err   = None
        max_attempts = 3

        # 网络错误特征：命中才自动重试，确定性错误（如代码 bug）不浪费重试
        NETWORK_ERR_MARKERS = (
            "peer closed connection", "incomplete chunked read",
            "connection", "timeout", "timed out", "read error",
            "remote protocol", "broken pipe", "reset",
            "apiconnectionerror",
        )

        with st.spinner("思考中..."):
            for attempt in range(1, max_attempts + 1):
                full_raw  = ""
                tool_calls_acc = {}
                tool_call_fragments = []
                acc_msg   = None
                try:
                    trimmed = st.session_state.lc_messages[-(MAX_HISTORY_TURNS * 2):]
                    for chunk, meta in agent.stream(
                        {"messages": trimmed}, stream_mode="messages"
                    ):
                        node = meta.get("langgraph_node")
                        if node == "tools":
                            status_ph.info("⚙️ 正在执行分析工具...")
                        if node == "agent":
                            # 累加流式消息，结束后用规范化的 tool_calls 提取代码
                            acc_msg = chunk if acc_msg is None else acc_msg + chunk
                            if chunk.content:
                                status_ph.empty()
                                full_raw += chunk.content
                                stream_ph.markdown(full_raw + "▌")
                        # 捕获 agent 发出的工具调用（用于展示/重跑分析代码）。
                        # 原样收集所有 (name, args) 分片——不按 id 合并，
                        # 因为不同版本/服务商对 id、index 的语义不一致，
                        # 按 id 拼装会把多个调用串到一起（曾导致 query 和 code 合并）。
                        try:
                            for tc in getattr(chunk, "tool_call_chunks", None) or []:
                                if isinstance(tc, dict):
                                    tc_id   = tc.get("id")
                                    tc_name = tc.get("name") or ""
                                    tc_args = tc.get("args") or ""
                                else:
                                    tc_id   = tc.id
                                    tc_name = tc.name or ""
                                    tc_args = tc.args or ""
                                tool_call_fragments.append((tc_name, tc_args))
                                # 仅当 id 明确存在时才按 id 累积（真分片流式兜底）
                                if tc_id:
                                    entry = tool_calls_acc.setdefault(
                                        tc_id, {"name": "", "args": ""}
                                    )
                                    if tc_name:
                                        entry["name"] = tc_name
                                    if tc_args:
                                        entry["args"] += tc_args
                        except Exception:
                            pass
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    if attempt >= max_attempts:
                        break
                    if not any(m in str(e).lower() for m in NETWORK_ERR_MARKERS):
                        break   # 非网络类错误，不重试
                    status_ph.warning(
                        f"⚠️ 网络波动，正在自动重试（{attempt}/{max_attempts - 1}）..."
                    )
                    stream_ph.empty()
                    import time
                    time.sleep(1.5)

        if last_err is not None:
            status_ph.empty()
            stream_ph.empty()
            st.error(f"Agent 执行出错：{last_err}")
            st.info("这通常是 API 网络波动导致的流式中断，直接重新发送问题即可；"
                    "若频繁出现，请检查网络或 API 服务状态。")
            st.stop()

        status_ph.empty()
        stream_ph.empty()

        clean_text, charts, xlsxs, tables = parse_output(full_raw)

        if clean_text:
            st.markdown(clean_text)
        for tbl in tables:
            render_table(tbl)
        for cp in charts:
            import os
            if os.path.exists(cp):
                st.image(cp, use_container_width=True)
        for xp in xlsxs:
            import os
            if os.path.exists(xp):
                with open(xp, "rb") as f:
                    st.download_button(
                        label=f"⬇️ 下载报告 ({os.path.basename(xp)})",
                        data=f,
                        file_name=os.path.basename(xp),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_live_{hash(xp + user_input[:20])}",
                    )

        # ── 从工具调用中提取 analyse_tool 的代码 ─────────────────────────
        code_exec = []
        seen = set()

        def _add_code(code) -> None:
            code = (code or "").strip()
            if code and code not in seen:
                seen.add(code)
                code_exec.append(code)

        def _extract_from_args(args) -> None:
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except Exception:
                    return
            if isinstance(args, dict):
                _add_code(args.get("code"))

        # 方案一：累加消息的规范化 tool_calls（langchain 负责解析）
        try:
            for tc in getattr(acc_msg, "tool_calls", None) or []:
                if not isinstance(tc, dict) or tc.get("name") != "analyse_tool":
                    continue
                _extract_from_args(tc.get("args"))
        except Exception:
            pass

        # 方案二：按"新名字出现"切分片段并拼装（标准 OpenAI 分片流式：
        # 每个工具调用的首个分片带 name，后续分片 name 为空）。
        # 不依赖 id，也不依赖 langchain 内部解析，兼容性最好。
        segments = []
        try:
            for frag_name, frag_args in tool_call_fragments:
                if not isinstance(frag_args, str):
                    continue
                if frag_name:
                    segments.append({"name": frag_name, "args": frag_args})
                elif segments:
                    segments[-1]["args"] += frag_args
            for seg in segments:
                _extract_from_args(seg["args"])
        except Exception:
            pass

        # 方案三：按 id 拼装的分片（真分片式流媒体兜底）
        try:
            for tc in tool_calls_acc.values():
                if tc["name"] != "analyse_tool":
                    continue
                _extract_from_args(tc["args"])
        except Exception:
            pass

        # 方案四：旧式 additional_kwargs.tool_calls（function.arguments 为 JSON 字符串）
        try:
            for tc in (getattr(acc_msg, "additional_kwargs", {}) or {}).get("tool_calls", []) or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if fn.get("name") != "analyse_tool":
                    continue
                _extract_from_args(fn.get("arguments"))
        except Exception:
            pass

        msg_idx = len(current_messages())
        st.session_state.lc_messages.append(AIMessage(content=clean_text))
        append_message("assistant", clean_text,
                       charts=charts, xlsxs=xlsxs, tables=tables,
                       code_exec=code_exec)

        # ── 可编辑代码 + 重新执行 ────────────────────────────────────────
        if code_exec:
            with st.expander(
                f"🛠️ 本次分析代码（{len(code_exec)} 段，可编辑后重新执行）"
            ):
                for i, code in enumerate(code_exec):
                    edited = st.text_area(
                        f"代码 {i + 1}",
                        value=code,
                        height=160,
                        key=f"code_edit_{msg_idx}_{i}",
                    )
                    if st.button("▶️ 重新执行", key=f"code_run_{msg_idx}_{i}"):
                        from tools.analyse_tool import run_analysis_code
                        with st.spinner("正在执行..."):
                            out = run_analysis_code(edited)
                        st.markdown(out)
        elif any(name == "analyse_tool" for name, _ in tool_call_fragments):
            # analyse_tool 被调用了但代码没提取出来：展示原始调用记录便于定位
            with st.expander(
                f"🔍 工具调用记录（本次调用了 analyse_tool 但未能提取代码）"
            ):
                for seg in segments[:20]:
                    st.code(f"{seg['name']}  args={seg['args'][:800]}", language=None)
