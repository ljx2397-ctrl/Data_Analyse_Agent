# backend/services/api_source.py
# API 数据源（精简版）：支持保存多个 URL 配置，随时拉取 CSV/JSON 数据注册到会话。
# 持久化到 storage/meta/api_sources.json，结构：
#   { "profiles": [ {id, name, url, auth_type, auth_token} ], "active_id": "xxx" }
# 支持两种返回格式：
#   1) URL 直接返回 CSV（text/csv 或 .csv 后缀）→ pd.read_csv
#   2) URL 返回 JSON → 自动定位第一个 list（列表）作为记录数组 → DataFrame
import io
import json
import os
import threading
import uuid

import pandas as pd

from settings import META_DIR


_CONFIG_PATH = os.path.join(META_DIR, "api_sources.json")
_lock = threading.Lock()


def _read_store() -> dict:
    default = {"profiles": [], "active_id": None}
    if not os.path.exists(_CONFIG_PATH):
        return default
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        data.setdefault("profiles", [])
        data.setdefault("active_id", None)
        return data
    except Exception:
        return default


def _write_store(data: dict) -> None:
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) > 8:
        return token[:4] + "*" * (len(token) - 8) + token[-4:]
    return "*" * len(token)


def _profile_view(p: dict, active_id: str | None) -> dict:
    return {
        "id": p.get("id", ""),
        "name": p.get("name", ""),
        "url": p.get("url", ""),
        "auth_type": p.get("auth_type", "none"),
        "auth_token_masked": _mask_token(p.get("auth_token", "")),
        "last_connected_at": p.get("last_connected_at", ""),
        "active": p.get("id") == active_id,
    }


# ── Profile CRUD ────────────────────────────────────────────────────────────

def list_profiles() -> list[dict]:
    data = _read_store()
    active_id = data.get("active_id")
    return [_profile_view(p, active_id) for p in data.get("profiles", [])]


def save_profile(profile_id: str | None = None, name: str = "", url: str = "",
                 auth_type: str = "none", auth_token: str | None = None,
                 activate: bool = True) -> dict:
    """新增或更新配置档案（仅 name/url/auth_type/auth_token）。"""
    with _lock:
        data = _read_store()
        profiles = data.get("profiles", [])
        if profile_id:
            target = None
            for p in profiles:
                if p.get("id") == profile_id:
                    target = p
                    break
            if target is None:
                raise ValueError(f"配置档案不存在：{profile_id}")
            if name is not None:
                target["name"] = name.strip()
            if url.strip():
                target["url"] = url.strip()
            target["auth_type"] = auth_type or "none"
            if auth_token is not None and auth_token.strip() and "*" not in auth_token:
                target["auth_token"] = auth_token.strip()
        else:
            if not url.strip():
                raise ValueError("URL 不能为空")
            new_id = uuid.uuid4().hex[:8]
            target = {
                "id": new_id,
                "name": name.strip(),
                "url": url.strip(),
                "auth_type": auth_type or "none",
                "auth_token": (auth_token.strip()
                               if auth_token and "*" not in auth_token else ""),
                "last_connected_at": "",
            }
            profiles.append(target)
        if activate:
            data["active_id"] = target["id"]
        _write_store(data)
        result = _profile_view(target, data.get("active_id"))
    return result


def activate_profile(profile_id: str) -> dict:
    with _lock:
        data = _read_store()
        profiles = data.get("profiles", [])
        found = None
        for p in profiles:
            if p.get("id") == profile_id:
                found = p
                break
        if found is None:
            raise ValueError(f"配置档案不存在：{profile_id}")
        data["active_id"] = profile_id
        _write_store(data)
        return _profile_view(found, profile_id)


def delete_profile(profile_id: str) -> dict:
    with _lock:
        data = _read_store()
        profiles = data.get("profiles", [])
        new_profiles = [p for p in profiles if p.get("id") != profile_id]
        if len(new_profiles) == len(profiles):
            raise ValueError(f"配置档案不存在：{profile_id}")
        data["profiles"] = new_profiles
        if data.get("active_id") == profile_id:
            data["active_id"] = new_profiles[0]["id"] if new_profiles else None
        _write_store(data)
    return {"remaining": len(new_profiles), "active_id": data.get("active_id")}


# ── 拉取核心：按 profile_id 拿配置 → HTTP GET → 自动 CSV/JSON → DataFrame ─

