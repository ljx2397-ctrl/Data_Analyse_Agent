# backend/core/rag_agent.py
# RAG Agent：专职处理知识库相关任务，集成两个能力：
#   1. 业务术语 / 口径查询 → search_knowledge() 检索 → LLM 回答
#   2. 字段匹配（无表头修复）→ search_knowledge() 检索字段文件 → 输出列名 → agent_service 执行 reload_df
#
# 架构说明：不使用 LangChain Tool / LangGraph Agent。
# 知识检索是 services.knowledge_service.search_knowledge（普通函数，非 Tool），
# 检索结果作为 context 拼进 LLM 的 user message，直接生成带标记的回复。
#
# 流程控制标记（回复末尾单独一行）：
#   ANSWER_ONLY        纯知识查询已回答完毕，不需要继续数据分析
#   CONTINUE_ANALYSIS  需要继续走数据分析 Agent
#   RELOAD_COLUMNS: a,b,c  从知识库解析到列名，需执行 reload_df
import re

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from core.agent import create_llm
from services.knowledge_service import search_knowledge


# 异常列名特征
_ABNORMAL_COL_PATTERNS = [
    r"^col\d+$",            # COL1, COL2
    r"^unnamed",            # Unnamed: 0
    r"^[a-z]$",             # 单字母 a, b, c
    r"^\d+$",               # 纯数字
    r"^[a-z]{1,3}\d{4,}$",  # TX015403 类编号
]

# 术语查询关键词
_TERM_MARKERS = (
    "什么是", "定义", "口径", "怎么算", "分类标准",
    "什么意思", "含义", "解释", "是指", "指的是",
)


def has_abnormal_columns(columns: list[str]) -> bool:
    """检测列名是否异常（无表头场景）。"""
    for col in columns:
        name = str(col).strip().lower()
        if any(re.search(p, name) for p in _ABNORMAL_COL_PATTERNS):
            return True
    return False


def should_run_rag(query: str, columns: list[str]) -> bool:
    """规则预判断：是否需要先走 RAG Agent。
    列名异常 或 问题含术语关键词 → 需要。
    大部分纯分析任务不会命中，直接走数据分析 Agent，零额外开销。
    """
    if has_abnormal_columns(columns):
        return True
    if any(m in query for m in _TERM_MARKERS):
        return True
    return False


def build_rag_system_prompt(columns: list[str]) -> str:
    """构建 RAG Agent 的系统提示。"""
    return f"""你是业务知识助手，专职处理与业务知识库相关的任务。

当前数据集列名：{columns}

━━━━ 你的职责 ━━━━

【职责 1：业务术语 / 口径查询】
当用户询问业务术语定义、计算口径、行业规则、业务背景时：
1. 使用用户消息中的【业务知识库检索结果】回答
2. 基于检索结果回答用户

【职责 2：字段匹配（无表头修复）】
当当前数据集列名异常（COL1/COL2、纯数字、TX015403、Unnamed、单字母等）时：
1. 参考用户消息中的【业务知识库检索结果】（已按关键词"字段 表头 列名 定义"检索字段文件）
2. 从返回的知识库内容中解析出列名列表。
   解析规则（知识库内容可能是以下格式之一）：
   a. 只有中文列名：客户号, 交易金额, 交易日期
   b. 拼音缩写+中文：JYJE-交易金额, JYRQ-交易日期
   c. 列序号+缩写+中文：COL1→KHH→客户号, COL2→JYJE→交易金额
   d. 其他分隔符：= / / 空格 / 换行 / 分号 等等
   ⚠️ 提取结果时：
   - 如果格式 c（有列序号 COL1/COL2），按列序号顺序提取中文名或拼音缩写（优先中文名）
   - 如果格式 b（只有缩写+中文），按文档顺序提取中文名，列数必须与当前数据一致
   - 禁止输出 COL1/COL2 这种序号列名作为结果
   - RELOAD_COLUMNS 里优先放中文名（如"交易金额"）；如果知识库只有缩写没有中文，才放缩写（如"JYJE"）
3. 在回复末尾输出标记：RELOAD_COLUMNS: 列名1,列名2,列名3,...

━━━━ 输出标记（回复末尾单独一行）━━━━

处理完成后，在回复末尾输出以下标记之一：

- ANSWER_ONLY
  纯知识查询已回答完毕，不需要继续数据分析。

- CONTINUE_ANALYSIS
  需要继续走数据分析 Agent（字段已修复、或用户问题需要数据分析）。

如果同时需要 reload_df 和继续分析，两个标记都输出：
  RELOAD_COLUMNS: 列名1,列名2,...
  CONTINUE_ANALYSIS

━━━━ 判断规则 ━━━━

1. 纯知识查询（如"什么是 AUM"）→ 回答 + ANSWER_ONLY
2. 纯字段匹配（如"匹配字段""检索字段文件""修复表头"）→ 解析列名 → RELOAD_COLUMNS + ANSWER_ONLY
3. 分析任务 + 列名异常（如"统计交易金额排名"但列名是 COL1/COL2）→ 解析列名 → RELOAD_COLUMNS + CONTINUE_ANALYSIS
4. 分析任务 + 列名正常 → 直接 CONTINUE_ANALYSIS（无需检索知识库）
5. 混合任务（如"什么是 AUM 然后统计排名"）→ 回答术语 + CONTINUE_ANALYSIS

判断"纯字段匹配"还是"分析任务"的关键：用户问题里有没有统计/排名/分组/计算/导出等数据分析动作。
有 → CONTINUE_ANALYSIS；没有 → ANSWER_ONLY。

━━━━ 注意事项 ━━━━

- 列名是否异常判断：COL1/COL2/Unnamed/纯数字/编号类（TX015403）= 异常
  真实字段名（如"客户号""交易金额""支行"）= 正常
- RELOAD_COLUMNS 的列名数量必须和当前数据集列数一致
- 不要臆造列名，只从知识库检索结果中解析
- 如果知识库未检索到字段文件，说明无法修复，输出 CONTINUE_ANALYSIS 让数据分析 Agent 尝试
"""


