# data_loader.py
"""
data_loader.py
负责：
1. 读取 CSV / Excel（xlsx、xlsm）文件。
2. 接受单独的头文件以提供列名。
3. 自动将类似日期的列转换为 datetime 类型。
4. 将干净的 DataFrame 提供给应用程序的其他部分。
"""
import io
import pandas as pd


def detect_separator(raw_bytes: bytes) -> str:
    sample = raw_bytes[:4096].decode("utf-8-sig", errors="ignore")
    pipe_count  = sample.count("|")
    comma_count = sample.count(",")
    return "|" if pipe_count > comma_count else ","


def extract_columns_from_header_file(header_bytes: bytes) -> list[str]:
    """
    用户上传的第二个文件就是字段头。
    支持三种常见格式：
      - 单行 CSV:   col1,col2,col3
      - 单行竖线:   col1|col2|col3
      - 单列:       每行一个字段名
    返回清洗后的字段名字符串列表。
    """
    text = header_bytes.decode("utf-8-sig", errors="ignore").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if not lines:
        return []

    first = lines[0]
    if "|" in first:
        return [c.strip() for c in first.split("|") if c.strip()]
    if "," in first:
        return [c.strip() for c in first.split(",") if c.strip()]
    # 每行一个
    return lines


def load_dataframe(
        data_bytes: bytes,
        column_names: list[str] | None = None,
) -> pd.DataFrame:
    sep = detect_separator(data_bytes)

    # 自动检测编码，按优先级逐一尝试
    encodings = ["utf-8-sig", "gbk", "gb2312", "gb18030", "utf-8", "latin-1"]

    last_err = None
    for enc in encodings:
        try:
            file_obj = io.BytesIO(data_bytes)
            if column_names:
                df = pd.read_csv(
                    file_obj, sep=sep, header=None,
                    names=column_names, encoding=enc,
                )
            else:
                df = pd.read_csv(
                    file_obj, sep=sep, encoding=enc,
                )
            # 成功则跳出循环
            break
        except (UnicodeDecodeError, Exception) as e:
            last_err = e
            continue
    else:
        raise ValueError(f"无法识别文件编码，已尝试：{encodings}。最后错误：{last_err}")

    return _coerce_dates(df)


def _coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    """自动将名称含日期关键字的列转为 datetime（保持原逻辑）。"""
    for col in df.columns:
        col_lower = str(col).lower()
        if any(kw in col_lower for kw in ("日期", "时间", "date", "time", "创建", "更新")):
            df[col] = pd.to_datetime(df[col], errors="coerce")

    return df


def detect_file_kind(file_name: str | None, raw: bytes | None = None) -> str:
    """按扩展名/魔数判断文件类型：csv / xlsx / xls。"""
    name = (file_name or "").lower()
    if raw is not None and len(raw) >= 4:
        if raw[:4] == b"PK\x03\x04":
            return "xlsx"          # xlsx/xlsm 均为 zip 容器
        if raw[:4] == b"\xd0\xcf\x11\xe0":
            return "xls"           # 旧版 OLE2 格式，需另存为 xlsx
    if name.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if name.endswith(".xls"):
        return "xls"
    return "csv"


def list_excel_sheets(data_bytes: bytes) -> list[str]:
    """返回 xlsx 工作簿的所有 Sheet 名。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data_bytes), read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def load_excel(data_bytes: bytes, sheet_name,
               column_names: list[str] | None = None) -> pd.DataFrame:
    """
    读取 xlsx/xlsm 指定 Sheet。
    提供 column_names 时把首行视为数据（与 CSV 的无标题行逻辑一致）。
    """
    if not data_bytes:
        raise ValueError("Excel 文件为空")
    try:
        df = pd.read_excel(
            io.BytesIO(data_bytes),
            sheet_name=sheet_name,
            header=None if column_names else 0,
            names=column_names or None,
        )
    except ImportError:
        raise ValueError(
            "读取 Excel 需要 openpyxl，请先安装：pip install openpyxl"
        ) from None
    return _coerce_dates(df)

def llm_parse_header_file(header_bytes: bytes) -> list[str]:
    """
    将字段说明文件原始内容发给 LLM，要求其提取清洗后的字段名列表。
    失败时返回空列表 []。
    """
    import os, json
    from openai import OpenAI  # 已通过 langchain-openai 间接依赖
    from config import LLM_MODEL, LLM_API_BASE, LLM_API_KEY_ENV

    text = header_bytes.decode("utf-8-sig", errors="ignore").strip()
    if not text:
        return []

    client = OpenAI(
        api_key=os.environ.get(LLM_API_KEY_ENV, ""),
        base_url=LLM_API_BASE,
    )
    prompt = f"""下面是一个字段说明文件的原始内容，请从中提取所有字段名，
返回一个 JSON 数组，格式为 ["字段1", "字段2", ...]。
只返回 JSON，不要任何解释或 markdown 代码块。

文件内容：
{text[:3000]}"""

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1024,
        )
        raw = resp.choices[0].message.content.strip()
        # 去除可能意外出现的 ```json 围栏
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        cols = json.loads(raw)
        if isinstance(cols, list) and all(isinstance(c, str) for c in cols):
            return [c.strip() for c in cols if c.strip()]
    except Exception:
        pass
    return []
