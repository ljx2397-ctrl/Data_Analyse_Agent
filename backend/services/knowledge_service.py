# backend/services/knowledge_service.py
# 业务知识库检索服务（普通函数，非 LangChain Tool）。
# 供 RAG Agent 直接调用，不进入数据分析 Agent 的 tools 列表。
from services import knowledge_store


def search_knowledge(query: str) -> str:
    """检索业务知识库，返回与 query 语义最相关的条目。
    根据 knowledge_store 当前 mode 自动选择 name（关键词）或 vector（向量）检索。
    返回格式化文本（Markdown），直接注入 RAG Agent 上下文即可。
    """
    try:
        mode = knowledge_store.get_search_mode()
        if mode == "name":
            results = knowledge_store.search_by_name(query)
        else:
            results = knowledge_store.search(query)
    except Exception as e:
        return f"业务知识库检索失败：{e}"

    if not results:
        return "未在业务知识库中检索到相关内容。"

    lines = [f"### 业务知识库检索结果（共 {len(results)} 条）"]
    for i, item in enumerate(results, 1):
        lines.append(f"\n【{i}】{item.get('title', '')}")
        if item.get("category"):
            lines.append(f"分类：{item['category']}")
        if item.get("tags"):
            lines.append(f"标签：{', '.join(item['tags'])}")
        lines.append(f"内容：{item.get('content', '')}")
        if item.get("score") is not None:
            lines.append(f"(相关度：{item['score']})")

    lines.append("\n请在回答中合理引用上述业务知识，确保口径与定义一致。")
    return "\n".join(lines)
