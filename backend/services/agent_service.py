# backend/services/agent_service.py
# Agent 运行时服务：构建 Agent、流式执行转 SSE、工具调用代码提取、质量检测分支。
# 改造自原 01_main_启动.py 的聊天主循环（去掉 Streamlit 依赖，改为生成 SSE 事件）。
import json
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.config import MAX_HISTORY_TURNS, SUMMARY_TRIGGER_TURNS, SUMMARY_KEEP_TURNS
from core.agent import get_agent
from core.plan_execute import build_plan_execute_graph
from core.router import route as route_query
from core.render import parse_output
from core.skills import SkillContext, dispatch_skill
from services import session_store, df_registry, skill_store, telemetry
from services.storage import abspath_to_download_url


# ── LangChain 消息历史（进程内存，按需从 session_store 重建）──────────────────
_lc_registry: dict[str, list] = {}

# ── Agent 实例缓存（按 sid）─────────────────────────────────────────────────
# 避免每次对话都重建 LangGraph 图 + 注入工具。数据变更/会话切换时清除。
_agent_registry: dict[str, object] = {}

# ── Plan-Execute-Replan 图缓存（按 sid）─────────────────────────────────────
# 图本身是无状态的（df/dfs 通过工具模块级 getter 注入），可跨会话复用。
# 但为 simplicity 仍按 sid 缓存，与 _agent_registry 生命周期一致。
_pe_registry: dict[str, object] = {}


def _make_reload_fn(sid: str):
    """创建闭包：用指定列名重新加载当前会话的主数据文件。
    供 Agent 在 analyse_tool 代码中调用 reload_df(字段名列表)。
    load_dataframe 会自动检测第一行是否为表头，不会丢失数据。
    """
    from services.storage import get_storage
    from core.data_loader import load_dataframe, load_excel

    def _reload(columns):
        sess = session_store.get(sid)
        if not sess:
            return "❌ 找不到会话"
        datasets = sess.get("datasets", [])
        if not datasets:
            return "❌ 找不到数据文件"
        # 默认重载主表（第一个数据集）
        ds = datasets[0]
        raw = get_storage().get(ds["storage_key"])
        kind = ds.get("kind", "csv")
        cols = [str(c).strip() for c in columns if str(c).strip()]
        if not cols:
            return "❌ 字段名列表为空"
        try:
            if kind == "xlsx":
                df = load_excel(raw, sheet_name=ds.get("sheet") or 0,
                                column_names=cols)
            else:
                df = load_dataframe(raw, column_names=cols)
            df_registry.register(sid, ds["id"], df)
            # 持久化列名到 session 元数据
            ds["columns"] = list(df.columns)
            session_store.update(sid, datasets=sess.get("datasets", []))
            # 同步向后兼容字段
            if len(datasets) == 1:
                session_store.update(sid, df_columns=list(df.columns))
            # 清除缓存的 Agent 实例，下次对话会用新 df 重建
            _agent_registry.pop(sid, None)
            # 列名已变，旧摘要中的列名会误导 Agent，清空摘要与 lc 缓存
            session_store.update(sid, summary=None, summary_turns=0)
            reset_lc(sid)
            # 列名变了，schema 也要重新查；清掉 schema_called 标记
            try:
                df_registry.set_named_result(sid, "__schema_called__", None)
            except Exception:
                pass
            return f"✅ 已用 {len(cols)} 个字段名重新加载数据，共 {len(df)} 行 × {len(df.columns)} 列。"
        except Exception as e:
            return f"❌ 重新加载失败：{e}"
    return _reload


def _get_or_create_agent(sid: str, df, data_desc: str, _df_getter, datasets=None):
    """获取或创建 Agent 实例（按 sid 缓存）。
    datasets 为会话的数据集元数据列表，用于构建多表 dfs 访问器。
    """
    from core.tools import schema_tool, analyse_tool, chart_tool, excel_tool
    cached = _agent_registry.get(sid)
    # sid_getter 闭包：每次注入都重新创建（cached 分支也要刷，确保指向当前 sid）
    _sid_getter = lambda: sid
    schema_tool.set_sid_getter(_sid_getter)
    analyse_tool.set_sid_getter(_sid_getter)
    chart_tool.set_sid_getter(_sid_getter)
    excel_tool.set_sid_getter(_sid_getter)
    if cached is not None:
        # 仍需重新注入 df_getter（进程重启后 df 可能从磁盘重建，getter 闭包需刷新）
        schema_tool.set_df_accessor(_df_getter)
        analyse_tool.set_df_accessor(_df_getter)
        chart_tool.set_df_accessor(_df_getter)
        excel_tool.set_df_accessor(_df_getter)
        # 注入 dfs 访问器（多表）
        if datasets:
            _dfs_getter = df_registry.make_all_getter(sid, datasets)
            schema_tool.set_dfs_accessor(_dfs_getter)
            analyse_tool.set_dfs_accessor(_dfs_getter)
            chart_tool.set_dfs_accessor(_dfs_getter)
            excel_tool.set_dfs_accessor(_dfs_getter)
        analyse_tool.set_reload_fn(_make_reload_fn(sid))
        return cached

    # 构建多表 dfs 访问器
    _dfs_getter = None
    if datasets:
        _dfs_getter = df_registry.make_all_getter(sid, datasets)

    agent = get_agent(df, data_desc, _df_getter, _dfs_getter)
    _agent_registry[sid] = agent
    analyse_tool.set_reload_fn(_make_reload_fn(sid))
    return agent


def reset_agent(sid: str) -> None:
    """数据变更/会话切换后清除缓存的 Agent，强制下次重建。"""
    _agent_registry.pop(sid, None)
    _pe_registry.pop(sid, None)
    reset_lc(sid)


def reset_all_agents() -> None:
    """LLM 配置变更后清空所有会话的 Agent 缓存，强制下次用新 LLM 重建。"""
    _agent_registry.clear()
    _pe_registry.clear()
    _lc_registry.clear()


def _get_or_create_pe_agent(sid: str):
    """获取或创建 Plan-Execute-Replan 图实例（按 sid 缓存）。
    图本身无状态，但按 sid 缓存可避免重复 compile。
    """
    cached = _pe_registry.get(sid)
    if cached is not None:
        return cached
    graph = build_plan_execute_graph()
    _pe_registry[sid] = graph
    return graph


