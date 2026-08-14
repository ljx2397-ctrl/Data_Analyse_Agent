# backend/services/knowledge_store.py
# 业务知识库存储层：DashScope embedding + Chroma 向量检索。
# - embedding 复用 DashScope（兼容 OpenAI 接口，复用 DASHSCOPE_API_KEY），中文效果好。
# - 向量存储与检索使用 Chroma（PersistentClient，cosine 距离），元数据+全文仍存 JSON。
# - name 模式（文件名/关键词匹配）不依赖向量，直接在内存索引上匹配。
# - 启动时自动把旧 JSON 中的向量迁移到 Chroma（复用已有 vector，无需重新 embed）。
# - 提供 CRUD + 语义检索，供 api 路由与 Agent 工具调用。
import datetime
import json
import os
import threading
import uuid

import chromadb

from core.config import (
    KNOWLEDGE_DIR, EMBEDDING_MODEL, KNOWLEDGE_TOP_K,
    LLM_API_BASE, LLM_API_KEY_ENV,
)


# ── 持久化路径 ────────────────────────────────────────────────────────────────
_DATA_FILE = os.path.join(KNOWLEDGE_DIR, "knowledge.json")
_CHROMA_DIR = os.path.join(KNOWLEDGE_DIR, "chroma")
_BATCH_SIZE = 10     # DashScope embedding 单次批量上限（保守值）
_MAX_TEXT_LEN = 2000  # 单条文本最大长度（embedding token 上限约 2048）

# 检索模式：vector=向量化语义检索 / name=文件名关键词匹配（不需要 embedding）
_search_mode = "name"

# ── 内存索引（启动后懒加载，读写加锁）────────────────────────────────────────
_items: dict[str, dict] = {}      # id -> {id,title,content,category,tags,created_at,vector}
_loaded = False
_lock = threading.RLock()
_openai_client = None
_chroma_client = None
_collection = None


def _get_openai_client():
    """获取 OpenAI 兼容客户端单例（指向 DashScope 兼容端点）。"""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    from openai import OpenAI
    _openai_client = OpenAI(
        api_key=os.environ.get(LLM_API_KEY_ENV, ""),
        base_url=LLM_API_BASE,
    )
    return _openai_client


def _get_collection():
    """获取 Chroma collection 单例（cosine 距离，持久化到 _CHROMA_DIR）。"""
    global _chroma_client, _collection
    if _collection is not None:
        return _collection
    os.makedirs(_CHROMA_DIR, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=_CHROMA_DIR)
    _collection = _chroma_client.get_or_create_collection(
        name="knowledge",
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


def _load() -> None:
    """从磁盘加载知识库到内存（仅一次），并同步 Chroma。"""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        try:
            if os.path.exists(_DATA_FILE):
                with open(_DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _items.update(data.get("items", {}))
        except Exception:
            pass
        # 把 JSON 中的向量同步到 Chroma（首次迁移或增量补齐）
        _migrate_to_chroma()
        _loaded = True


def _migrate_to_chroma() -> None:
    """把 _items 中已有 vector 的条目同步到 Chroma（复用已有向量，无需重新 embed）。
    Chroma 中已有的 id 跳过，避免重复写入。
    """
    if not _items:
        return
    try:
        col = _get_collection()
        existing = set(col.get(include=[]).get("ids", []))
        pending = [
            (kid, rec) for kid, rec in _items.items()
            if kid not in existing and rec.get("vector")
        ]
        if not pending:
            return
        col.add(
            ids=[kid for kid, _ in pending],
            embeddings=[rec["vector"] for _, rec in pending],
            documents=[rec.get("content", "") for _, rec in pending],
            metadatas=[{"category": rec.get("category", "")} for _, rec in pending],
        )
    except Exception:
        pass


def _save() -> None:
    """持久化内存索引到磁盘。"""
    try:
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"items": _items}, f, ensure_ascii=False)
    except Exception:
        pass


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _to_item(kid: str, rec: dict, include_vector: bool = False) -> dict:
    """把内部记录转为对外条目（默认不含 vector）。"""
    item = {
        "id": kid,
        "title": rec.get("title", ""),
        "content": rec.get("content", ""),
        "category": rec.get("category", ""),
        "tags": rec.get("tags", []),
        "created_at": rec.get("created_at", ""),
    }
    if include_vector:
        item["vector"] = rec.get("vector", [])
    return item


def _embed(text: str) -> list[float]:
    """调用 DashScope embedding API 把文本转向量（直接传字符串，避免 tokenize）。"""
    client = _get_openai_client()
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """批量 embedding（DashScope 单次批量有上限，分批调用）。"""
    client = _get_openai_client()
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i:i + _BATCH_SIZE]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        all_vectors.extend([d.embedding for d in resp.data])
    return all_vectors


