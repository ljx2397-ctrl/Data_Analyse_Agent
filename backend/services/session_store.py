# backend/services/session_store.py
# 会话存储：Redis 或本地文件持久化。
# - 会话元数据 + 消息历史存 Redis / 文件（df_bytes 不存，太大）。
# - df 原始字节存对象存储（services.storage），session 只保留 df_storage_key。
# - REDIS_URL 未配置时自动降级为本地文件存储（持久化，重启不丢失）。
import datetime
import json
import os
import threading

from settings import REDIS_URL, REDIS_PREFIX, META_DIR, STORAGE_DIR
from services.storage import get_storage


# ── 文件持久化存储（Redis 接口子集，开发模式兜底）──────────────────────────────
class _FileKV:
    """
    基于 JSON 文件的持久化 KV 存储，模拟 session_store 用到的 Redis 接口。
    数据落盘到 META_DIR，重启后自动恢复。
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._strings_path = os.path.join(data_dir, "strings.json")
        self._zsets_path = os.path.join(data_dir, "zsets.json")
        self._lock = threading.Lock()
        # 内存缓存，启动时从磁盘加载
        self._strings: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self._strings_path):
                with open(self._strings_path, "r", encoding="utf-8") as f:
                    self._strings = json.load(f)
        except Exception:
            self._strings = {}
        try:
            if os.path.exists(self._zsets_path):
                with open(self._zsets_path, "r", encoding="utf-8") as f:
                    self._zsets = json.load(f)
        except Exception:
            self._zsets = {}

    def _save_strings(self) -> None:
        with open(self._strings_path, "w", encoding="utf-8") as f:
            json.dump(self._strings, f, ensure_ascii=False)

    def _save_zsets(self) -> None:
        with open(self._zsets_path, "w", encoding="utf-8") as f:
            json.dump(self._zsets, f, ensure_ascii=False)

    # ── 字符串操作 ──────────────────────────────────────────────
    def set(self, key: str, value: str) -> bool:
        with self._lock:
            self._strings[key] = value
            self._save_strings()
            return True

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._strings.get(key)

    def exists(self, key: str) -> int:
        with self._lock:
            return 1 if key in self._strings else 0

    def delete(self, key: str) -> int:
        with self._lock:
            changed = 0
            if key in self._strings:
                del self._strings[key]
                changed = 1
            if key in self._zsets:
                del self._zsets[key]
                changed = 1
            if changed:
                self._save_strings()
                self._save_zsets()
            return changed

    # ── 有序集合操作 ────────────────────────────────────────────
    def zadd(self, key: str, mapping: dict) -> int:
        with self._lock:
            if key not in self._zsets:
                self._zsets[key] = {}
            added = 0
            for member, score in mapping.items():
                if member not in self._zsets[key]:
                    added += 1
                self._zsets[key][member] = float(score)
            self._save_zsets()
            return added

    def _zrange(self, key: str, start: int, stop: int, rev: bool) -> list[str]:
        zset = self._zsets.get(key, {})
        sorted_items = sorted(zset.items(), key=lambda x: x[1], reverse=rev)
        if stop == -1:
            stop = len(sorted_items) - 1
        return [m for m, _ in sorted_items[start:stop + 1]]

    def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        with self._lock:
            return self._zrange(key, start, stop, rev=True)

    def zrange(self, key: str, start: int, stop: int) -> list[str]:
        with self._lock:
            return self._zrange(key, start, stop, rev=False)

    def zrem(self, key: str, member: str) -> int:
        with self._lock:
            if key in self._zsets and member in self._zsets[key]:
                del self._zsets[key][member]
                self._save_zsets()
                return 1
            return 0


# ── 客户端获取（Redis 优先，文件兜底）──────────────────────────────────────────
_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if REDIS_URL:
            import redis
            _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        else:
            # 开发模式：本地文件持久化，重启不丢失
            _client = _FileKV(META_DIR)
        return _client


def _k(sid: str) -> str:
    return f"{REDIS_PREFIX}session:{sid}"


def _index_key() -> str:
    return f"{REDIS_PREFIX}sessions"


# ── 会话数据结构 ──────────────────────────────────────────────────────────────

def _new_sid() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _empty_session(sid: str) -> dict:
    return {
        "sid":             sid,
        "title":           "新对话",
        "messages":        [],
        "datasets":        [],       # 多表列表：[{id, name, kind, columns, sheet, storage_key}]
        "pending_dataset": None,    # 上传中待确认的数据集：{id, name, kind, storage_key, sheets}
        "summary":         None,    # 历史对话滚动摘要（超过阈值时生成，避免每次都带全量历史）
        "summary_turns":   0,       # 摘要覆盖的轮数（用于判断是否需要再次摘要）
        "usage":           None,    # 会话级 token 累计 {input_tokens, output_tokens, total_tokens, turns, ...}
        # 以下字段保留向后兼容，始终镜像 datasets[0]（主表）
        "df_name":         None,
        "df_kind":         None,
        "df_columns":      None,
        "df_sheet":        None,
        "df_storage_key":  None,
        "created_at":      datetime.datetime.now().isoformat(timespec="seconds"),
    }


# ── 公共 API ──────────────────────────────────────────────────────────────────

def create() -> str:
    """新建空会话，返回 sid。"""
    sid = _new_sid()
    sess = _empty_session(sid)
    cli = _get_client()
    cli.set(_k(sid), json.dumps(sess, ensure_ascii=False))
    cli.zadd(_index_key(), {sid: datetime.datetime.now().timestamp()})
    return sid


def get(sid: str) -> dict | None:
    cli = _get_client()
    raw = cli.get(_k(sid))
    if raw is None:
        return None
    try:
        sess = json.loads(raw)
    except Exception:
        return None
    # 迁移：旧会话没有 datasets 字段但有 df_storage_key，从旧字段构造
    if not sess.get("datasets") and sess.get("df_storage_key"):
        sess["datasets"] = [{
            "id":          "ds1",
            "name":        sess.get("df_name", "data.csv"),
            "kind":        sess.get("df_kind", "csv"),
            "columns":     sess.get("df_columns"),
            "sheet":       sess.get("df_sheet"),
            "storage_key": sess["df_storage_key"],
        }]
    if "pending_dataset" not in sess:
        sess["pending_dataset"] = None
    if "summary" not in sess:
        sess["summary"] = None
    if "summary_turns" not in sess:
        sess["summary_turns"] = 0
    if "usage" not in sess:
        sess["usage"] = None
    return sess


def exists(sid: str) -> bool:
    return _get_client().exists(_k(sid)) > 0


def list_all() -> list[dict]:
    """返回所有会话，按最近活动时间降序（有新消息的会话浮到顶部）。"""
    cli = _get_client()
    sids = cli.zrevrange(_index_key(), 0, -1)
    out = []
    for sid in sids:
        sess = get(sid)
        if sess is not None:
            out.append(sess)
    return out


def update(sid: str, **fields) -> dict | None:
    """部分更新会话字段，返回更新后的会话；会话不存在返回 None。"""
    sess = get(sid)
    if sess is None:
        return None
    sess.update(fields)
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return sess


def delete(sid: str) -> None:
    cli = _get_client()
    sess = get(sid)
    # 清理对象存储中所有数据集的字节
    if sess:
        for ds in sess.get("datasets", []):
            try:
                get_storage().delete(ds["storage_key"])
            except Exception:
                pass
        # 清理未确认的 pending_dataset 存储
        pending = sess.get("pending_dataset")
        if pending and pending.get("storage_key"):
            try:
                get_storage().delete(pending["storage_key"])
            except Exception:
                pass
    cli.delete(_k(sid))
    cli.zrem(_index_key(), sid)
    # 清理进程内 df 与运行轨迹文件
    from services import df_registry
    df_registry.remove(sid)
    from services import telemetry
    telemetry.delete_trace(sid)
    # 清理会话存储目录（sessions/{sid}/），避免遗留空文件夹
    import shutil
    sess_dir = os.path.join(STORAGE_DIR, "sessions", sid)
    if os.path.isdir(sess_dir):
        shutil.rmtree(sess_dir, ignore_errors=True)


def append_message(sid: str, role: str, content: str,
                   charts: list | None = None, xlsxs: list | None = None,
                   tables: list | None = None,
                   code_exec: list | None = None,
                   usage: dict | None = None,
                   mode: str | None = None,
                   plan_steps: list | None = None,
                   replan_events: list | None = None) -> dict | None:
    """追加一条消息，并自动命名会话标题（首条用户消息时）。"""
    sess = get(sid)
    if sess is None:
        return None
    msg = {
        "role":     role,
        "content":  content,
        "charts":   charts or [],
        "xlsxs":    xlsxs or [],
        "tables":   tables or [],
        "code_exec": code_exec or [],
        "usage":    usage,   # 本轮 token 用量（assistant 消息；user 消息为 None）
    }
    # mode/plan_steps/replan_events 仅 assistant 消息有值（plan-execute 路径）
    if mode is not None:
        msg["mode"] = mode
    if plan_steps is not None:
        msg["plan_steps"] = plan_steps
    if replan_events is not None:
        msg["replan_events"] = replan_events
    sess["messages"].append(msg)
    # 第一条用户消息时自动生成标题
    if len(sess["messages"]) == 1 and role == "user":
        time_str   = datetime.datetime.now().strftime("%m-%d %H:%M")
        file_label = (sess.get("df_name") or "数据分析").rsplit(".", 1)[0]
        sess["title"] = f"[{time_str}] {file_label}"
    cli = _get_client()
    cli.set(_k(sid), json.dumps(sess, ensure_ascii=False))
    # 更新最近活动时间戳：让有新消息的会话浮到列表顶部（按最近活动排序，而非创建时间）
    cli.zadd(_index_key(), {sid: datetime.datetime.now().timestamp()})
    return sess


def set_data(sid: str, df_bytes: bytes, df_name: str, df_kind: str,
             df_columns: list | None, df_sheet: str | None) -> dict | None:
    """把 df 原始字节写入对象存储，并更新会话元数据。"""
    storage_key = f"sessions/{sid}/data.bin"
    get_storage().put(storage_key, df_bytes)
    return update(
        sid,
        df_name=df_name,
        df_kind=df_kind,
        df_columns=df_columns,
        df_sheet=df_sheet,
        df_storage_key=storage_key,
    )


def get_messages(sid: str) -> list[dict]:
    sess = get(sid)
    return sess["messages"] if sess else []


def delete_message(sid: str, index: int) -> dict | None:
    """删除单条消息（按索引）。返回更新后的 session；越界或会话不存在返回 None。"""
    sess = get(sid)
    if sess is None:
        return None
    msgs = sess.get("messages", [])
    if index < 0 or index >= len(msgs):
        return None
    msgs.pop(index)
    sess["messages"] = msgs
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return sess


def truncate_messages(sid: str, from_index: int) -> dict | None:
    """截断消息：保留 [0, from_index) 的消息，删除 from_index 及之后所有。
    用于编辑 user 消息后重发（丢弃该条及后续，重新生成）。
    同时清空历史摘要（摘要可能引用了被截断的消息，保留会错乱）。
    """
    sess = get(sid)
    if sess is None:
        return None
    msgs = sess.get("messages", [])
    if from_index < 0 or from_index > len(msgs):
        return None
    sess["messages"] = msgs[:from_index]
    # 摘要覆盖范围可能超出剩余消息，重置以避免上下文错乱
    sess["summary"] = None
    sess["summary_turns"] = 0
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return sess


# ── 多表数据集管理 ────────────────────────────────────────────────────────────

def _next_ds_id(sess: dict) -> str:
    """生成下一个数据集 ID：ds1, ds2, ..."""
    existing = {ds["id"] for ds in sess.get("datasets", [])}
    n = 1
    while f"ds{n}" in existing:
        n += 1
    return f"ds{n}"


def set_pending_dataset(sid: str, name: str, kind: str,
                        storage_key: str, sheets: list | None = None) -> dict | None:
    """设置待确认的数据集（上传文件后、确认导入前）。"""
    sess = get(sid)
    if sess is None:
        return None
    ds_id = _next_ds_id(sess)
    sess["pending_dataset"] = {
        "id":          ds_id,
        "name":        name,
        "kind":        kind,
        "storage_key": storage_key,
        "sheets":      sheets,
    }
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return sess["pending_dataset"]


def get_pending_dataset(sid: str) -> dict | None:
    sess = get(sid)
    return sess.get("pending_dataset") if sess else None


def update_pending_dataset(sid: str, **fields) -> dict | None:
    """部分更新 pending_dataset 字段（如修正 storage_key）。"""
    sess = get(sid)
    if sess is None or not sess.get("pending_dataset"):
        return None
    sess["pending_dataset"].update(fields)
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return sess["pending_dataset"]


def clear_pending_dataset(sid: str) -> None:
    sess = get(sid)
    if sess is None:
        return
    sess["pending_dataset"] = None
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))


def add_dataset(sid: str, ds_id: str, name: str, kind: str,
                columns: list | None, sheet: str | None,
                storage_key: str) -> dict | None:
    """确认导入：将数据集加入 datasets 列表。
    若是第一个数据集，同步更新 df_* 向后兼容字段。
    """
    sess = get(sid)
    if sess is None:
        return None
    ds = {
        "id":          ds_id,
        "name":        name,
        "kind":        kind,
        "columns":     columns,
        "sheet":       sheet,
        "storage_key": storage_key,
    }
    sess.setdefault("datasets", []).append(ds)
    sess["pending_dataset"] = None
    # 第一个数据集：同步向后兼容字段
    if len(sess["datasets"]) == 1:
        sess["df_name"]        = name
        sess["df_kind"]        = kind
        sess["df_columns"]     = columns
        sess["df_sheet"]       = sheet
        sess["df_storage_key"] = storage_key
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return ds


def get_datasets(sid: str) -> list[dict]:
    """返回会话的所有数据集元数据。"""
    sess = get(sid)
    return sess.get("datasets", []) if sess else []


def remove_dataset(sid: str, ds_id: str) -> dict | None:
    """删除指定数据集（仅允许删除副表，即非第一个）。
    返回更新后的 session；数据集不存在或为主表时返回 None。
    """
    sess = get(sid)
    if sess is None:
        return None
    datasets = sess.get("datasets", [])
    idx = None
    for i, ds in enumerate(datasets):
        if ds["id"] == ds_id:
            idx = i
            break
    if idx is None or idx == 0:
        # 不存在或为主表（不允许删除）
        return None
    # 删除对象存储中的文件
    ds = datasets[idx]
    try:
        get_storage().delete(ds["storage_key"])
    except Exception:
        pass
    datasets.pop(idx)
    sess["datasets"] = datasets
    _get_client().set(_k(sid), json.dumps(sess, ensure_ascii=False))
    return sess


def clear_all() -> None:
    """清空所有会话（开发/测试用）。"""
    cli = _get_client()
    sids = cli.zrange(_index_key(), 0, -1)
    for sid in sids:
        cli.delete(_k(sid))
    cli.delete(_index_key())