def _is_schema_already_called(sid: str) -> bool:
    """检查当前会话是否已调用过 schema_tool（供 planner 决定是否需要 schema 步骤）。"""
    try:
        return df_registry.get_named_result(sid, "__schema_called__") is not None
    except Exception:
        return False


def _rebuild_lc_messages(messages: list[dict], summary: str | None = None) -> list:
    """从 session_store 的消息字典重建 LangChain 消息列表。
    summary 非空时作为首条 SystemMessage（历史摘要）插入。
    末尾连续的孤立 user 消息（上次出错/打断未得到 assistant 回复）会被丢弃，
    避免下次新提问时把遗留问题一起带给 LLM。
    """
    lc = []
    if summary:
        lc.append(SystemMessage(content=summary))
    for m in messages:
        if m["role"] == "user":
            lc.append(HumanMessage(content=m["content"]))
        else:
            lc.append(AIMessage(content=m["content"]))
    # 丢弃末尾连续的孤立 user 消息（无对应 assistant 回复）：
    # 上次出错/打断时 user 已写入但 assistant 未保存，下次新提问不应把它带上。
    while lc and isinstance(lc[-1], HumanMessage):
        lc.pop()
    return lc


def _get_lc(sid: str) -> list:
    """获取会话的 LangChain 消息列表（含摘要），按需从 session_store 重建。"""
    lc = _lc_registry.get(sid)
    if lc is None:
        sess = session_store.get(sid)
        if sess:
            # 重建时只加载「未被摘要覆盖的」消息，避免摘要后老消息又被带回来
            summary_turns = sess.get("summary_turns", 0)
            keep_from = summary_turns * 2
            msgs = sess["messages"][keep_from:]
            lc = _rebuild_lc_messages(msgs, sess.get("summary"))
        else:
            lc = []
        _lc_registry[sid] = lc
    return lc


def reset_lc(sid: str) -> None:
    """会话切换/数据变更后清空缓存的 lc_messages，强制下次重建。"""
    _lc_registry.pop(sid, None)


# ── 上下文压缩：滚动摘要 ──────────────────────────────────────────────────────
# 当未摘要的消息轮数超过 SUMMARY_TRIGGER_TURNS 时，把较早的消息让 LLM 压缩成一条
# SystemMessage，保留最近 SUMMARY_KEEP_TURNS 轮原文。摘要持久化到 session_store，
# 进程重启后不重复摘要。摘要失败时降级为简单截断，不阻塞主流程。

_SUMMARY_PROMPT = """请将以下对话历史压缩为一段简洁的摘要，供后续对话参考。

【必须保留】
1. 数据集信息：文件名、规模、关键列名（特别是 reload_df 后的最终列名）
2. 用户的核心分析目标和已确认的业务口径
3. 已得出的关键结论：具体数字、排名、趋势、占比等
4. 用户偏好：导出格式、图表类型、关注维度

【可以丢弃】
- 工具调用代码、中间过程、表格明细数据
- 寒暄、重复确认、失败尝试

【格式要求】
- 用要点形式，每条一行，控制在 300 字以内
- 开头标注「【历史对话摘要】」

对话历史：
{history}
"""


def _maybe_summarize(sid: str, lc: list) -> list:
    """检查是否需要摘要，需要则执行并返回压缩后的消息列表。
    lc 为当前内存中的消息列表（可能已含旧摘要）。
    返回：压缩后的消息列表（可能等于原列表，表示未触发摘要）。
    """
    # 统计未摘要的对话轮数（排除已有的摘要 SystemMessage）
    non_summary_msgs = [m for m in lc if not isinstance(m, SystemMessage)]
    total_turns = len(non_summary_msgs) // 2

    if total_turns <= SUMMARY_TRIGGER_TURNS:
        return lc

    # 需要摘要的轮数 = 总轮数 - 保留轮数
    turns_to_summarize = total_turns - SUMMARY_KEEP_TURNS
    if turns_to_summarize <= 0:
        return lc

    # 切分：老消息待摘要 + 最近 N 轮保留
    keep_msgs = non_summary_msgs[turns_to_summarize * 2:]

    # 构造历史文本
    history_lines = []
    existing_summary = None
    for m in lc:
        if isinstance(m, SystemMessage):
            existing_summary = m.content
            continue
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        history_lines.append(f"{role}：{m.content}")

    # 若已有旧摘要，放在最前作为上下文
    if existing_summary:
        history_lines.insert(0, existing_summary)

    history_text = "\n".join(history_lines)

    # 调 LLM 生成摘要（非流式，失败则降级）
    try:
        from core.agent import create_llm
        llm = create_llm()
        prompt = _SUMMARY_PROMPT.format(history=history_text)
        resp = llm.invoke([HumanMessage(content=prompt)])
        # 摘要消耗的 token 计入会话总量（系统开销，不占对话轮数）
        try:
            um = getattr(resp, "usage_metadata", None)
            if um:
                from services.llm_config import get_config as _get_llm_cfg
                _u = telemetry.build_usage(
                    um.get("input_tokens", 0), um.get("output_tokens", 0),
                    model=_get_llm_cfg().get("model", ""))
                telemetry.accumulate_session_usage(sid, _u, count_turn=False)
                telemetry.record_event(sid, "summary",
                                       input_tokens=_u["input_tokens"],
                                       output_tokens=_u["output_tokens"])
        except Exception:
            pass
        new_summary = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        if not new_summary:
            return lc
    except Exception:
        # 摘要失败，降级为截断（只保留最近 N 轮，不带摘要）
        return keep_msgs

    # 持久化摘要：新摘要覆盖的轮数 = 旧摘要已覆盖的轮数 + 本次摘要的轮数
    sess = session_store.get(sid)
    old_summary_turns = sess.get("summary_turns", 0) if sess else 0
    total_summarized_turns = old_summary_turns + turns_to_summarize
    session_store.update(sid, summary=new_summary, summary_turns=total_summarized_turns)

    # 返回：新摘要 + 最近 N 轮原文
    return [SystemMessage(content=new_summary)] + keep_msgs


# ── Skill 调度（Layer 3：确定性流程，命中关键词或前端显式选择即直接跑）────────
# 替换原 _is_quality_intent 硬编码分支：所有"高频确定性流程"都收敛为 Skill，
# 在前端可编辑/启停，新增 skill 只需在 skill_store 注册 + 实现 runner。
CUSTOM_QUALITY_KEYWORDS = ("一致", "匹配", "关联", "关系", "规则", "自定义", "联动")


