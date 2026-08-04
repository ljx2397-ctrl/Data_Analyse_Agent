# session.py
# 负责所有 Streamlit session_state 的初始化、读写、会话切换和持久化。
# 每个会话存储：标题、消息列表、CSV 字节、文件名、列名（可选）。
# 持久化使用"每会话一个 JSON 文件"（原子写入 + 进程内锁，避免多标签页并发写坏），
# 首次启动时自动把旧版 chat_sessions.json / chat_sessions.pkl 迁移到 sessions 目录。

import datetime
import base64
import json
import os
import pickle
import threading
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage
from config import HISTORY_FILE, HISTORY_FILE_LEGACY, SESSIONS_DIR


# ── 持久化读写 ────────────────────────────────────────────────────────────────

# 进程内写锁：Streamlit 的多个会话在同一个进程的不同线程中运行，
# 用锁串行化写盘；再配合"先写临时文件再原子替换"，保证文件不会被写坏。
_WRITE_LOCK = threading.Lock()


def _to_jsonable(s: dict) -> dict:
    """将单个会话字典转为可 JSON 序列化的结构（df_bytes 转 base64）。"""
    d = dict(s)
    raw = d.get("df_bytes")
    if isinstance(raw, bytes):
        d["df_bytes"]          = base64.b64encode(raw).decode("ascii")
        d["df_bytes_encoding"] = "base64"
    elif raw is not None and not isinstance(raw, str):
        d["df_bytes"] = None   # 未知类型兜底，避免整个文件写失败
    return d


def _from_jsonable(d: dict) -> dict:
    """把 JSON 加载出的单个会话结构还原（base64 的 df_bytes 解码回 bytes）。"""
    s = dict(d)
    enc = s.pop("df_bytes_encoding", None)
    raw = s.get("df_bytes")
    if enc == "base64" and isinstance(raw, str):
        try:
            s["df_bytes"] = base64.b64decode(raw)
        except Exception:
            s["df_bytes"] = None
    elif isinstance(raw, str) and raw:
        s["df_bytes"] = raw.encode("utf-8")
    return s


def _session_path(sid: str) -> str:
    """返回某个会话对应的 JSON 文件路径。"""
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def _atomic_write_json(path: str, data: dict) -> None:
    """原子写入单个 JSON 文件（先写临时文件再替换）。"""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _load_from_dir() -> dict | None:
    """从 sessions 目录加载所有会话；目录不存在或为空时返回 None。"""
    if not os.path.isdir(SESSIONS_DIR):
        return None
    sessions = {}
    for name in sorted(os.listdir(SESSIONS_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, name), "r", encoding="utf-8") as f:
                data = json.load(f)
            sid = name[:-5]   # 去掉 .json 后缀
            if isinstance(data, dict):
                sessions[sid] = _from_jsonable(data)
        except Exception:
            continue   # 单个文件损坏不阻塞其他会话
    return sessions or None


def _migrate_legacy() -> dict | None:
    """旧版单文件（chat_sessions.json / chat_sessions.pkl）→ 每会话一个文件。

    迁移成功后把旧文件重命名为 .migrated 作为备份，
    避免"删除全部会话后旧数据复活"的问题。
    """
    sessions = None
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                sessions = {
                    sid: _from_jsonable(s)
                    for sid, s in data.items()
                    if isinstance(s, dict)
                }
        except Exception:
            pass
    if sessions is None and os.path.exists(HISTORY_FILE_LEGACY):
        try:
            with open(HISTORY_FILE_LEGACY, "rb") as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                sessions = data
        except Exception:
            pass
    if sessions is None:
        return None

    save_all_sessions(sessions)
    for legacy in (HISTORY_FILE, HISTORY_FILE_LEGACY):
        try:
            os.rename(legacy, legacy + ".migrated")
        except OSError:
            pass
    return sessions


def load_all_sessions() -> dict:
    """从磁盘加载所有历史会话。

    优先读新版 per-session 目录；目录为空时回退到旧版单文件并自动迁移。
    文件均不存在时返回空字典。
    """
    from_dir = _load_from_dir()
    if from_dir is not None:
        return from_dir
    migrated = _migrate_legacy()
    return migrated if migrated is not None else {}


def save_all_sessions(sessions: dict, only: list[str] | None = None) -> None:
    """将每个会话写入独立的 JSON 文件。

    传入 only（会话 ID 列表）时只重写这些会话文件（增量保存），
    其他会话的文件保持不变；不传 only 时执行全量同步：
    写入全部会话，并删除磁盘上不属于 sessions 的多余文件。
    """
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with _WRITE_LOCK:
        if only is None:
            valid = set()
            for sid, s in sessions.items():
                path = _session_path(sid)
                valid.add(path)
                _atomic_write_json(path, _to_jsonable(s))

            # 同步删除已不存在的会话文件（删除会话后磁盘保持一致）
            for name in os.listdir(SESSIONS_DIR):
                path = os.path.join(SESSIONS_DIR, name)
                if name.endswith(".json") and path not in valid:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                elif name.endswith(".tmp"):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        else:
            # 增量保存：只重写变化的会话文件
            for sid in only:
                s = sessions.get(sid)
                if s is not None:
                    _atomic_write_json(_session_path(sid), _to_jsonable(s))