def _build_embed_text(title: str, content: str) -> str:
    """构造用于 embedding 的文本：title + content 拼接后截断到 _MAX_TEXT_LEN。
    让文件名/标题参与向量化，弥补 vector 模式下 title 不参与相似度计算的缺陷
    （否则文件名命中"字段"二字在向量空间里完全无信号）。
    存储仍保留 content 全文，此处结果仅用于 embedding 与 Chroma documents。
    """
    title = (title or "").strip()
    content = content or ""
    text = f"{title}\n{content}" if title else content
    if len(text) > _MAX_TEXT_LEN:
        text = text[:_MAX_TEXT_LEN]
    return text


# ── 公共 API ──────────────────────────────────────────────────────────────────

def add(title: str, content: str, category: str = "",
        tags: list[str] | None = None) -> dict:
    """新增一条知识，返回完整条目。"""
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    # 超长内容：embedding 用截断版（避免 token 超限），存储保留全文
    # title 纳入 embedding，让文件名/标题参与语义命中
    embed_text = _build_embed_text(title, content)
    vector = _embed(embed_text)
    kid = str(uuid.uuid4())
    rec = {
        "id": kid,
        "title": title or "",
        "content": content,
        "category": category or "",
        "tags": [t for t in (tags or []) if t],
        "created_at": _now(),
        "vector": vector,
    }
    with _lock:
        _load()
        _items[kid] = rec
        _save()
        # 同步到 Chroma
        try:
            col = _get_collection()
            col.add(
                ids=[kid],
                embeddings=[vector],
                documents=[embed_text],
                metadatas=[{"category": category or ""}],
            )
        except Exception:
            pass
    return _to_item(kid, rec)


def batch_add(items: list[dict]) -> list[dict]:
    """批量新增知识条目，分批 embed（DashScope 单次批量有上限）。
    items: [{title, content, category, tags}, ...]
    """
    valid = [it for it in items if it.get("content", "").strip()]
    if not valid:
        return []

    # 每条构造 embed 文本（title 纳入 embedding）；存储保留 content 全文，不再截断
    embed_texts = [_build_embed_text(it.get("title", ""), it["content"]) for it in valid]

    all_vectors = _embed_batch(embed_texts)

    results = []
    with _lock:
        _load()
        col = _get_collection()
        chroma_ids, chroma_vecs, chroma_docs, chroma_meta = [], [], [], []
        for it, vec, embed_text in zip(valid, all_vectors, embed_texts):
            kid = str(uuid.uuid4())
            rec = {
                "id": kid,
                "title": it.get("title", ""),
                "content": it["content"],
                "category": it.get("category", ""),
                "tags": it.get("tags", []),
                "created_at": _now(),
                "vector": vec,
            }
            _items[kid] = rec
            results.append(_to_item(kid, rec))
            chroma_ids.append(kid)
            chroma_vecs.append(vec)
            chroma_docs.append(embed_text)
            chroma_meta.append({"category": it.get("category", "")})
        _save()
        # 批量同步到 Chroma
        try:
            col.add(ids=chroma_ids, embeddings=chroma_vecs,
                    documents=chroma_docs, metadatas=chroma_meta)
        except Exception:
            pass
    return results


def update(kid: str, title: str | None = None, content: str | None = None,
           category: str | None = None, tags: list[str] | None = None) -> dict | None:
    """部分更新一条知识。content 变更时重新计算向量。"""
    with _lock:
        _load()
        rec = _items.get(kid)
        if rec is None:
            return None

        new_content = content if content is not None else rec["content"]
        if not new_content or not new_content.strip():
            raise ValueError("content 不能为空")

        title_changed = title is not None and title != rec["title"]
        content_changed = content is not None and content != rec["content"]

        rec["title"] = title if title is not None else rec["title"]
        rec["category"] = category if category is not None else rec["category"]
        rec["tags"] = tags if tags is not None else rec["tags"]
        # title 或 content 任一变更都重新 embed（title 已纳入 embedding）
        if title_changed or content_changed:
            if content is not None:
                rec["content"] = content
            embed_text = _build_embed_text(rec["title"], rec["content"])
            rec["vector"] = _embed(embed_text)
            # 同步更新 Chroma（upsert 覆盖向量+文档+元数据）
            try:
                col = _get_collection()
                col.update(
                    ids=[kid],
                    embeddings=[rec["vector"]],
                    documents=[embed_text],
                    metadatas=[{"category": rec.get("category", "")}],
                )
            except Exception:
                pass
        else:
            # category 变更时同步 Chroma 元数据
            if category is not None:
                try:
                    col = _get_collection()
                    col.update(ids=[kid], metadatas=[{"category": category}])
                except Exception:
                    pass
        _save()
        return _to_item(kid, rec)