# ── RAG Agent 主函数（普通函数，非 Agent 图）─────────────────────────────────

def run_rag(query: str, columns: list[str]) -> tuple[str, str]:
    """执行 RAG 流程：按需检索知识库 → 调 LLM 生成带标记的回复。

    返回 (answer_text, raw_response_text)：
    - raw_response_text 包含所有控制标记（供 parse_rag_response 解析）
    - answer_text 与 raw_response_text 相同（流式输出时按行切给用户）
    """
    llm = create_llm()
    system_prompt = build_rag_system_prompt(columns)

    # ── 按需构造检索上下文 ──────────────────────────────────────────────────
    user_msg_parts = [f"用户问题：{query}"]
    abnormal = has_abnormal_columns(columns)

    # 列名异常：自动检索字段文件
    if abnormal:
        field_ctx = search_knowledge("字段 表头 列名 定义")
        user_msg_parts.append(f"\n\n【字段匹配检索上下文（已自动检索）】\n{field_ctx}")

    # 术语关键词：检索术语定义
    if any(m in query for m in _TERM_MARKERS) or abnormal is False:
        # 列名正常但含术语关键词 → 只查术语
        if not abnormal and any(m in query for m in _TERM_MARKERS):
            term_ctx = search_knowledge(query)
            user_msg_parts.append(f"\n\n【术语检索上下文】\n{term_ctx}")
        # 列名异常 + 用户也有术语问题 → 同时查术语
        if abnormal and any(m in query for m in _TERM_MARKERS):
            term_ctx = search_knowledge(query)
            user_msg_parts.append(f"\n\n【术语检索上下文】\n{term_ctx}")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(user_msg_parts)),
    ]

    resp = llm.invoke(messages)
    text = resp.content if isinstance(resp, AIMessage) else str(resp)

    usage_meta = {}
    try:
        um = getattr(resp, "usage_metadata", None)
        if um:
            usage_meta = um
    except Exception:
        pass
    return text, text, usage_meta


# ── 流式版本 ──────────────────────────────────────────────────────────────────