def _load_profile_raw(profile_id: str) -> dict:
    with _lock:
        data = _read_store()
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                return dict(p)
    raise ValueError(f"配置档案不存在：{profile_id}")


def _request(url: str, headers: dict) -> tuple[int, bytes, str]:
    """GET 请求，返回 (status_code, content_bytes, content_type)。"""
    import httpx
    try:
        resp = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True)
    except Exception as e:
        raise ValueError(f"请求失败：{e}")
    return resp.status_code, resp.content, resp.headers.get("content-type", "")


def _detect_sep(text: str) -> str:
    """检测文本的分隔符：| , \\t ;。取首行中出现次数最多的。"""
    first_line = text.split("\n", 1)[0]
    candidates = ["|", ",", "\t", ";"]
    counts = {s: first_line.count(s) for s in candidates}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _is_number_like(s) -> bool:
    if s is None or s == "":
        return False
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_header_row(first_row: list, sample_rows: list[list]) -> bool:
    """启发式判断首行是否为表头：
    判断规则（按顺序）：
    1) 首行有重复值 → 数据行（无表头）
    2) 首行混合了数字和非数字（如 张三|28|北京）→ 数据行（无表头）
    3) 首行全是非数字，后续行含数字 → 表头
    4) 其他情况（含纯数字首行）→ 默认表头（保守，避免多一行）
    """
    if not first_row:
        return True
    # 1) 首行有重复值 → 视为数据行
    if len(set(first_row)) != len(first_row):
        return False
    # 判断首行各单元格是否为数字
    numeric_flags = [_is_number_like(c) for c in first_row]
    has_num = any(numeric_flags)
    has_non_num = not all(numeric_flags)
    # 2) 首行混合数字和非数字 → 数据行
    if has_num and has_non_num:
        return False
    # 3) 首行全是非数字，后续行含数字 → 表头
    if has_non_num:
        for row in sample_rows[:5]:
            if any(_is_number_like(c) for c in row):
                return True
    # 4) 其他情况（纯数字首行或无法判断）→ 默认表头
    return True


def _parse_delimited(content: bytes, url: str) -> pd.DataFrame | None:
    """解析分隔符分隔的文本（CSV / | / \\t / ;)。
    自动判断首行是否为表头；无表头时用 col1, col2, ... 占位。
    返回 None 表示无法解析为分隔文本。
    """
    try:
        text = content.decode("utf-8-sig", errors="ignore")
    except Exception:
        return None
    if not text.strip():
        return None

    sep = _detect_sep(text)
    # 用 io.StringIO 解析
    import csv as _csv
    reader = _csv.reader(io.StringIO(text), delimiter=sep)
    rows = []
    for r in reader:
        # 跳过完全空行
        if any(c.strip() for c in r):
            rows.append([c.strip() for c in r])
    if not rows:
        return None

    # 统一列数：以最长行为准，不足的补空字符串
    max_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < max_cols:
            r.append("")

    first_row = rows[0]
    sample = rows[1:6]
    has_header = _is_header_row(first_row, sample)

    if has_header:
        columns = first_row
        data_rows = rows[1:]
    else:
        columns = [f"col{i+1}" for i in range(max_cols)]
        data_rows = rows

    if not data_rows:
        return None

    df = pd.DataFrame(data_rows, columns=columns)
    return df


def _response_to_df(content: bytes, content_type: str, url: str) -> pd.DataFrame:
    """把响应内容转成 DataFrame。
    顺序：JSON → 分隔符文本（自动检测 | , \\t ;）→ 兜底 CSV。
    分隔符文本自动判断首行是否为表头，无表头时用 col1/col2/... 占位。
    """
    ct = (content_type or "").lower()
    url_lower = url.lower()

    # 1) 明确是 CSV 文件
    if "csv" in ct or url_lower.endswith(".csv"):
        try:
            df = _parse_delimited(content, url)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
        try:
            return pd.read_csv(io.BytesIO(content))
        except Exception as e:
            raise ValueError(f"解析 CSV 失败：{e}")

    # 2) 尝试 JSON
    try:
        payload = json.loads(content.decode("utf-8-sig", errors="ignore"))
    except Exception:
        # 不是 JSON → 尝试分隔符文本
        df = _parse_delimited(content, url)
        if df is not None and not df.empty:
            return df
        # 兜底 CSV
        try:
            return pd.read_csv(io.BytesIO(content))
        except Exception as e:
            raise ValueError(f"响应既不是合法 JSON 也无法解析为分隔文本：{e}")

    # JSON 情况：找到第一个 list（数组）作为记录
    def _find_list(node, depth=0):
        if depth > 10:
            return None
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            for _, v in node.items():
                r = _find_list(v, depth + 1)
                if r is not None:
                    return r
        return None

    records = _find_list(payload)
    if records is None:
        if isinstance(payload, dict):
            records = [payload]
        else:
            raise ValueError("JSON 响应中未找到数组节点作为数据记录")

    if not records:
        raise ValueError("定位到的数据数组为空")

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("DataFrame 为空")
    return df


