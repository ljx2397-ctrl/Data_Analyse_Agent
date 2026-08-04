# render.py
"""
render.py
所有 Streamlit 渲染逻辑：解析工具输出标记，并展示表格、图片、下载按钮和聊天气泡。
"""
import os
import re
import pandas as pd
import streamlit as st


def parse_output(text: str) -> tuple[str, list, list, list]:
    """
    扫描 Agent 回复中嵌入的标记并拆分。
    返回 (clean_text, chart_paths, xlsx_paths, markdown_tables)。
    """
    charts, xlsxs, tables = [], [], []
    clean_lines = []

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith("CHART:"):
            charts.append(stripped[6:].strip())
            i += 1

        elif stripped.startswith("XLSX:"):
            xlsxs.append(stripped[5:].strip())
            i += 1

        elif stripped == "TABLE:" or stripped.startswith("TABLE:"):
            table_buf = []
            if len(stripped) > 6:
                table_buf.append(stripped[6:].strip())
            i += 1
            while i < len(lines) and (
                lines[i].strip().startswith("|")
                or re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i])
            ):
                table_buf.append(lines[i])
                i += 1
            if table_buf:
                tables.append("\n".join(table_buf))

        else:
            clean_lines.append(lines[i])
            i += 1

    return "\n".join(clean_lines).strip(), charts, xlsxs, tables


def render_table(markdown_table: str):
    """Parse a Markdown table string and display as st.dataframe."""
    try:
        from io import StringIO
        # 去除分隔行（---|--- 这类）
        lines = [
            l for l in markdown_table.split("\n")
            if not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", l)
        ]
        df_tbl = pd.read_csv(
            StringIO("\n".join(lines)),
            sep="|", skipinitialspace=True,
        )
        df_tbl = df_tbl.dropna(axis=1, how="all")
        df_tbl.columns = [c.strip() for c in df_tbl.columns]
        height = min(36 * len(df_tbl) + 38, 420)
        st.dataframe(df_tbl, use_container_width=True, height=height)
    except Exception:
        st.markdown(markdown_table)


def render_message(msg: dict):
    """渲染单条聊天消息及其所有附件。"""
    role = msg["role"]

    if role == "user":
        st.markdown(
            f"""<div class="chat-row user">
                <div class="chat-avatar">🧑</div>
                <div class="chat-bubble user">{msg["content"]}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        return

    # ── Assistant ─────────────────────────────────────────────────────────
    st.markdown("🏦 **数据分析助手**")

    if msg.get("content"):
        st.markdown(msg["content"])

    for i, code in enumerate(msg.get("code_exec", [])):
        with st.expander(f"🛠️ 分析代码 {i + 1}"):
            st.code(code, language="python")

    for tbl in msg.get("tables", []):
        render_table(tbl)

    for cp in msg.get("charts", []):
        if os.path.exists(cp):
            st.image(cp, use_container_width=True)
        else:
            st.warning(f"图表文件不存在：{cp}")

    for xp in msg.get("xlsxs", []):
        if os.path.exists(xp):
            with open(xp, "rb") as f:
                st.download_button(
                    label=f"⬇️ 下载报告 ({os.path.basename(xp)})",
                    data=f,
                    file_name=os.path.basename(xp),
                    mime=("application/vnd.openxmlformats-"
                          "officedocument.spreadsheetml.sheet"),
                    key=f"dl_{id(msg)}_{xp}",
                )
        else:
            st.warning(f"Excel 文件不存在：{xp}")