def delete(kid: str) -> bool:
    """按 id 删除一条知识，返回是否删除成功。"""
    with _lock:
        _load()
        if kid not in _items:
            return False
        del _items[kid]
        _save()
        # 从 Chroma 删除
        try:
            col = _get_collection()
            col.delete(ids=[kid])
        except Exception:
            pass
        return True


def get(kid: str) -> dict | None:
    """按 id 获取单条知识。"""
    with _lock:
        _load()
        rec = _items.get(kid)
        return _to_item(kid, rec) if rec else None


def list_all() -> list[dict]:
    """返回全部知识条目，按创建时间降序。"""
    with _lock:
        _load()
        items = [_to_item(kid, rec) for kid, rec in _items.items()]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def search(query: str, top_k: int = KNOWLEDGE_TOP_K,
           category: str | None = None) -> list[dict]:
    """语义检索：用 Chroma cosine 查询 top_k 结果。
    指定 category 时只在该分类内检索（Chroma where 过滤）。
    """
    with _lock:
        _load()
        if not _items:
            return []

    try:
        query_vec = _embed(query)
    except Exception:
        return []

    try:
        col = _get_collection()
        kwargs = {
            "query_embeddings": [query_vec],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if category:
            kwargs["where"] = {"category": category}
        raw = col.query(**kwargs)
    except Exception:
        return []

    # Chroma 返回结构：{ids: [[...]], documents: [[...]], distances: [[...]], metadatas: [[...]]}
    ids = raw.get("ids", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    out = []
    with _lock:
        for kid, dist in zip(ids, distances):
            rec = _items.get(kid)
            if not rec:
                continue
            item = _to_item(kid, rec)
            # cosine 距离 = 1 - 相似度，转回相似度分数
            item["score"] = round(max(0.0, 1.0 - float(dist)), 3)
            out.append(item)
    # 按相似度降序
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


# ── 检索模式管理 ──────────────────────────────────────────────────────────────

def get_search_mode() -> str:
    return _search_mode


def set_search_mode(mode: str) -> None:
    global _search_mode
    if mode in ("vector", "name"):
        _search_mode = mode


def search_by_name(query: str, top_k: int = KNOWLEDGE_TOP_K) -> list[dict]:
    """文件名/关键词直接匹配（不需要 embedding）。
    query 按空格分词，文件名匹配权重高，内容匹配权重低。
    """
    with _lock:
        _load()
        if not _items:
            return []
        snapshot = [(kid, rec) for kid, rec in _items.items()]

    keywords = [k.strip().lower() for k in query.split() if k.strip()]
    if not keywords:
        return [_to_item(kid, rec) for kid, rec in snapshot[:top_k]]

    scored = []
    for kid, rec in snapshot:
        title = (rec.get("title") or "").lower()
        content = (rec.get("content") or "").lower()
        score = 0.0
        for kw in keywords:
            if kw in title:
                score += 2.0
            if kw in content:
                score += 1.0
        if score > 0:
            item = _to_item(kid, rec)
            item["score"] = score
            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def count() -> int:
    """返回知识库总条数。"""
    with _lock:
        _load()
        return len(_items)


def reindex_all() -> dict:
    """用当前 embedding 策略（title 纳入）重算全部知识的向量并同步 Chroma。
    供升级 embedding 逻辑后迁移历史数据使用。返回 {total, ok, failed}。
    会调用 embedding API，条目多时较慢（每批 _BATCH_SIZE 条）。
    """
    with _lock:
        _load()
        ids = list(_items.keys())

    total = len(ids)
    ok = 0
    failed = 0
    for i in range(0, total, _BATCH_SIZE):
        batch_ids = ids[i:i + _BATCH_SIZE]
        batch_recs = [(_items[kid], kid) for kid in batch_ids if kid in _items]
        if not batch_recs:
            continue
        texts = [_build_embed_text(r.get("title", ""), r.get("content", ""))
                 for r, _ in batch_recs]
        try:
            vectors = _embed_batch(texts)
        except Exception:
            failed += len(batch_recs)
            continue
        with _lock:
            col = _get_collection()
            for (rec, kid), vec, text in zip(batch_recs, vectors, texts):
                try:
                    rec["vector"] = vec
                    col.update(
                        ids=[kid],
                        embeddings=[vec],
                        documents=[text],
                        metadatas=[{"category": rec.get("category", "")}],
                    )
                    ok += 1
                except Exception:
                    failed += 1
            _save()
    return {"total": total, "ok": ok, "failed": failed}
