# backend/core/data_loader.py
# 复用原 data_loader.py，仅把 import 路径改为 core 包。
# 职责：读取 CSV/Excel、接受头文件、自动转日期列、LLM 解析字段说明文件。
import io
import re
import pandas as pd


def detect_separator(raw_bytes: bytes) -> str:
    sample = raw_bytes[:4096].decode("utf-8-sig", errors="ignore")
    pipe_count  = sample.count("|")
    comma_count = sample.count(",")
    return "|" if pipe_count > comma_count else ","


def _norm_col(v) -> str:
    """归一化列名用于比较：去空白/下划线/连字符，统一小写。"""
    return re.sub(r"[\s_\-—]+", "", str(v)).strip().lower()


def _first_row_matches_columns(first_row: list[str], column_names: list[str]) -> bool:
    """
    判断数据文件第一行是否已经是表头（即与字段文件解析出的列名基本一致）。
    返回 True 时说明文件自带表头，加载时应按 header=0 读取，避免表头叠加。
    """
    expected = [_norm_col(c) for c in column_names]
    actual   = [_norm_col(v) for v in first_row]
    if not expected or len(actual) != len(expected):
        return False
    exact = sum(1 for a, b in zip(actual, expected) if a == b)
    if exact == len(expected):
        return True
    # 允许少量别名差异（如"客户_名称"vs"客户名称"），要求至少 80% 一致
    threshold = max(2, int(len(expected) * 0.8))
    return exact >= threshold


# 占位列名模式：col1 / column1 / 字段1 / field1 / 空字符串
_PLACEHOLDER_PATTERNS = [
    re.compile(r'^col\d+$', re.IGNORECASE),
    re.compile(r'^column\d+$', re.IGNORECASE),
    re.compile(r'^字段\d+$'),
    re.compile(r'^field\d+$', re.IGNORECASE),
]


def _is_placeholder_header(first_row: list[str]) -> bool:
    """检测首行是否为自动生成的占位列名（col1, col2, ... 等）。
    超过一半的列匹配占位模式 → 判定为占位表头，加载时应跳过此行。
    """
    if not first_row:
        return False
    match_count = 0
    for v in first_row:
        v_str = str(v).strip()
        if any(p.match(v_str) for p in _PLACEHOLDER_PATTERNS):
            match_count += 1
    return match_count >= len(first_row) * 0.5


def extract_columns_from_header_file(header_bytes: bytes) -> list[str]:
    """字段头文件支持三种格式：单行CSV / 单行竖线 / 每行一个。"""
    text = header_bytes.decode("utf-8-sig", errors="ignore").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    first = lines[0]
    if "|" in first:
        return [c.strip() for c in first.split("|") if c.strip()]
    if "," in first:
        return [c.strip() for c in first.split(",") if c.strip()]
    return lines


def load_dataframe(data_bytes: bytes,
                   column_names: list[str] | None = None) -> pd.DataFrame:
    sep = detect_separator(data_bytes)
    encodings = ["utf-8-sig", "gbk", "gb2312", "gb18030", "utf-8", "latin-1"]
    last_err = None
    for enc in encodings:
        try:
            if column_names:
                first = pd.read_csv(io.BytesIO(data_bytes), sep=sep,
                                    header=None, nrows=1, encoding=enc)
                first_row = [str(v).strip() for v in first.iloc[0].tolist()]
                if _first_row_matches_columns(first_row, column_names):
                    # 数据文件自带表头：直接按 header=0 读取，不再叠加字段名
                    df = pd.read_csv(io.BytesIO(data_bytes), sep=sep, encoding=enc)
                elif _is_placeholder_header(first_row):
                    # 首行是占位列名（col1, col2, ...），跳过此行再用字段名
                    df = pd.read_csv(io.BytesIO(data_bytes), sep=sep, header=None,
                                     names=column_names, skiprows=1, encoding=enc)
                else:
                    df = pd.read_csv(io.BytesIO(data_bytes), sep=sep, header=None,
                                     names=column_names, encoding=enc)
            else:
                df = pd.read_csv(io.BytesIO(data_bytes), sep=sep, encoding=enc)
            break
        except (UnicodeDecodeError, Exception) as e:
            last_err = e
            continue
    else:
        raise ValueError(f"无法识别文件编码，已尝试：{encodings}。最后错误：{last_err}")
    return _coerce_dates(df)


def _coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        col_lower = str(col).lower()
        if any(kw in col_lower for kw in ("日期", "时间", "date", "time", "创建", "更新")):
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def detect_file_kind(file_name: str | None, raw: bytes | None = None) -> str:
    name = (file_name or "").lower()
    if raw is not None and len(raw) >= 4:
        if raw[:4] == b"PK\x03\x04":
            return "xlsx"
        if raw[:4] == b"\xd0\xcf\x11\xe0":
            return "xls"
    if name.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if name.endswith(".xls"):
        return "xls"
    return "csv"


def list_excel_sheets(data_bytes: bytes) -> list[str]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data_bytes), read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def load_excel(data_bytes: bytes, sheet_name,
               column_names: list[str] | None = None) -> pd.DataFrame:
    if not data_bytes:
        raise ValueError("Excel 文件为空")
    try:
        if column_names:
            first = pd.read_excel(io.BytesIO(data_bytes), sheet_name=sheet_name,
                                  header=None, nrows=1)
            first_row = [str(v).strip() for v in first.iloc[0].tolist()]
            if _first_row_matches_columns(first_row, column_names):
                # 数据文件自带表头：直接按 header=0 读取，不再叠加字段名
                df = pd.read_excel(io.BytesIO(data_bytes), sheet_name=sheet_name,
                                   header=0)
            elif _is_placeholder_header(first_row):
                # 首行是占位列名（col1, col2, ...），跳过此行再用字段名
                df = pd.read_excel(io.BytesIO(data_bytes), sheet_name=sheet_name,
                                   header=None, names=column_names, skiprows=1)
            else:
                df = pd.read_excel(io.BytesIO(data_bytes), sheet_name=sheet_name,
                                   header=None, names=column_names)
        else:
            df = pd.read_excel(io.BytesIO(data_bytes), sheet_name=sheet_name,
                               header=0)
    except ImportError:
        raise ValueError("读取 Excel 需要 openpyxl，请先安装：pip install openpyxl") from None
    return _coerce_dates(df)


def llm_parse_header_file(header_bytes: bytes) -> list[str]:
    """将字段说明文件原始内容发给 LLM 提取字段名，失败返回 []。"""
    import os, json
    from openai import OpenAI
    from core.config import LLM_MODEL, LLM_API_BASE, LLM_API_KEY_ENV

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
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        cols = json.loads(raw)
        if isinstance(cols, list) and all(isinstance(c, str) for c in cols):
            return [c.strip() for c in cols if c.strip()]
    except Exception:
        pass
    return []