def _is_custom_quality(text: str) -> bool:
    """自定义质量规则仍需走 LLM（生成代码检查），不进确定性 skill 分支。"""
    return any(k in text for k in CUSTOM_QUALITY_KEYWORDS)


# ── 网络错误重试 ──────────────────────────────────────────────────────────────
NETWORK_ERR_MARKERS = (
    "peer closed connection", "incomplete chunked read",
    "connection", "timeout", "timed out", "read error",
    "remote protocol", "broken pipe", "reset",
    "apiconnectionerror",
)

# 不可恢复错误：重试也无意义，直接报错给用户
# 认证失败 / 计费问题 / 模型不存在 / 上下文超长，重试只会浪费时间
FATAL_ERR_MARKERS = (
    "authentication", "unauthorized", "invalid api key", "invalid_api_key",
    "insufficient_quota", "billing",
    "model_not_found", "model not found",
    "context_length_exceeded", "context length",
    "permission_denied", "forbidden",
)

MAX_ATTEMPTS = 3


# ── SSE 事件辅助 ──────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 工具调用代码提取（四种方案兜底，原样保留兼容多服务商流式分片）──────────────

def _extract_code_exec(full_raw: str, acc_msg, tool_call_fragments: list,
                       tool_calls_acc: dict, segments: list) -> list[str]:
    """从 Agent 流式输出中提取 analyse_tool 的 code 参数。"""
    code_exec = []
    seen = set()

    def _add_code(code) -> None:
        code = (code or "").strip()
        if code and code not in seen:
            seen.add(code)
            code_exec.append(code)

    def _extract_from_args(args) -> None:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except Exception:
                return
        if isinstance(args, dict):
            _add_code(args.get("code"))

    # 方案一：累加消息的规范化 tool_calls（langchain 负责解析）
    try:
        for tc in getattr(acc_msg, "tool_calls", None) or []:
            if not isinstance(tc, dict) or tc.get("name") != "analyse_tool":
                continue
            _extract_from_args(tc.get("args"))
    except Exception:
        pass

    # 方案二：按"新名字出现"切分片段并拼装（标准 OpenAI 分片流式）
    try:
        for seg in segments:
            _extract_from_args(seg["args"])
    except Exception:
        pass

    # 方案三：按 id 拼装的分片（真分片式流媒体兜底）
    try:
        for tc in tool_calls_acc.values():
            if tc["name"] != "analyse_tool":
                continue
            _extract_from_args(tc["args"])
    except Exception:
        pass

    # 方案四：旧式 additional_kwargs.tool_calls
    try:
        for tc in (getattr(acc_msg, "additional_kwargs", {}) or {}).get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name") != "analyse_tool":
                continue
            _extract_from_args(fn.get("arguments"))
    except Exception:
        pass

    return code_exec


# ── Plan-Execute-Replan 流式 ──────────────────────────────────────────────────

def _parse_structured_from_tool_calls(tool_calls_acc: dict, expected_name: str) -> dict | None:
    """从累积的 tool_call 分片中解析 structured_output 结果。
    with_structured_output 内部用工具调用承载结构化输出，工具名即 schema 名
    （如 Plan / ReplanDecision）。args 是 JSON 字符串，可能跨分片。
    """
    for tc in tool_calls_acc.values():
        if tc.get("name") == expected_name and tc.get("args"):
            try:
                return json.loads(tc["args"])
            except Exception:
                return None
    return None


