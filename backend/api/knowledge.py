# backend/api/knowledge.py
# 业务知识库 CRUD + 检索路由（全局共享，不挂在 /sessions/{sid} 下）。
from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from schemas import (
    KnowledgeItem, KnowledgeCreate, KnowledgeUpdate, KnowledgeSearchRequest,
)
from services import knowledge_store
from core.config import KNOWLEDGE_TOP_K

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeItem])
def list_items():
    """返回全部知识条目（按创建时间降序）。"""
    return knowledge_store.list_all()


@router.get("/stats/count")
def count_items():
    """返回知识库总条数（须放在 /{kid} 之前，避免被路径参数拦截）。"""
    return {"count": knowledge_store.count()}


@router.get("/search-mode")
def get_search_mode():
    """获取当前检索模式：vector=向量化 / name=文件名匹配。"""
    return {"mode": knowledge_store.get_search_mode()}


@router.put("/search-mode")
def set_search_mode(body: dict):
    """切换检索模式。body: {"mode": "vector" | "name"}。"""
    mode = body.get("mode", "vector")
    if mode not in ("vector", "name"):
        raise HTTPException(400, "mode 必须是 vector 或 name")
    knowledge_store.set_search_mode(mode)
    return {"mode": mode}


@router.post("/reindex")
def reindex():
    """用当前 embedding 策略（title 纳入）重算全部知识的向量。
    用于升级向量逻辑后迁移历史数据；会调用 embedding API，条目多时较慢。
    """
    try:
        return knowledge_store.reindex_all()
    except Exception as e:
        raise HTTPException(500, f"重建索引失败：{e}")


@router.post("/search", response_model=list[KnowledgeItem])
def search_items(body: KnowledgeSearchRequest):
    """语义检索知识库，返回最相关的 top_k 条（含 score）。
    指定 category 时只在该分类内检索。
    """
    if not body.query.strip():
        raise HTTPException(400, "query 不能为空")
    top_k = body.top_k or KNOWLEDGE_TOP_K
    return knowledge_store.search(body.query, top_k=top_k, category=body.category)


@router.get("/{kid}", response_model=KnowledgeItem)
def get_item(kid: str):
    """按 id 获取单条知识。"""
    item = knowledge_store.get(kid)
    if item is None:
        raise HTTPException(404, f"知识条目不存在：{kid}")
    return item


@router.post("", response_model=KnowledgeItem)
def create_item(body: KnowledgeCreate):
    """新增一条知识。"""
    try:
        return knowledge_store.add(
            title=body.title,
            content=body.content,
            category=body.category,
            tags=body.tags,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"新增失败（可能是 embedding 服务异常）：{e}")


@router.put("/{kid}", response_model=KnowledgeItem)
def update_item(kid: str, body: KnowledgeUpdate):
    """更新一条知识（部分字段）。"""
    try:
        item = knowledge_store.update(
            kid,
            title=body.title,
            content=body.content,
            category=body.category,
            tags=body.tags,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"更新失败：{e}")
    if item is None:
        raise HTTPException(404, f"知识条目不存在：{kid}")
    return item


@router.delete("/{kid}")
def delete_item(kid: str):
    """删除一条知识。"""
    ok = knowledge_store.delete(kid)
    if not ok:
        raise HTTPException(404, f"知识条目不存在：{kid}")
    return {"ok": True, "id": kid}


# ── 文件批量导入 ──────────────────────────────────────────────────────────────

_ALLOWED_EXTS = {"txt", "md", "csv", "xlsx"}
_MAX_CHUNK_LEN = 500   # 超长段落按此长度切块


def _parse_text_to_items(text: str, filename: str, ext: str) -> list[dict]:
    """把文件文本解析为知识条目列表。
    - txt/md：按空行分段，超长段按 _MAX_CHUNK_LEN 切块
    - csv：每行一条，第一列标题，第二列内容
    """
    base_name = filename.rsplit(".", 1)[0] if "." in filename else filename

    if ext == "csv":
        import csv
        items = []
        for row in csv.reader(text.splitlines()):
            if not row or not row[0].strip():
                continue
            title = row[0].strip()
            content = row[1].strip() if len(row) > 1 else title
            if content:
                items.append({"title": title, "content": content,
                              "category": "", "tags": []})
        return items

    # txt / md：按空行分段
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    items = []
    for i, p in enumerate(paragraphs, 1):
        if len(p) > _MAX_CHUNK_LEN:
            for j in range(0, len(p), _MAX_CHUNK_LEN):
                chunk = p[j:j + _MAX_CHUNK_LEN].strip()
                if chunk:
                    items.append({"title": f"{base_name}-{i}-{j // _MAX_CHUNK_LEN + 1}",
                                  "content": chunk, "category": "", "tags": []})
        else:
            items.append({"title": f"{base_name}-{i}", "content": p,
                          "category": "", "tags": []})
    return items


def _parse_excel_to_items(raw: bytes, filename: str) -> list[dict]:
    """解析 .xlsx 为知识条目。遍历所有 sheet，每行一条：
    第一列作标题，其余列合并为内容；单列则标题=内容。
    """
    import io
    import pandas as pd

    try:
        xls = pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")
    except Exception as e:
        raise ValueError(f"Excel 解析失败：{e}")

    items = []
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        if df.empty:
            continue
        for _, row in df.iterrows():
            cells = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
            if not cells:
                continue
            title = cells[0]
            content = " ".join(cells[1:]) if len(cells) > 1 else title
            if content:
                items.append({"title": title, "content": content,
                              "category": "", "tags": []})
    return items


@router.post("/upload", response_model=list[KnowledgeItem])
async def upload_file(file: UploadFile = File(...), category: str = Form(...)):
    """上传文件批量导入知识库。支持 .txt / .md / .csv / .xlsx。
    旧版 .xls 请在 Excel 中另存为 .xlsx 后上传。
    category 为必填项，用于向量检索时同分类内比较。
    """
    filename = file.filename or "knowledge.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    print(f"[knowledge.upload] filename={filename}, ext={ext}")

    if ext == "xls":
        raise HTTPException(400, "暂不支持旧版 .xls，请在 Excel 中另存为 .xlsx 后上传")
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(400, f"不支持的文件类型：.{ext}，仅支持 {', '.join(sorted(_ALLOWED_EXTS))}")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    print(f"[knowledge.upload] file size={len(raw)} bytes")

    try:
        if ext == "xlsx":
            items = _parse_excel_to_items(raw, filename)
        else:
            # txt/md/csv：自动检测编码（utf-8 优先，兜底 gbk）
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="ignore")
            items = _parse_text_to_items(text, filename, ext)
        print(f"[knowledge.upload] parsed {len(items)} rows")
    except ValueError as e:
        print(f"[knowledge.upload] parse error: {e}")
        raise HTTPException(400, str(e))

    if not items:
        raise HTTPException(400, "文件内容为空")

    # 整文件合并为全文，存为 1 条知识（不切块，保留原始内容供 Agent 检索）
    lines = []
    for it in items:
        if it["title"] and it["title"] != it["content"]:
            lines.append(f"{it['title']}: {it['content']}")
        else:
            lines.append(it["content"])
    full_content = "\n".join(lines)

    try:
        item = knowledge_store.add(
            title=filename,
            content=full_content,
            category=category,
            tags=[ext],
        )
        print(f"[knowledge.upload] stored as 1 item, content_len={len(full_content)}")
        return [item]
    except Exception as e:
        print(f"[knowledge.upload] add error: {type(e).__name__}: {e}")
        raise HTTPException(500, f"导入失败：{type(e).__name__}: {e}")