async def run_rag_stream(query: str, columns: list[str]):
    """异步流式版本。
    每次 yield (token_chunk_str | None, usage_dict | None)：
      - 流式 token 阶段：(token_chunk, None)
      - 结束阶段：      (None, usage_dict)
    注意：结束阶段不 yield full_text，避免调用方把全文再累加一遍导致内容重复。
    """
    llm = create_llm()
    system_prompt = build_rag_system_prompt(columns)

    user_msg_parts = [f"用户问题：{query}"]
    abnormal = has_abnormal_columns(columns)

    if abnormal:
        field_ctx = search_knowledge("字段 表头 列名 定义")
        user_msg_parts.append(f"\n\n【字段匹配检索上下文（已自动检索）】\n{field_ctx}")

    if (not abnormal and any(m in query for m in _TERM_MARKERS)) or \
       (abnormal and any(m in query for m in _TERM_MARKERS)):
        term_ctx = search_knowledge(query)
        user_msg_parts.append(f"\n\n【术语检索上下文】\n{term_ctx}")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(user_msg_parts)),
    ]

    usage = {}
    async for chunk in llm.astream(messages):
        content = ""
        # 双处读取 token 用量：优先官方 usage_metadata，再读 response_metadata（DashScope 常放这）
        try:
            _um = None
            if hasattr(chunk, "usage_metadata"):
                _um = getattr(chunk, "usage_metadata") or _um
            if _um is None and hasattr(chunk, "response_metadata"):
                rm = getattr(chunk, "response_metadata") or {}
                tu = rm.get("token_usage") or rm.get("usage")
                if tu:
                    # DashScope 格式: {"input_tokens": n, "output_tokens": n}
                    _um = {
                        "input_tokens":  int(tu.get("input_tokens")  or tu.get("prompt_tokens") or 0),
                        "output_tokens": int(tu.get("output_tokens") or tu.get("completion_tokens") or 0),
                    }
            if _um and (_um.get("input_tokens") or _um.get("output_tokens")):
                usage = _um
        except Exception:
            pass

        if isinstance(chunk, str):
            content = chunk
        elif isinstance(chunk, AIMessage) and chunk.content:
            content = chunk.content if isinstance(chunk.content, str) else ""
        elif hasattr(chunk, "content"):
            content = chunk.content if isinstance(chunk.content, str) else ""
        if content:
            yield content, None

    # 结束标志：tok=None 仅携带 usage，不再重复发送 full_text
    # 兜底字符估算：如果流式里完全没拿到 token 用量（0 tokens），就按 ~2.5 字符/token 粗估。
    if not usage.get("input_tokens") and not usage.get("output_tokens"):
        in_len  = len(system_prompt) + sum(len(m.content or "") for m in messages if hasattr(m, "content"))
        # 调用方需要 output 长度，这里没有 full_text（因为流式没保留）。
        # 把 in 粗略估出来，output 留给调用方基于已收到的流式字符补算。
        usage["_input_chars"] = in_len

    yield None, usage


# ── 标记解析 ──────────────────────────────────────────────────────────────────

_RELOAD_PATTERN = re.compile(r"RELOAD_COLUMNS:\s*(.+)", re.IGNORECASE)


def parse_rag_response(response: str) -> dict:
    """解析 RAG Agent 输出，提取流程控制标记。
    返回 {reload_columns, continue_analysis, answer_only, clean_text}。
    clean_text 为去掉标记后的纯回复。
    """
    reload_cols = None
    m = _RELOAD_PATTERN.search(response)
    if m:
        raw = m.group(1).strip()
        for tag in ("CONTINUE_ANALYSIS", "ANSWER_ONLY"):
            raw = raw.replace(tag, "").strip()
        reload_cols = [c.strip() for c in raw.split(",") if c.strip()]

    continue_analysis = "CONTINUE_ANALYSIS" in response
    answer_only = "ANSWER_ONLY" in response

    clean = response
    clean = _RELOAD_PATTERN.sub("", clean)
    clean = clean.replace("CONTINUE_ANALYSIS", "").replace("ANSWER_ONLY", "")
    clean = clean.rstrip("\n").strip()

    return {
        "reload_columns": reload_cols,
        "continue_analysis": continue_analysis,
        "answer_only": answer_only,
        "clean_text": clean,
    }


# ── 流式标记过滤 ──────────────────────────────────────────────────────────────

_MARKERS = ("RELOAD_COLUMNS:", "CONTINUE_ANALYSIS", "ANSWER_ONLY")


def is_marker_line(text: str) -> bool:
    """判断一行是否为流程控制标记（流式输出时过滤掉，不显示给用户）。"""
    stripped = text.strip()
    return any(stripped.startswith(m) or stripped == m for m in _MARKERS)