def _get_headers(cfg: dict) -> dict:
    """按 auth_type 注入鉴权头。"""
    headers = {}
    auth_type = cfg.get("auth_type") or "none"
    token = cfg.get("auth_token") or ""
    if auth_type == "bearer" and token:
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key_header" and token:
        headers["X-API-Key"] = token
    return headers


def _df_to_preview(df: pd.DataFrame) -> dict:
    """生成预览：columns + 行数/列数 + 前 5 行 Markdown。"""
    sample = df.head(5).copy()
    for col in sample.columns:
        if pd.api.types.is_datetime64_any_dtype(sample[col]):
            sample[col] = sample[col].dt.strftime("%Y-%m-%d")
    sample = sample.fillna("")
    md_lines = ["Table: api_data  ({:,} rows)".format(len(df))]
    # 字段类型行（模拟截图里 VARCHAR / BIGINT）
    for col in sample.columns:
        series = df[col]
        if pd.api.types.is_integer_dtype(series):
            t = "BIGINT"
        elif pd.api.types.is_float_dtype(series):
            t = "DOUBLE"
        elif pd.api.types.is_datetime64_any_dtype(series):
            t = "VARCHAR"
        else:
            t = "VARCHAR"
        md_lines.append(f"  {col}  {t}")
    # 样本行
    if len(sample) > 0:
        md_lines.append("  sample data (first {} rows)  ".format(len(sample)))
        for row in sample.itertuples(index=False):
            cells = [str(v) for v in row]
            md_lines.append("| " + " | ".join(cells) + " |")
    return {
        "columns": list(df.columns),
        "rows": len(df),
        "cols": len(df.columns),
        "preview_md": "\n".join(md_lines),
    }


def preview(profile_id: str) -> dict:
    """预览：请求 URL，返回字段/行数/前 5 行 Markdown，不写入会话。"""
    cfg = _load_profile_raw(profile_id)
    url = cfg.get("url", "").strip()
    if not url:
        raise ValueError("URL 为空")
    headers = _get_headers(cfg)
    status, content, ct = _request(url, headers)
    if status >= 400:
        raise ValueError(f"HTTP {status}")
    df = _response_to_df(content, ct, url)
    return _df_to_preview(df)


def fetch_to_session(profile_id: str, sid: str) -> dict:
    """请求 URL → DataFrame → CSV 字节 → 注册为会话的新数据集。"""
    from datetime import datetime
    from services import session_store, df_registry, agent_service
    from services.storage import get_storage

    cfg = _load_profile_raw(profile_id)
    url = cfg.get("url", "").strip()
    if not url:
        raise ValueError("URL 为空")
    headers = _get_headers(cfg)
    status, content, ct = _request(url, headers)
    if status >= 400:
        raise ValueError(f"HTTP {status}")
    df = _response_to_df(content, ct, url)

    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")

    sess = session_store.get(sid)
    if sess is None:
        raise ValueError(f"会话不存在：{sid}")

    ds_id = session_store._next_ds_id(sess)
    storage_key = f"sessions/{sid}/data_{ds_id}.bin"
    get_storage().put(storage_key, csv_bytes)

    name = cfg.get("name") or cfg.get("url").rsplit("/", 1)[-1] or "api_data"
    columns = list(df.columns)
    session_store.add_dataset(
        sid,
        ds_id=ds_id,
        name=name,
        kind="csv",
        columns=columns,
        sheet=None,
        storage_key=storage_key,
    )

    df_registry.remove(sid)
    agent_service.reset_agent(sid)
    sess = session_store.get(sid)
    df_registry.get_or_load_all(sid, sess)

    # 记录最近连接时间
    with _lock:
        data = _read_store()
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                p["last_connected_at"] = datetime.now().isoformat(timespec="seconds")
                break
        _write_store(data)

    return {
        "sid": sid,
        "ds_id": ds_id,
        "name": name,
        "rows": len(df),
        "cols": len(df.columns),
        "columns": columns,
    }