def _stream_plan_execute(sid: str, query: str, df, data_desc: str,
                         datasets: list, lc: list, turn_start_ts: float,
                         result_holder: dict | None = None):
    """Plan-Execute-Replan 流式执行。
    节点流式输出通过 meta["langgraph_node"] 区分：
      - planner：结构化输出，无用户可见 token；完成后发 "plan" 事件
      - executor：AIMessage 内容发 "token"，ToolMessage 发 "tool"
      - replanner：结构化输出；完成后发 "replan" 事件
      - synthesizer：最终回复，发 "token"

    result_holder: 可选的可变 dict，用于向调用方传递信号：
      - result_holder["fallback_react"] = True 表示连续失败≥3次，需要降级 ReAct
    """
    # 注入工具访问器（与 ReAct 路径的 _get_or_create_agent 保持一致）
    from core.tools import schema_tool, analyse_tool, chart_tool, excel_tool
    _df_getter = df_registry.make_getter(sid)
    _sid_getter = lambda: sid
    for _t in (schema_tool, analyse_tool, chart_tool, excel_tool):
        _t.set_df_accessor(_df_getter)
        _t.set_sid_getter(_sid_getter)
    if datasets:
        _dfs_getter = df_registry.make_all_getter(sid, datasets)
        for _t in (schema_tool, analyse_tool, chart_tool, excel_tool):
            _t.set_dfs_accessor(_dfs_getter)
    analyse_tool.set_reload_fn(_make_reload_fn(sid))

    pe_agent = _get_or_create_pe_agent(sid)
    schema_already = _is_schema_already_called(sid)

    # 构造输入 state
    trimmed = lc[-(MAX_HISTORY_TURNS * 2):]
    # 把实际列名传给 planner/executor，避免不知道字段名而猜测
    df_columns = list(df.columns) if df is not None else []
    input_state = {
        "messages": trimmed,
        "plan": [],
        "current_step_idx": 0,
        "past_steps": [],
        "response": "",
        "data_desc": data_desc,
        "schema_already_called": schema_already,
        "columns": df_columns,
    }

    last_err = None
    full_raw = ""               # synthesizer 输出（用户可见的最终回复）
    acc_msg = None
    tool_call_fragments = []
    tool_calls_acc = {}
    tool_outputs = []           # ToolMessage.content（用于提取 CHART:/XLSX:）
    usage_in = 0
    usage_out = 0
    saw_usage = False
    recorded_tools: set = set()
    current_node = None
    # 按 node 分组累积 tool_calls_acc，便于解析 executor 的工具调用
    node_tool_calls: dict[str, dict] = {}
    # 计划与重规划信息（供 SSE 事件 + 持久化）
    plan_emitted = False
    replan_count = 0
    # planner/replanner 输出是纯文本 JSON（非 tool_calls），需累积 content 后解析
    planner_content_acc = ""
    replanner_content_acc = ""
    saved_plan_steps: list = []      # 持久化用
    saved_replan_events: list = []   # 持久化用

    config = {"recursion_limit": 50}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        full_raw = ""
        acc_msg = None
        tool_call_fragments = []
        tool_calls_acc = {}
        tool_outputs = []
        node_tool_calls = {}
        current_node = None
        plan_emitted = False
        replan_count = 0
        planner_content_acc = ""
        replanner_content_acc = ""
        try:
            for chunk, meta in pe_agent.stream(
                input_state, config=config, stream_mode="messages"
            ):
                node = meta.get("langgraph_node")
                # 节点切换：先处理上一个节点的累积 content，再发出过渡提示
                if node != current_node:
                    # planner 完成：解析累积的 JSON 文本，发出 plan 事件
                    if current_node == "planner" and planner_content_acc and not plan_emitted:
                        try:
                            from core.plan_execute import _parse_json_response, Plan
                            plan_obj = _parse_json_response(
                                planner_content_acc, Plan, list_key="steps")
                            steps_summary = [
                                {"tool": s.tool, "intent": s.intent,
                                 "result_name": s.result_name}
                                for s in plan_obj.steps
                            ]
                            plan_emitted = True
                            saved_plan_steps = steps_summary
                            yield _sse("plan", {"steps": steps_summary})
                            telemetry.record_event(sid, "plan",
                                                   step_count=len(steps_summary))
                        except Exception:
                            pass
                        planner_content_acc = ""
                    # replanner 完成：解析累积的 JSON 文本，发出 replan 事件
                    if current_node == "replanner" and replanner_content_acc:
                        try:
                            from core.plan_execute import _parse_json_response, ReplanDecision
                            decision = _parse_json_response(
                                replanner_content_acc, ReplanDecision)
                            action = decision.action
                            if action in ("replan", "finish"):
                                replan_count += 1
                                ev = {
                                    "action": action,
                                    "reason": decision.reason,
                                    "new_steps": [
                                        {"tool": s.tool, "intent": s.intent,
                                         "result_name": s.result_name}
                                        for s in decision.new_steps
                                    ],
                                }
                                saved_replan_events.append(ev)
                                yield _sse("replan", ev)
                                telemetry.record_event(
                                    sid, "replan", action=action,
                                    reason=decision.reason[:200])
                        except Exception:
                            pass
                        replanner_content_acc = ""
                    current_node = node
                    if node == "planner":
                        yield _sse("tool", {"node": "planner",
                                            "message": "正在规划分析步骤..."})
                    elif node == "executor":
                        yield _sse("tool", {"node": "executor",
                                            "message": "正在执行计划步骤..."})
                    elif node == "replanner":
                        yield _sse("tool", {"node": "replanner",
                                            "message": "正在评审执行结果..."})
                    elif node == "synthesizer":
                        yield _sse("tool", {"node": "synthesizer",
                                            "message": "正在生成最终回复..."})

                # token 用量
                try:
                    um = getattr(chunk, "usage_metadata", None)
                    if um:
                        usage_in += um.get("input_tokens", 0) or 0
                        usage_out += um.get("output_tokens", 0) or 0
                        saw_usage = True
                except Exception:
                    pass

                # ToolMessage：工具执行结果
                if isinstance(chunk, ToolMessage):
                    tc = chunk.content
                    if tc and isinstance(tc, str):
                        tool_outputs.append(tc)
                    tc_id = getattr(chunk, "tool_call_id", None)
                    if tc_id and tc_id not in recorded_tools:
                        recorded_tools.add(tc_id)
                        tool_name = getattr(chunk, "name", "") or "tool"
                        telemetry.record_event(
                            sid, "tool", name=tool_name,
                            preview=str(tc or "")[:200])
                        yield _sse("tool", {"node": node or "executor",
                                            "message": f"已执行 {tool_name}"})

                # AIMessage chunk：LLM 输出
                if isinstance(chunk, AIMessage) or hasattr(chunk, "tool_call_chunks"):
                    # 累积完整消息（用于后续 code_exec 提取）
                    acc_msg = chunk if acc_msg is None else acc_msg + chunk
                    # planner/replanner 输出是纯文本 JSON，累积 content 供节点切换时解析
                    if chunk.content and node == "planner":
                        planner_content_acc += chunk.content
                    elif chunk.content and node == "replanner":
                        replanner_content_acc += chunk.content
                    # 用户可见 token：仅 executor/synthesizer 的 content 转发
                    if chunk.content and node in ("executor", "synthesizer"):
                        full_raw += chunk.content
                        yield _sse("token", {"content": chunk.content})
                    # 收集 tool_call 分片（executor 的工具调用）
                    try:
                        for tc in getattr(chunk, "tool_call_chunks", None) or []:
                            if isinstance(tc, dict):
                                tc_id = tc.get("id")
                                tc_name = tc.get("name") or ""
                                tc_args = tc.get("args") or ""
                            else:
                                tc_id = tc.id
                                tc_name = tc.name or ""
                                tc_args = tc.args or ""
                            tool_call_fragments.append((tc_name, tc_args))
                            if tc_id:
                                entry = tool_calls_acc.setdefault(
                                    tc_id, {"name": "", "args": ""})
                                if tc_name:
                                    entry["name"] = tc_name
                                if tc_args:
                                    entry["args"] += tc_args
                    except Exception:
                        pass
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt >= MAX_ATTEMPTS:
                break
            err_msg = str(e).lower()
            if any(m in err_msg for m in FATAL_ERR_MARKERS):
                break
            is_network = any(m in err_msg for m in NETWORK_ERR_MARKERS)
            hint = (f"网络波动，正在自动重试（{attempt}/{MAX_ATTEMPTS - 1}）..." if is_network
                    else f"分析过程出错，正在自动重试（{attempt}/{MAX_ATTEMPTS - 1}）...")
            yield _sse("tool", {"node": "retry", "message": hint})
            telemetry.record_event(sid, "retry", attempt=attempt,
                                   error=str(e)[:300])
            time.sleep(1.5 * attempt)

    if last_err is not None:
        err_lower = str(last_err).lower()
        if any(m in err_lower for m in FATAL_ERR_MARKERS):
            hint = "配置或鉴权类错误，请检查 API Key、模型名称、账户余额后重试。"
        else:
            hint = ("已自动重试仍失败，可能是网络波动或大模型临时异常，"
                    "稍后重新发送问题即可（大模型是非确定性的，换次推理可能成功）。")
        yield _sse("error", {"message": f"Plan-Execute-Replan 执行出错：{last_err}",
                             "hint": hint})
        telemetry.record_event(sid, "error", message=str(last_err)[:300])
        return

    # ── 连续失败≥3次降级 ReAct ──────────────────────────────────────────
    # planner_node 返回 {"response": "__fallback_react__"} 时，graph 直接走 END，
    # full_raw 为空、plan_emitted 为 False。通知调用方降级到 ReAct。
    if not full_raw and not plan_emitted:
        if result_holder is not None:
            result_holder["fallback_react"] = True
        return

    # ── 解析输出（与 ReAct 路径一致的逻辑）──────────────────────────────
    clean_text, charts, xlsxs, tables = parse_output(full_raw)

    # 从工具输出中补充 CHART:/XLSX:/TABLE: 标记
    for tc_text in tool_outputs:
        _, t_charts, t_xlsxs, t_tables = parse_output(tc_text)
        for c in t_charts:
            if c and c not in charts:
                charts.append(c)
        for x in t_xlsxs:
            if x and x not in xlsxs:
                xlsxs.append(x)
        for t in t_tables:
            if t and t not in tables:
                tables.append(t)

    # Plan 模式兜底：从 graph 最终 state 的 past_steps 中提取 TABLE 标记。
    # Plan 模式 executor 在节点内部同步调用工具，ToolMessage 不一定被
    # stream_mode="messages" 输出，导致 tool_outputs 为空、TABLE 丢失。
    # 这里从 final state 的 past_steps 中补充。
    if not tables:
        try:
            final_state = pe_agent.get_state(config)
            for ps in (final_state.values.get("past_steps") or []):
                obs = str(ps.get("observation", ""))
                _, _, _, t_tables = parse_output(obs)
                for t in t_tables:
                    if t and t not in tables:
                        tables.append(t)
        except Exception:
            pass

    # 构建 segments + 提取 code_exec
    segments = []
    for frag_name, frag_args in tool_call_fragments:
        if not isinstance(frag_args, str):
            continue
        if frag_name:
            segments.append({"name": frag_name, "args": frag_args})
        elif segments:
            segments[-1]["args"] += frag_args

    code_exec = _extract_code_exec(
        full_raw, acc_msg, tool_call_fragments, tool_calls_acc, segments
    )

    chart_urls = [abspath_to_download_url(c) for c in charts if c]
    xlsx_urls = [abspath_to_download_url(x) for x in xlsxs if x]

    # token 用量
    duration_ms = int((time.time() - turn_start_ts) * 1000)
    from services.llm_config import get_config as _get_llm_cfg
    model = _get_llm_cfg().get("model", "")
    if saw_usage:
        usage = telemetry.build_usage(usage_in, usage_out, model=model,
                                      duration_ms=duration_ms)
    else:
        in_est = telemetry.estimate_tokens(
            "".join(str(getattr(m, "content", "")) for m in lc) + query)
        out_est = telemetry.estimate_tokens(full_raw)
        usage = telemetry.build_usage(in_est, out_est, model=model,
                                      duration_ms=duration_ms, estimated=True)
    session_usage = telemetry.accumulate_session_usage(sid, usage)
    telemetry.record_event(sid, "turn_end", duration_ms=duration_ms,
                           input_tokens=usage["input_tokens"],
                           output_tokens=usage["output_tokens"],
                           estimated=usage["estimated"], mode="plan_execute")

    # 保存
    lc.append(AIMessage(content=clean_text))
    session_store.append_message(
        sid, "assistant", clean_text,
        charts=chart_urls, xlsxs=xlsx_urls,
        tables=tables, code_exec=code_exec,
        usage=usage,
        mode="plan",
        plan_steps=saved_plan_steps,
        replan_events=saved_replan_events,
    )

    yield _sse("done", {
        "content": clean_text,
        "charts": chart_urls,
        "xlsxs": xlsx_urls,
        "tables": tables,
        "code_exec": code_exec,
        "usage": usage,
        "session_usage": session_usage,
    })