def _remove_session_file(sid: str) -> None:
    """删除某个会话的持久化文件（删除会话时调用）。"""
    with _WRITE_LOCK:
        try:
            os.remove(_session_path(sid))
        except FileNotFoundError:
            pass
        except OSError:
            pass


# ── 当前会话访问器 ────────────────────────────────────────────────────────────

def current_session() -> dict | None:
    """返回当前激活会话的字典，无激活会话时返回 None"""
    sid = st.session_state.get("current_session_id")
    if sid is None:
        return None
    return st.session_state.all_sessions.get(sid)


def current_messages() -> list:
    """返回当前会话的消息列表"""
    sess = current_session()
    return sess["messages"] if sess else []


def append_message(role: str, content: str,
                   charts: list = None, xlsxs: list = None,
                   tables: list = None, code_exec: list = None) -> None:
    """向当前会话追加一条消息，并自动命名会话标题"""
    sid  = st.session_state.current_session_id
    sess = st.session_state.all_sessions[sid]
    sess["messages"].append({
        "role":    role,
        "content": content,
        "charts":  charts or [],
        "xlsxs":   xlsxs  or [],
        "tables":  tables  or [],
        "code_exec": code_exec or [],
    })
    # 第一条用户消息时自动生成会话标题
    if len(sess["messages"]) == 1 and role == "user":
        time_str   = datetime.datetime.now().strftime("%m-%d %H:%M")
        file_label = os.path.splitext(sess.get("df_name") or "数据分析")[0]
        sess["title"] = f"[{time_str}] {file_label}"
    save_all_sessions(st.session_state.all_sessions, only=[sid])


# ── 初始化（01_main_启动.py 启动时调用一次）──────────────────────────────────────────

def init_session_state() -> None:
    """初始化所有 session_state 键，已存在的键不会被覆盖"""
    import os  # 此处 import 避免循环依赖

    if "all_sessions" not in st.session_state:
        st.session_state.all_sessions = load_all_sessions()

    if "current_session_id" not in st.session_state:
        existing = sorted(st.session_state.all_sessions.keys(), reverse=True)
        if existing:
            sid = existing[0]
        else:
            sid = _new_session_id()
            st.session_state.all_sessions[sid] = _empty_session()
            save_all_sessions(st.session_state.all_sessions, only=[sid])
        st.session_state.current_session_id = sid
        _rebuild_lc_messages(sid)

    if "lc_messages" not in st.session_state:
        st.session_state.lc_messages = []

    # 上传流程标志位
    for key, default in [
        ("need_column_input", False),
        ("raw_upload_bytes",  None),
        ("upload_name",       None),
        ("show_upload",       False),
    ]:
        st.session_state.setdefault(key, default)


# ── 会话生命周期 ──────────────────────────────────────────────────────────────

def create_new_session() -> str:
    """新建一个空会话并切换到它，返回新会话 ID"""
    sid = _new_session_id()
    st.session_state.all_sessions[sid] = _empty_session()
    st.session_state.current_session_id = sid
    st.session_state.lc_messages        = []
    st.session_state.show_upload        = True
    save_all_sessions(st.session_state.all_sessions, only=[sid])
    return sid


def switch_session(sid: str) -> None:
    """切换到指定会话，重建 LangChain 消息历史"""
    from agent import get_agent
    get_agent.clear()
    st.session_state.current_session_id = sid
    _rebuild_lc_messages(sid)


def delete_session(sid: str) -> None:
    """
    删除指定会话。
    修复原来的 bug：delete 后若无剩余会话，create_new_session()
    会把 show_upload 设为 True，导致空白页面直接显示上传框。
    现在改为：无剩余会话时显示落地页（show_upload=False）。
    """
    del st.session_state.all_sessions[sid]
    _remove_session_file(sid)

    remaining = sorted(st.session_state.all_sessions.keys(), reverse=True)
    if remaining:
        # 切换到最新的剩余会话
        switch_session(remaining[0])
    else:
        # 没有任何会话了，新建一个但不立即显示上传框
        new_sid = _new_session_id()
        st.session_state.all_sessions[new_sid] = _empty_session()
        st.session_state.current_session_id    = new_sid
        st.session_state.lc_messages           = []
        st.session_state.show_upload           = False  # ← 修复：显示落地页
        save_all_sessions(st.session_state.all_sessions, only=[new_sid])


# ── 内部工具函数 ──────────────────────────────────────────────────────────────

def _new_session_id() -> str:
    """生成基于时间戳的唯一会话 ID"""
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _empty_session() -> dict:
    """返回一个空会话的初始字典结构"""
    return {
        "title":      "新对话",
        "messages":   [],
        "df_bytes":   None,   # CSV / Excel 原始字节
        "df_name":    None,   # 上传文件名
        "df_columns": None,   # 手动列名列表（无标题行时使用）
        "df_kind":    None,   # 文件类型：csv / xlsx
        "df_sheet":   None,   # Excel 使用的 Sheet 名（csv 时为 None）
    }


def _rebuild_lc_messages(sid: str) -> None:
    """
    从持久化消息重建 LangChain 消息对象列表。
    修复：用 .get() 防止旧版 pickle 数据缺少 messages 键导致 KeyError。
    """
    lc = []
    for m in st.session_state.all_sessions[sid].get("messages", []):
        if m["role"] == "user":
            lc.append(HumanMessage(content=m["content"]))
        else:
            lc.append(AIMessage(content=m["content"]))
    st.session_state.lc_messages = lc


