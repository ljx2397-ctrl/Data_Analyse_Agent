# backend/core/render.py
# 改造自原 render.py：只保留 parse_output（纯文本解析，不依赖 Streamlit）。
# render_message / render_table 已移至 Vue 前端实现。
import re


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