# ── 主入口：流式聊天 ──────────────────────────────────────────────────────────

def stream_chat(sid: str, query: str, skill_id: str | None = None, mode: str | None = None):
    """
    同步生成器，yield SSE 事件字符串。
    流程：Skill 调度（确定性流程） → agent 流式 → 解析输出 → 提取代码 → 保存 → done。
    skill_id 非空时强制走该 skill；为空时按关键词命中任一启用的 skill。
    mode: 用户选择的模式，"react"/"plan"/None(=auto)。用户选 react/plan 时跳过自动路由。
    """
    sess = session_store.get(sid)
    if sess is None:
        yield _sse("error", {"message": f"会话不存在：{sid}"})
        return

    telemetry.record_event(sid, "turn_start", query=query[:200],
                           skill_id=skill_id or None)
    turn_start_ts = time.time()

    # 加载/重建所有数据集
    df_registry.get_or_load_all(sid, sess)
    df = df_registry.get(sid)
    if df is None:
        yield _sse("error", {"message": "当前会话未加载数据，请先上传文件。"})
        return

    data_desc = sess.get("df_name") or "数据集"
    datasets = sess.get("datasets", [])

    # 上次出错/打断遗留的孤立 user 消息（无 assistant 回复）会让本次新提问把它一起带给 LLM。
    # 检测到时刷新内存 lc 缓存，让 _get_lc 重建时通过 _rebuild_lc_messages 过滤掉末尾孤立消息。
    # 注意：session_store 中的孤立消息保留（UI 仍可见），仅从 LLM 上下文中剔除。
    _msgs = sess.get("messages", [])
    if _msgs and _msgs[-1].get("role") == "user":
        reset_lc(sid)

    # 记录用户消息
    session_store.append_message(sid, "user", query)
    lc = _get_lc(sid)
    lc.append(HumanMessage(content=query))

    # ── 分支 1：Skill 调度（确定性流程，不走 LLM）─────────────────────────
    # 自定义质量规则（跨字段一致性等）仍交给 LLM 生成代码，不进 skill 分支。
    custom_quality = _is_custom_quality(query)
    should_try_skill = (skill_id is not None) or (not custom_quality)
    if should_try_skill:
        skills = skill_store.enabled_specs()
        ctx = SkillContext(
            df=df, query=query,
            dfs=df_registry.make_all_getter(sid, datasets)() if datasets else None,
            data_desc=data_desc, sid=sid,
        )
        dispatched = dispatch_skill(query, ctx, skill_id=skill_id, skills=skills)
        if dispatched is not None:
            spec, result = dispatched
            yield _sse("tool", {
                "node": f"skill:{spec.id}",
                "message": f"正在执行技能：{spec.name}...",
            })
            try:
                dur_ms = int((time.time() - turn_start_ts) * 1000)
                telemetry.record_event(sid, "skill", skill_id=spec.id,
                                       name=spec.name, duration_ms=dur_ms)
                # Skill 确定性流程不走 LLM，token 消耗为 0
                usage = telemetry.build_usage(0, 0, duration_ms=dur_ms)
                session_usage = telemetry.accumulate_session_usage(sid, usage)
                lc.append(AIMessage(content=result.content))
                session_store.append_message(
                    sid, "assistant", result.content,
                    charts=result.chart_urls,
                    xlsxs=result.xlsx_urls,
                    tables=result.tables,
                    usage=usage,
                )
                yield _sse("done", {
                    "content":   result.content,
                    "charts":    result.chart_urls,
                    "xlsxs":     result.xlsx_urls,
                    "tables":    result.tables,
                    "code_exec": result.code_exec,
                    "usage":        usage,
                    "session_usage": session_usage,
                })
            except Exception as e:
                yield _sse("error", {"message": f"技能「{spec.name}」执行出错：{e}"})
            return

    # ── RAG Agent 前置：术语查询 / 字段匹配（仅在需要时触发）──────────────
    # 规则预判断：列名异常 或 问题含术语关键词 → 走 RAG 流程
    # RAG 流程现在用普通函数（非 LangChain Tool/Agent 图）：
    #   按需自动 search_knowledge() → 拼 context → 单次 LLM 调用生成带标记回复
    # 大部分纯分析任务不命中，零额外开销，直接进 Router。
    from core.rag_agent import (
        should_run_rag, run_rag_stream, parse_rag_response, is_marker_line,
    )
    df_columns = list(df.columns) if df is not None else []
    rag_usage_in = 0
    rag_usage_out = 0
    rag_ran = False

    if should_run_rag(query, df_columns):
        rag_ran = True
        yield _sse("tool", {"node": "rag", "message": "正在检索业务知识库..."})
        telemetry.record_event(sid, "rag_start", query=query[:200])

        rag_full_raw = ""
        buffer_line = ""   # 流式按行判断标记（避免 token 级切分导致匹配失败）

        try:
            async def _rag_aiter():
                async for tok, usage in run_rag_stream(query, df_columns):
                    yield tok, usage
            import asyncio as _aio
            agen = _rag_aiter()
            loop = _aio.new_event_loop()
            try:
                while True:
                    try:
                        tok, usage = loop.run_until_complete(
                            _aio.wait_for(agen.__anext__(), timeout=120)
                        )
                    except StopAsyncIteration:
                        break
                    # token 与用量二选一：结束元组 (None, usage) 只带用量，不带文本
                    if usage:
                        in_t = int(usage.get("input_tokens", 0) or 0)
                        ot_t = int(usage.get("output_tokens", 0) or 0)
                        if in_t or ot_t:
                            rag_usage_in  += in_t
                            rag_usage_out += ot_t
                        else:
                            # 字符级兜底：~2.5 中文字符/token
                            if "_input_chars" in usage:
                                rag_usage_in  += int(usage["_input_chars"] / 2.5) or 1
                            # output 基于已收到的 rag_full_raw 字符长度（此时已累积完流式 token）
                            rag_usage_out += int(len(rag_full_raw) / 2.5) or 1
                    if tok is None:
                        # 仅用量元组，跳过文本处理
                        continue
                    rag_full_raw += tok
                    # 按行缓冲，完整行出来后判断是否标记
                    buffer_line += tok
                    while "\n" in buffer_line:
                        line, buffer_line = buffer_line.split("\n", 1)
                        if not is_marker_line(line):
                            yield _sse("token", {"content": line + "\n", "node": "rag"})
            finally:
                loop.close()
            # 最后把 buffer 里剩余的（非标记行）吐出去
            if buffer_line and not is_marker_line(buffer_line):
                yield _sse("token", {"content": buffer_line, "node": "rag"})
        except Exception as e:
            yield _sse("tool", {"node": "rag",
                                "message": f"RAG Agent 异常，跳过：{e}"})
            rag_full_raw = ""

        # 解析标记
        rag_result = parse_rag_response(rag_full_raw)
        telemetry.record_event(
            sid, "rag_done",
            reload=bool(rag_result["reload_columns"]),
            continue_analysis=rag_result["continue_analysis"],
            answer_only=rag_result["answer_only"],
            input_tokens=rag_usage_in, output_tokens=rag_usage_out,
        )

        # 执行 reload_df（如果 RAG Agent 解析到了列名）
        if rag_result["reload_columns"]:
            new_cols = rag_result["reload_columns"]
            if df is not None and len(new_cols) == len(df.columns):
                reload_fn = _make_reload_fn(sid)
                reload_msg = reload_fn(new_cols)
                yield _sse("tool", {"node": "rag", "message": reload_msg})
                # 刷新 df（reload 后列名已变）
                df = df_registry.get(sid)
                df_columns = list(df.columns) if df is not None else []
            else:
                yield _sse("tool", {"node": "rag",
                                    "message": f"列数不匹配（知识库 {len(new_cols)} 列 vs 数据 {len(df.columns) if df is not None else 0} 列），跳过字段修复"})

        # 默认结束：除非 RAG Agent 明确输出 CONTINUE_ANALYSIS，否则本次到此为止。
        # 这样即使 LLM 忘了输出标记，也不会错误地继续走数据分析 Agent。
        if not rag_result["continue_analysis"]:
            clean_text = rag_result["clean_text"] or "已处理完毕。"
            from services.llm_config import get_config as _get_llm_cfg
            _model = _get_llm_cfg().get("model", "")
            _usage = telemetry.build_usage(rag_usage_in, rag_usage_out, model=_model)
            _session_usage = telemetry.accumulate_session_usage(sid, _usage)
            telemetry.record_event(sid, "turn_end",
                                    input_tokens=rag_usage_in,
                                    output_tokens=rag_usage_out, mode="rag")
            lc.append(AIMessage(content=clean_text))
            session_store.append_message(sid, "assistant", clean_text,
                                         usage=_usage, mode="rag")
            yield _sse("done", {
                "content": clean_text,
                "charts": [], "xlsxs": [], "tables": [], "code_exec": [],
                "usage": _usage,
                "session_usage": _session_usage,
            })
            return

        # CONTINUE_ANALYSIS：把 RAG 查到的业务知识作为上下文注入数据分析 Agent
        if rag_result["clean_text"]:
            rag_context = f"\n\n---\n【业务知识参考】\n{rag_result['clean_text']}"
            if lc and isinstance(lc[-1], HumanMessage):
                lc[-1] = HumanMessage(content=lc[-1].content + rag_context)

    # 把 RAG Agent 的 token 用量累计到会话总量（CONTINUE_ANALYSIS 场景）
    if rag_ran and (rag_usage_in > 0 or rag_usage_out > 0):
        from services.llm_config import get_config as _get_llm_cfg
        _rag_model = _get_llm_cfg().get("model", "")
        _rag_usage = telemetry.build_usage(rag_usage_in, rag_usage_out, model=_rag_model)
        telemetry.accumulate_session_usage(sid, _rag_usage)

    # ── 路由：决定走 ReAct 还是 Plan-Execute-Replan ──────────────────────
    # 默认为 ReAct（简单单步快响应）；用户显式指定或规则+LLM判定复杂时才切 Plan。
    # 路由结果作为 SSE 事件回传前端，便于在轨迹中展示。
    has_multi_tables = bool(datasets) and len(datasets) > 1
    from core.router import RouteDecision as _RD
    mode_norm = (mode or "").strip().lower()
    user_override = False
    if mode_norm == "react":
        rd = _RD(mode="react", complexity=3, estimated_steps=1,
                 reason="用户选择：ReAct 模式（单步快速响应）")
        user_override = True
    elif mode_norm == "plan":
        rd = _RD(mode="plan", complexity=8, estimated_steps=4,
                 reason="用户选择：Plan-Execute-Replan 模式（多步规划执行）")
        user_override = True
    else:
        # 默认走 ReAct，只有 route_query 返回【明确极复杂】才升级到 plan：
        #   mode == plan 且 complexity >= 8 且 estimated_steps >= 4
        # 大部分常见分析（3 步以内）走 ReAct 更稳更快。
        try:
            rd_tmp = route_query(query, data_desc, has_multi_tables)
            if (rd_tmp.mode == "plan"
                    and (rd_tmp.complexity or 0) >= 8
                    and (rd_tmp.estimated_steps or 0) >= 4):
                rd = rd_tmp
            else:
                rd = _RD(mode="react", complexity=3, estimated_steps=2,
                         reason="默认模式：ReAct（单步快速响应）")
        except Exception:
            rd = _RD(mode="react", complexity=3, estimated_steps=2,
                     reason="路由异常，降级 react")
    yield _sse("route", {
        "mode": rd.mode,
        "complexity": rd.complexity,
        "estimated_steps": rd.estimated_steps,
        "reason": rd.reason,
        "user_override": user_override,
        "query": query[:100],
    })
    telemetry.record_event(sid, "route", mode=rd.mode, complexity=rd.complexity,
                           estimated_steps=rd.estimated_steps,
                           user_override=user_override, query=query[:100])

    # 上下文压缩：消息过多时滚动摘要老消息，省 token（两条路径共用）
    lc = _maybe_summarize(sid, lc)
    _lc_registry[sid] = lc

    if rd.mode == "plan":
        # ── 分支 2a：Plan-Execute-Replan 流式 ────────────────────────────
        pe_result: dict = {}
        yield from _stream_plan_execute(sid, query, df, data_desc, datasets,
                                        lc, turn_start_ts, pe_result)
        if pe_result.get("fallback_react"):
            # Plan 连续失败≥3次，降级到 ReAct
            yield _sse("tool", {"node": "fallback",
                                "message": "Plan模式连续执行失败，自动降级到ReAct模式..."})
            telemetry.record_event(sid, "fallback_react", query=query[:100])
        else:
            return  # Plan 正常完成

    # ── 分支 2b：ReAct 流式（原有逻辑 / Plan 降级兜底）────────────────────
    _df_getter = df_registry.make_getter(sid)
    agent = _get_or_create_agent(sid, df, data_desc, _df_getter, datasets)

    last_err = None
    full_raw = ""
    acc_msg = None
    tool_call_fragments = []
    tool_calls_acc = {}
    # 直接收集工具返回内容（ToolMessage），CHART:/XLSX: 标记从这里提取，
    # 不再依赖 LLM 在最终文本中转发标记（连续对话时 LLM 常漏转发）。
    tool_outputs = []
    # token 用量累计（跨重试累加——失败的重试同样消耗 token）
    usage_in = 0
    usage_out = 0
    saw_usage = False
    recorded_tools: set = set()   # 按 tool_call_id 去重，避免同一工具记录多次

    for attempt in range(1, MAX_ATTEMPTS + 1):
        full_raw = ""
        acc_msg = None
        tool_call_fragments = []
        tool_calls_acc = {}
        tool_outputs = []
        try:
            trimmed = lc[-(MAX_HISTORY_TURNS * 2):]
            for chunk, meta in agent.stream(
                {"messages": trimmed}, stream_mode="messages"
            ):
                node = meta.get("langgraph_node")
                # token 用量：stream_usage=True 时每次 LLM 调用的末块带 usage_metadata
                try:
                    um = getattr(chunk, "usage_metadata", None)
                    if um:
                        usage_in  += um.get("input_tokens", 0) or 0
                        usage_out += um.get("output_tokens", 0) or 0
                        saw_usage = True
                except Exception:
                    pass
                if node == "tools":
                    yield _sse("tool", {"node": "tools",
                                        "message": "正在执行分析工具..."})
                    # 捕获工具返回内容（ToolMessage.content）
                    tc = getattr(chunk, "content", None)
                    if tc and isinstance(tc, str):
                        tool_outputs.append(tc)
                    # 记录工具调用轨迹（按 tool_call_id 去重）
                    tc_id = getattr(chunk, "tool_call_id", None)
                    if tc_id and tc_id not in recorded_tools:
                        recorded_tools.add(tc_id)
                        telemetry.record_event(
                            sid, "tool",
                            name=getattr(chunk, "name", "") or "tool",
                            preview=str(tc or "")[:200])
                if node == "agent":
                    acc_msg = chunk if acc_msg is None else acc_msg + chunk
                    if chunk.content:
                        full_raw += chunk.content
                        yield _sse("token", {"content": chunk.content})
                # 收集工具调用分片（原样收集，不按 id 合并）
                try:
                    for tc in getattr(chunk, "tool_call_chunks", None) or []:
                        if isinstance(tc, dict):
                            tc_id   = tc.get("id")
                            tc_name = tc.get("name") or ""
                            tc_args = tc.get("args") or ""
                        else:
                            tc_id   = tc.id
                            tc_name = tc.name or ""
                            tc_args = tc.args or ""
                        tool_call_fragments.append((tc_name, tc_args))
                        if tc_id:
                            entry = tool_calls_acc.setdefault(
                                tc_id, {"name": "", "args": ""}
                            )
                            if tc_name:
                                entry["name"] = tc_name
                            if tc_args:
                                entry["args"] += tc_args
                except Exception:
                    pass
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt >= MAX_ATTEMPTS:
                break
            err_msg = str(e).lower()
            # 不可恢复错误（认证/计费/模型不存在/上下文超长）：直接报错，不重试
            if any(m in err_msg for m in FATAL_ERR_MARKERS):
                break
            # 其他错误都重试（网络波动/序列化失败/代码错误/沙箱异常等，
            # LLM 是非确定性的，重试时换种写法可能就成功）
            is_network = any(m in err_msg for m in NETWORK_ERR_MARKERS)
            hint = (f"网络波动，正在自动重试（{attempt}/{MAX_ATTEMPTS - 1}）..." if is_network
                    else f"分析过程出错，正在自动重试（{attempt}/{MAX_ATTEMPTS - 1}）...")
            yield _sse("tool", {"node": "retry", "message": hint})
            telemetry.record_event(sid, "retry", attempt=attempt,
                                   error=str(e)[:300])
            time.sleep(1.5 * attempt)   # 递增 backoff：1.5s / 3s

    if last_err is not None:
        err_lower = str(last_err).lower()
        if any(m in err_lower for m in FATAL_ERR_MARKERS):
            hint = "配置或鉴权类错误，请检查 API Key、模型名称、账户余额后重试。"
        else:
            hint = ("已自动重试仍失败，可能是网络波动或大模型临时异常，"
                    "稍后重新发送问题即可（大模型是非确定性的，换次推理可能成功）。")
        yield _sse("error", {
            "message": f"Agent 执行出错：{last_err}",
            "hint": hint
        })
        telemetry.record_event(sid, "error", message=str(last_err)[:300])
        return

    # ── 解析输出 ──────────────────────────────────────────────────────────
    # 优先从工具返回内容中提取 CHART:/XLSX: 标记（可靠来源），
    # 再补充 LLM 文本中可能转发的标记，去重后合并。
    clean_text, charts, xlsxs, tables = parse_output(full_raw)

    # 从工具输出中提取标记（连续对话时 LLM 常漏转发，这是图片不显示的根因）
    for tc_text in tool_outputs:
        _, t_charts, t_xlsxs, _ = parse_output(tc_text)
        for c in t_charts:
            if c and c not in charts:
                charts.append(c)
        for x in t_xlsxs:
            if x and x not in xlsxs:
                xlsxs.append(x)

    # 构建 segments（方案二用）
    segments = []
    for frag_name, frag_args in tool_call_fragments:
        if not isinstance(frag_args, str):
            continue
        if frag_name:
            segments.append({"name": frag_name, "args": frag_args})
        elif segments:
            segments[-1]["args"] += frag_args

    code_exec = _extract_code_exec(
        full_raw, acc_msg, tool_call_fragments, tool_calls_acc, segments
    )

    # 路径转下载 URL
    chart_urls = [abspath_to_download_url(c) for c in charts if c]
    xlsx_urls  = [abspath_to_download_url(x) for x in xlsxs if x]

    # ── token 用量与成本计量 ────────────────────────────────────────────
    duration_ms = int((time.time() - turn_start_ts) * 1000)
    from services.llm_config import get_config as _get_llm_cfg
    model = _get_llm_cfg().get("model", "")
    if saw_usage:
        # 服务商返回了真实 usage
        usage = telemetry.build_usage(usage_in, usage_out, model=model,
                                      duration_ms=duration_ms)
    else:
        # 兜底：按字符数粗略估算（输入=发给 agent 的历史消息，输出=生成文本）
        in_est  = telemetry.estimate_tokens(
            "".join(str(getattr(m, "content", "")) for m in lc))
        out_est = telemetry.estimate_tokens(full_raw)
        usage = telemetry.build_usage(in_est, out_est, model=model,
                                      duration_ms=duration_ms, estimated=True)
    session_usage = telemetry.accumulate_session_usage(sid, usage)
    telemetry.record_event(sid, "turn_end", duration_ms=duration_ms,
                           input_tokens=usage["input_tokens"],
                           output_tokens=usage["output_tokens"],
                           estimated=usage["estimated"])

    # 保存
    lc.append(AIMessage(content=clean_text))
    session_store.append_message(
        sid, "assistant", clean_text,
        charts=chart_urls, xlsxs=xlsx_urls,
        tables=tables, code_exec=code_exec,
        usage=usage,
        mode="react",
    )

    yield _sse("done", {
        "content":  clean_text,
        "charts":   chart_urls,
        "xlsxs":    xlsx_urls,
        "tables":   tables,
        "code_exec": code_exec,
        "usage":        usage,
        "session_usage": session_usage,
    })


def run_code(sid: str, code: str) -> str:
    """UI「重新执行」入口：执行编辑后的代码，返回 Markdown 结果。"""
    from core.tools.analyse_tool import run_analysis_code
    # 确保工具访问到当前会话的 df
    sess = session_store.get(sid)
    if sess is None:
        return "❌ 会话不存在"
    df_registry.get_or_load_all(sid, sess)
    datasets = sess.get("datasets", [])
    # 重新注入 getter（run_analysis_code 内部用模块级 _df_accessor / _dfs_accessor）
    from core.tools import analyse_tool
    analyse_tool.set_df_accessor(df_registry.make_getter(sid))
    if datasets:
        analyse_tool.set_dfs_accessor(df_registry.make_all_getter(sid, datasets))
    analyse_tool.set_sid_getter(lambda: sid)
    return run_analysis_code(code)
