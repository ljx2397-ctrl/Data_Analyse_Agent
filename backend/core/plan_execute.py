# backend/core/plan_execute.py
# Plan-Execute-Replan Agent：基于 LangGraph StateGraph 实现。
#
# 图结构：
#   START → planner → executor → replanner ─┬─ continue → executor（下一步）
#                                             ├─ replan   → planner（重规划剩余步骤）
#                                             └─ finish   → synthesizer → END
#
# - planner：生成计划（手动解析 JSON，兼容不支持 function calling 的模型）
# - executor：bind_tools(all_tools)，单步执行（一次工具调用）
# - replanner：评审并决定后续动作（continue/replan/finish，手动解析 JSON）
# - synthesizer：汇总 past_steps 生成最终自然语言回复
#
# 与现有 ReAct 共用 create_llm / all_tools / build_system_prompt 的核心规则。
# 流式：graph.stream(stream_mode="messages") 会拾取所有节点的 LLM token chunk，
#      通过 meta["langgraph_node"] 区分来源（planner/executor/replanner/synthesizer/tools）。
import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from core.agent import build_system_prompt, create_llm
from core.tools import all_tools


def _parse_json_response(text: str, model_cls, list_key: str = None):
    """从 LLM 文本回复中解析 JSON 并用 Pydantic 模型验证。

    支持纯 JSON / JSON 数组（自动包装成 {list_key: [...]}）/ markdown 代码块 /
    带前后说明文字的 JSON。兼容不支持 function calling 的模型（如 qwen-flash）。
    """
    data = None
    # 1) 先尝试直接解析
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # 2) 尝试从 markdown 代码块提取
    if data is None:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text or "")
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    # 3) 尝试提取第一个 { ... } 或 [ ... ] 片段
    if data is None:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = (text or "").find(opener)
            end = (text or "").rfind(closer)
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                    break
                except json.JSONDecodeError:
                    continue
    if data is None:
        raise ValueError(f"无法从 LLM 回复中解析 JSON：{(text or '')[:300]}")
    # 列表 → 对象（当模型期望一个含列表字段的对象时）
    if isinstance(data, list) and list_key:
        data = {list_key: data}
    return model_cls.model_validate(data)


# ── 数据结构（供 structured_output 使用）──────────────────────────────────────

class PlanStep(BaseModel):
    """计划中的一个步骤。"""
    tool: str = Field(
        description="这一步要调用的工具名，可选值：schema_tool / analyse_tool / "
                    "chart_tool / excel_tool"
    )
    intent: str = Field(
        description="这一步要做什么，具体目标（一句话，如：统计各支行交易金额并排名）"
    )
    result_name: str = Field(
        description="本次 analyse_tool 结果的命名（仅 analyse_tool 必填，"
                    "后续 chart_tool/excel_tool 通过此名复用 result；其他工具填空字符串）"
    )
    reason: str = Field(
        description="为什么需要这一步（简短说明，便于 replanner 判断是否需要调整）"
    )


class Plan(BaseModel):
    """分析计划。"""
    steps: list[PlanStep] = Field(
        description="按顺序执行的步骤列表。第一步通常为 schema_tool（若尚未确认列名）。"
                    "后续步骤为 analyse_tool（必填 result_name）→ chart_tool/excel_tool。"
                    "导出/画图步骤必须通过 result_name 复用前序 analyse_tool 的结果，禁止重算。"
    )


class ReplanDecision(BaseModel):
    """replanner 的决策。"""
    action: str = Field(
        description="决策动作：continue（继续执行下一步）/ replan（重新规划剩余步骤）/ finish（已收集足够信息，生成最终回复）"
    )
    reason: str = Field(description="决策理由（简短）")
    new_steps: list[PlanStep] = Field(
        default_factory=list,
        description="仅 action=replan 时填写：替换剩余未执行步骤的新计划。"
                    "action=continue/finish 时填空列表。"
    )


# ── State ─────────────────────────────────────────────────────────────────────

class PlanExecuteState(TypedDict, total=False):
    """Plan-Execute-Replan 图的状态。"""
    messages: Annotated[list, add_messages]
    plan: list[dict]              # PlanStep 序列化后的 dict 列表
    current_step_idx: int         # 下一个要执行的步骤下标
    past_steps: list[dict]        # 已执行步骤：[{step, observation}]
    response: str                 # 最终用户回复
    # 上下文信息（不入消息历史，仅供节点读取）
    data_desc: str
    schema_already_called: bool
    columns: list[str]            # 数据集列名（让 planner/executor 知道实际字段名）
    consecutive_failures: int     # 连续失败次数（≥3 触发降级 react，见 planner_node）


# ── 工具名 → 工具实例映射 ─────────────────────────────────────────────────────

_TOOL_BY_NAME = {t.name: t for t in all_tools}


# ── Planner ───────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """你是数据分析任务规划器。根据用户问题和数据集信息，制定一个清晰的工具调用计划。

【可用工具】
- schema_tool：确认数据列名。同一会话只需调用一次。多表场景首次调用会展示所有表 schema。
- analyse_tool：执行 Pandas 代码做统计/过滤/聚合/趋势/TopN 分析。必须为结果命名（result_name）。
- chart_tool：画图。必须通过 result_name 或 use_last_result 复用前序 analyse_tool 的结果，禁止重算。
- excel_tool：导出 Excel。必须通过 result_name 复用前序结果，禁止重算。
- 业务术语/字段匹配已在对话前由 RAG Agent 处理，你不需要再调用知识库工具。

━━━━ 规划必须遵守的硬约束（违反会导致规划直接失败）━━━━
【H1 禁止写 schema_tool】
"实际列名"已提供（且不为空）时，直接基于这些列名规划分析步骤，**永远不要规划 schema_tool**。
列名/业务知识已由前置 RAG Agent 处理。

【H2 save_as 与 use_result 强对齐】
每个 analyse_tool 的 result_name 必须写在步骤里；后续 chart_tool / excel_tool 的 intent
中必须明确写出引用哪个 result_name（名字完全一致，不要改词）。如果 plan 里写
result_name="不匹配客户名单"，后续 excel_tool 就必须写用这个名字，不能瞎编别的。

【H3 涉及分类/映射/匹配规则的问题 — 第一步必须先探查实际唯一值】
**典型场景**：风险偏好 vs 风险等级匹配、客户等级分类、交易渠道归一、AUM 分层等。
这是最高频错误来源：你无法在规划阶段凭空知道数据里"风险偏好"的取值到底是
["保守型""进取型"] 还是 ["R1/R2/R3"]，更无法凭空臆造映射字典。
**必须在 plan 的第 1 步就用 analyse_tool 打印这些列的 unique()**：
  例：print(df['风险偏好'].unique())；print(df['风险等级'].unique())
拿到真实取值后，**第 2 步再**基于实际取值写匹配逻辑与统计。
没有"先探 unique 再写规则"的 plan = 会被自动否决并重写。

【H4 不要凭空定义未在代码显式声明的变量/字典/列表】
规划 intent 中不要写"用 match_map 做匹配""按 risk_order 排序"这种话——
除非你在同一步 analyse_tool 里先写代码把 match_map / risk_order 定义出来。

【H5 不要多此一举】
用户没明确要求画图/导出时，不要主动加 chart_tool/excel_tool。
能合并在一个 analyse_tool 里做完的统计（如同时算匹配数和不匹配名单）就拆成 1 步，
不要为了"显得有规划"硬拆成 3 步（先算匹配→再算不匹配→再导出→再加 3 个中间 result）。

【规划原则（软约束）】
1. chart_tool / excel_tool 步骤必须通过 result_name 复用前序结果，不要重新计算。
2. 步骤数量控制在 2~5 步。1 步的任务走 ReAct（不需要 Plan）。
3. 多表场景下，analyse_tool 步骤的 intent 中说明使用 pd.merge 关联哪些表。
4. analyse_tool 步骤的 intent 中必须写明用哪些列名做什么操作。
5. 用户要求导出时，excel_tool 步骤的 intent 中必须列出要导出哪些 result_name。
   excel_tool 支持一次导出多个 sheet。
6. 用户要求画图时，每个 chart_tool 步骤只画一张图，通过 result_name 指定数据来源。

【输出要求】
- 以 JSON 对象格式输出，字段结构如下：
{
  "steps": [
    {
      "tool": "工具名（schema_tool/analyse_tool/chart_tool/excel_tool）",
      "intent": "这一步要做什么",
      "result_name": "仅 analyse_tool 必填，其他工具填空字符串",
      "reason": "为什么需要这一步"
    }
  ]
}
- steps 中每个元素必须包含 tool / intent / result_name / reason 四个字段，缺一不可。
- result_name 仅在 tool=analyse_tool 时必填，其他工具填空字符串。
"""

_PLANNER_USER = """数据集：{data_desc}
schema 已确认：{schema_called}
实际列名：{columns}

用户问题：{query}
{prev_fail_warning}

请制定执行计划。"""

# 「匹配 / 映射 / 分类 / 分层 / 对应关系」等高频场景——必须先探 unique 再写规则
_H3_PROBE_KEYWORDS = (
    "匹配", "映射", "对应", "分类", "归类", "分层", "等级", "规则",
    "偏好", "风险", "不匹配", "一致性",
)


def _plan_needs_probe(steps: list[dict], query: str) -> str | None:
    """检查 plan 是否违反 H3（匹配类问题未先探 unique）。
    返回 None 表示通过；否则返回一段文字说明违反原因，用于自动重写 planner。
    """
    q = query or ""
    if not any(k in q for k in _H3_PROBE_KEYWORDS):
        return None  # 不是匹配类问题，不检查
    # steps 里是否有第 1 步探 unique
    if not steps:
        return "匹配/映射类问题，但 plan 为空"
    first = steps[0]
    first_intent = (first.get("intent") or "") + " " + (first.get("reason") or "")
    # 探 unique 的标志：unique / 唯一值 / print(...unique / 打印取值
    if not any(mark in first_intent for mark in
               ["unique", "唯一值", "打印取值", ".unique", "查看取值", "探查取值"]):
        return ("H3 违反：匹配/映射类问题必须【第 1 步】用 analyse_tool "
                "打印相关列的 unique() 探查实际取值，"
                "不能直接臆造 match_map / risk_order 等变量写规则。")
    return None


def planner_node(state: PlanExecuteState) -> dict:
    """生成计划。手动解析 JSON，兼容不支持 function calling 的模型。

    附加两道关卡：
    1. 连续失败 ≥3 次 → 返回 "__fallback_react__" 触发 agent_service 降级 ReAct
    2. Plan 通过后跑 H3 预检（匹配类问题必须先探 unique），未通过自动再调用一次 Planner
       （附带明确违反原因，LLM 下次必定按 H3 重写），最多重试 2 次。
    """
    global _plan_violation_msg
    query = ""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            query = m.content
            break

    # ── 关卡 1：失败限次 ──────────────────────────────────────────────────────
    consec = int(state.get("consecutive_failures") or 0)
    if consec >= 3:
        return {"response": "__fallback_react__"}

    cols = state.get("columns") or []
    llm = create_llm()

    # 前次失败警告：写在 user prompt 末尾，让 LLM 看到连续 replan 的原因
    if consec > 0:
        prev_warn = (f"\n\n⚠️ 前序已连续 {consec} 次执行出错，"
                     "请特别注意：不要臆造 match_map/risk_order 等未定义变量，"
                     "代码里用到的所有字典/列表必须先显式声明。")
    else:
        prev_warn = ""

    # ── 关卡 2：H3 预检（最多重试 2 次）─────────────────────────────────────────
    steps: list[dict] = []
    for attempt in range(2):
        prompt = _PLANNER_USER.format(
            data_desc=state.get("data_desc", "数据集"),
            schema_called="是" if state.get("schema_already_called") else "否",
            columns=", ".join(cols) if cols else "（未知，需先调 schema_tool）",
            query=query,
            prev_fail_warning=prev_warn,
        )
        # 第二次重试时，把上次 plan 的违反原因也塞进去，LLM 针对性修
        if attempt > 0:
            prompt += (
                f"\n\n上一次规划违反了硬约束，原因是：【{_plan_violation_msg}】。"
                f"请按该原因重新规划，不要再犯同样错误。"
            )
        resp = llm.invoke([
            SystemMessage(content=_PLANNER_SYSTEM),
            HumanMessage(content=prompt),
        ])
        plan_obj = _parse_json_response(resp.content, Plan, list_key="steps")
        candidate = [s.model_dump() for s in plan_obj.steps]
        violation = _plan_needs_probe(candidate, query)
        if violation is None:
            steps = candidate
            break
        # 存下违反原因给下次重试
        _plan_violation_msg = violation
    else:
        # 两次都没过 H3，直接用最后一次的 plan（总比没 plan 好）
        steps = candidate

    return {
        "plan": steps,
        "current_step_idx": 0,
        "past_steps": [],
        # 重置失败计数：planner 每次规划都当成新起点
        # （连续失败计数只在 executor → replanner 路径上递增）
        "consecutive_failures": 0,
    }


# 存放 H3 预检违反原因，跨 planner 重试回合使用
_plan_violation_msg: str = ""


# ── Executor ──────────────────────────────────────────────────────────────────

_EXECUTOR_SYSTEM = """你是计划执行器。根据给定的步骤意图，调用对应工具完成这一步。

【最重要的规则：每次 analyse_tool 都是独立沙箱】
每次 analyse_tool 调用都是一个全新的 Python 环境。前序步骤中定义的变量（如 match_map、
risk_order、临时 DataFrame）**不会保留到本步**。你必须在本次代码中重新定义所有需要用到的
变量、字典、列表。唯一能跨步骤复用的是通过 result_name 缓存的 result（chart_tool/excel_tool
可用 result_name 取用）。

【灵活变通原则】
步骤的 intent 是指导方向，不是死命令。你应该基于「已完成步骤」中实际观察到的数据来调整实现：
- 如果前一步探查到风险偏好的取值是 ['保守型','进取型','成长型','稳健型','平衡型']，
  本步写匹配逻辑时就必须用这些真实值，不要臆造。
- 如果前一步的 observation 暴露了数据结构（如列名、取值范围、数据类型），直接据此写代码。
- 如果前一步执行出错，本步应该换一种写法，不要重复已失败的代码模式。

【其他规则】
1. 按步骤的 tool 字段调用对应工具。
2. analyse_tool 的 code 中最终结果必须赋值给 result 变量。
3. analyse_tool 必须传 result_name 参数（用步骤中指定的名字）。
4. chart_tool / excel_tool 必须通过 result_name 复用前序结果，禁止重写 data_expr。
5. 不要输出多余文字，直接调用工具即可。
6. excel_tool 多 result_name 时，每个创建一个 sheet。
7. chart_tool 调用时，确认 result_name 对应的结果包含 x_col/y_col 指定的列。
8. ⚠️ analyse_tool 的 code 中禁止 import 任何模块（pd/np/datetime/re/json 已预置）。
   禁止访问 __ 开头属性，禁止调用 eval/exec/getattr 等越权函数。

【可用工具的详细规则】
{tool_rules}
"""

_EXECUTOR_USER = """当前要执行的步骤：
  - 工具：{tool}
  - 目标：{intent}
  - result_name：{result_name}

数据集实际列名：{columns}

已完成的步骤（含实际代码和观察结果，请基于这些真实数据写本步代码）：
{past_steps_summary}

请调用 {tool} 完成这一步。
⚠️ 提醒：本步代码中用到的所有变量/字典/列表必须在本步代码内显式定义，前序步骤的变量不会保留。
请参考「已完成的步骤」中的观察结果获取真实数据取值。"""


def _build_past_steps_summary(past_steps: list[dict]) -> str:
    if not past_steps:
        return "（无）"
    lines = []
    for i, ps in enumerate(past_steps, 1):
        step = ps.get("step", {})
        obs = ps.get("observation", "")
        code = ps.get("code", "")
        # observation 截断到 1500 字符（够看清数据取值即可，省 token）
        obs_short = obs[:1500] + "..." if len(obs) > 1500 else obs
        # code 截断到 800 字符（够看清变量定义和逻辑即可）
        code_short = code[:800] + "..." if len(code) > 800 else code
        lines.append(
            f"  {i}. [{step.get('tool')}] {step.get('intent')}"
            f"（result_name={step.get('result_name') or '无'}）"
        )
        if code_short:
            lines.append(f"     执行代码：{code_short}")
        lines.append(f"     观察结果：{obs_short}")
    return "\n".join(lines)


def executor_node(state: PlanExecuteState) -> dict:
    """执行当前步骤：调用对应工具一次。"""
    idx = state["current_step_idx"]
    plan = state["plan"]
    if idx >= len(plan):
        return {}  # 越界，交给 replanner 处理

    step = plan[idx]
    # 取 system_prompt 中的工具规则段（复用现有规则，避免重复维护）
    from core.agent import build_system_prompt  # 局部 import 避免循环
    # 用一个临时 df 生成 prompt 取工具规则段（这里只取规则部分，不依赖具体 df）
    # 实际工具规则已在 _EXECUTOR_SYSTEM 中引用 {tool_rules} 占位
    tool_rules = _extract_tool_rules()

    system_prompt = _EXECUTOR_SYSTEM.format(tool_rules=tool_rules)
    cols = state.get("columns") or []
    user_prompt = _EXECUTOR_USER.format(
        tool=step.get("tool", ""),
        intent=step.get("intent", ""),
        result_name=step.get("result_name", ""),
        columns=", ".join(cols) if cols else "（未知，需先调 schema_tool 确认）",
        past_steps_summary=_build_past_steps_summary(state.get("past_steps", [])),
    )

    llm = create_llm().bind_tools(all_tools)
    ai_msg: AIMessage = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    messages_to_add: list = [ai_msg]
    observations: list[str] = []
    # 记录本步实际执行的代码/参数，供后续步骤参考
    executed_code: str = ""

    # 执行工具调用
    if ai_msg.tool_calls:
        for tc in ai_msg.tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {}) or {}
            tool = _TOOL_BY_NAME.get(tool_name)
            if tool is None:
                obs = f"❌ 未知工具：{tool_name}"
            elif tool_name == "analyse_tool":
                # ★ analyse_tool 执行前静态预检：拦截未定义变量
                code_str = tool_args.get("code", "") or ""
                executed_code = code_str
                precheck_err = _precheck_analyse_code(code_str)
                if precheck_err:
                    # 预检失败：不执行，直接返回错误让 replanner 触发重写
                    obs = precheck_err
                else:
                    try:
                        obs = tool.invoke(tool_args)
                    except Exception as e:
                        obs = f"❌ 工具执行出错：{e}"
            else:
                try:
                    obs = tool.invoke(tool_args)
                except Exception as e:
                    obs = f"❌ 工具执行出错：{e}"
            observations.append(str(obs))
            messages_to_add.append(ToolMessage(
                content=str(obs),
                tool_call_id=tc.get("id", ""),
                name=tool_name,
            ))
    else:
        # LLM 未调用工具，把文本作为观察
        observations.append(ai_msg.content or "（执行器未调用工具）")

    # 记录 past_steps（含实际执行的代码，供后续步骤参考）
    past_steps = list(state.get("past_steps", []))
    combined_obs = "\n".join(observations)
    past_steps.append({
        "step": step,
        "observation": combined_obs,
        "code": executed_code,
    })

    # 连续失败计数：有错误关键词 → +1；成功 → 归零
    has_error = any(kw in combined_obs for kw in _ERROR_KEYWORDS)
    prev_fail = int(state.get("consecutive_failures") or 0)
    new_fail = prev_fail + 1 if has_error else 0

    return {
        "messages": messages_to_add,
        "past_steps": past_steps,
        "current_step_idx": idx + 1,
        "consecutive_failures": new_fail,
    }


# 工具规则段（从 build_system_prompt 中提取，避免重复维护）
_TOOL_RULES_CACHE: str | None = None


def _extract_tool_rules() -> str:
    """提取工具调用规则段，供 executor prompt 使用。

    截取范围：工具调用规则 → data_expr 代码规范（含两段之间的标准工作流和输出格式规范）。
    必须包含 data_expr 代码规范段，否则 Executor 不知道"禁止 import"、"只能用 df/pd/np"，
    会生成 import 语句导致安全检查拦截。
    """
    global _TOOL_RULES_CACHE
    if _TOOL_RULES_CACHE is not None:
        return _TOOL_RULES_CACHE
    # 直接复用 build_system_prompt 的内容（用一个空 df 占位）
    import pandas as pd
    placeholder = pd.DataFrame()
    full_prompt = build_system_prompt(placeholder, "占位")
    start = full_prompt.find("━━━━ 工具调用规则 ━━━━")
    # 截到 prompt 末尾（data_expr 代码规范是最后一段，直到字符串结尾）
    if start >= 0:
        _TOOL_RULES_CACHE = full_prompt[start:].strip()
    else:
        _TOOL_RULES_CACHE = "（详见系统提示）"
    return _TOOL_RULES_CACHE


# ── analyse_tool 代码执行前静态预检（拦截未定义变量等低级错误） ──────────────

# analyse_tool sandbox 的全局命名空间白名单（来自 tools/analyse_tool.py _build_sandbox）
_ANALYSE_SANDBOX_GLOBALS = {
    # 内置函数白名单
    "print", "len", "range", "enumerate", "zip", "map", "filter",
    "list", "dict", "set", "tuple", "int", "float", "str", "bool",
    "round", "abs", "min", "max", "sum", "sorted", "reversed",
    "isinstance", "repr", "type", "hasattr",
    # 异常类（用户 try/except 需要）
    "Exception", "ValueError", "KeyError", "TypeError",
    "IndexError", "AttributeError", "ZeroDivisionError", "StopIteration",
    # 全局变量
    "df", "dfs", "pd", "np", "datetime", "re", "json", "reload_df",
    # local_vars 中预定义的（analyse_tool 会把 result 注入本地作用域）
    "result",
}


def _precheck_analyse_code(code: str) -> str | None:
    """对 analyse_tool 的 Python 代码做执行前静态检查。

    当前检查项：
      1) 语法是否合法（ast.parse）
      2) 未定义变量引用（Name Load 操作，但名字既不在 sandbox 白名单中，
         也不在代码自身通过赋值/for/comprehension/函数参数等定义的名字里）

    返回 None 表示通过；返回字符串表示错误原因（含可疑变量名列表）。
    """
    import ast
    # 1) 语法检查
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return f"❌ 代码语法错误（行 {e.lineno}）：{e.msg}。请修正语法后再执行。"

    # 2) 收集代码中自己定义的名字（Store 上下文）
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            targets = getattr(node, "targets", None)
            if targets is not None:
                # Assign: targets 是列表（支持解包赋值 a,b = ...）
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        defined.add(tgt.id)
                    elif isinstance(tgt, (ast.Tuple, ast.List)):
                        for elt in tgt.elts:
                            if isinstance(elt, ast.Name):
                                defined.add(elt.id)
            else:
                # AugAssign / AnnAssign: target 是单个节点
                tgt = getattr(node, "target", None)
                if isinstance(tgt, ast.Name):
                    defined.add(tgt.id)
                elif isinstance(tgt, (ast.Tuple, ast.List)):
                    for elt in tgt.elts:
                        if isinstance(elt, ast.Name):
                            defined.add(elt.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            tgt = node.target
            if isinstance(tgt, ast.Name):
                defined.add(tgt.id)
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                for elt in tgt.elts:
                    if isinstance(elt, ast.Name):
                        defined.add(elt.id)
        elif isinstance(node, ast.comprehension):
            tgt = node.target
            if isinstance(tgt, ast.Name):
                defined.add(tgt.id)
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                for elt in tgt.elts:
                    if isinstance(elt, ast.Name):
                        defined.add(elt.id)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    defined.add(item.optional_vars.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                defined.add(arg.arg)
            if node.args.vararg:
                defined.add(node.args.vararg.arg)
            if node.args.kwarg:
                defined.add(node.args.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                defined.add(node.name)

    # 3) 收集代码中引用（Load）的名字，排除白名单和自己定义的
    undefined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if (name not in _ANALYSE_SANDBOX_GLOBALS
                    and name not in defined
                    and name not in {"True", "False", "None"}):
                undefined.add(name)

    if undefined:
        hint = ", ".join(sorted(undefined))
        return (f"❌ 代码预检失败：发现未在代码中声明的变量：【{hint}】。\n"
                f"你不能直接使用 match_map / risk_order 等名字——要么先用字典/列表"
                f"显式定义它们（如 match_map = {{'保守型': '低风险', ...}}），"
                f"要么改用更直接的写法（例如直接用 df['列名'] 判断）。"
                f"请修正后再执行。")
    return None


# ── Replanner ─────────────────────────────────────────────────────────────────

_REPLANNER_SYSTEM = """你是计划评审器。在每一步执行后，判断后续应该如何进行。

【决策动作】
- continue：当前计划仍有效，继续执行下一步。
- replan：遇到了意外情况（schema 不符、数据质量问题、中间结果异常等），
          需要重新规划剩余未执行的步骤。此时必须给出 new_steps 替换剩余步骤。
- finish：已收集到足够信息，可以生成最终回复了。

【判断依据】
1. 如果当前步骤的 observation 包含错误（❌）或返回了意外结果（如列名是乱码、
   数据为空、行数异常），应 replan。
2. 如果 schema_tool 揭示了实际列名与计划假设不符，应 replan。
3. 如果所有计划步骤都已执行完，应 finish。
4. 如果当前步骤成功且后续步骤仍合理，应 continue。
5. 不要因为"想多分析一些"而 replan，严格按用户原始问题判断是否足够。

【0 行软失败的处理】
如果 analyse_tool 返回"共 0 行"（但代码无错误），说明计划假设的筛选值与实际数据
不匹配（如用户输入"杨家坪支行"但实际值是"重庆杨家坪支行"）。此时应 replan：
- 第一步用 analyse_tool 查询该列的唯一值（如 df['网点'].unique()），
  observation 中会暴露实际值。
- 第二步用正确的实际值重新筛选统计。
不要直接 finish 并告诉用户"无数据"，那不是用户想要的。

【new_steps 要求】（仅 action=replan 时）
- 只包含剩余未执行的步骤，不要重复已完成的步骤。
- 遵循与 planner 相同的工具规则（result_name 复用、schema_tool 只调一次等）。
- 如果是空列表但 action=replan，会被视为 finish。

以 JSON 对象格式输出决策结果，字段结构如下：
{
  "action": "continue 或 replan 或 finish",
  "reason": "决策理由（一句话）",
  "new_steps": [
    {
      "tool": "工具名（schema_tool/analyse_tool/chart_tool/excel_tool）",
      "intent": "这一步要做什么",
      "result_name": "仅 analyse_tool 必填，其他工具填空字符串",
      "reason": "为什么需要这一步"
    }
  ]
}
注意：new_steps 中每个元素必须包含 tool / intent / result_name / reason 四个字段，缺一不可。
action=continue 或 finish 时 new_steps 填空数组 []。"""

_REPLANNER_USER = """用户原始问题：{query}

完整计划：
{full_plan}

已完成步骤：
{past_steps_summary}

剩余未执行步骤：
{remaining_steps_summary}

请决定下一步动作。"""


def _format_plan(plan: list[dict]) -> str:
    lines = []
    for i, s in enumerate(plan, 1):
        lines.append(
            f"  {i}. [{s.get('tool')}] {s.get('intent')}"
            f"（result_name={s.get('result_name') or '无'}）"
        )
    return "\n".join(lines)


def replanner_node(state: PlanExecuteState) -> dict:
    """评审并决定后续动作。"""
    query = ""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            query = m.content
            break

    plan = state["plan"]
    idx = state["current_step_idx"]
    past_steps = state.get("past_steps", [])

    # 剩余步骤为空 → 检查最后一步是否失败
    # 如果最后一步是软失败（0 行）或硬失败（错误），仍需让 LLM 评估是否 replan，
    # 不能直接 finish（否则单步计划 0 行时无法自纠）
    if idx >= len(plan):
        if past_steps:
            last_obs = str(past_steps[-1].get("observation", ""))
            has_hard_fail = any(kw in last_obs for kw in _ERROR_KEYWORDS)
            has_soft_fail = "TABLE:" in last_obs and any(
                kw in last_obs for kw in _EMPTY_RESULT_KEYWORDS
            )
            if has_hard_fail or has_soft_fail:
                # 落到下方 LLM 评估，让 replanner 决定是否 replan
                pass
            else:
                return {"response": "__finish__"}
        else:
            return {"response": "__finish__"}

    llm = create_llm()
    prompt = _REPLANNER_USER.format(
        query=query,
        full_plan=_format_plan(plan),
        past_steps_summary=_build_past_steps_summary(past_steps),
        remaining_steps_summary=_format_plan(plan[idx:]),
    )
    resp = llm.invoke([
        SystemMessage(content=_REPLANNER_SYSTEM),
        HumanMessage(content=prompt),
    ])
    decision = _parse_json_response(resp.content, ReplanDecision)

    if decision.action == "replan":
        if not decision.new_steps:
            # new_steps 为空视为 finish
            return {"response": "__finish__"}
        # 替换剩余步骤，重置 idx 指向新步骤开头
        new_plan = list(plan[:idx]) + [s.model_dump() for s in decision.new_steps]
        return {"plan": new_plan}
    elif decision.action == "finish":
        return {"response": "__finish__"}
    else:
        # continue：不动 plan，executor 自然取下一步
        return {}


# ── Synthesizer（最终回复生成）─────────────────────────────────────────────────

_SYNTHESIZER_SYSTEM = """你是数据分析总结器。根据用户问题和已完成的步骤结果，生成最终的自然语言回复。

【要求】
1. 用中文，3~6 句话总结关键结论。
2. 必须引用观察结果中的具体数字（如金额、笔数、占比、排名等），不要只说"已完成分析"。
3. 如果有对比关系（如不同渠道、不同客户类型），要点出差异和排名。
4. 如果工具已产出图表/Excel，简要提及"已生成图表/已导出报告"即可。
5. 不要暴露代码或工具调用细节。
6. 不要编造未在观察结果中出现的数据。
7. 如果观察结果中提示"已截断显示"，说明你看到的是部分数据，请在回复末尾补充"以上为部分数据，完整结果已缓存，可导出查看全部"。

【重要：保留 TABLE 标记】
如果观察结果中包含以 "TABLE:" 开头的表格数据（markdown 表格），你必须在回复末尾
**原样输出**这些 TABLE 标记和表格内容。格式：先写你的文字总结，然后空一行，
再输出 "TABLE:" 和表格的 markdown 内容。不要省略表格，不要只描述表格内容。
这些标记会被系统自动解析并渲染为前端表格，用户需要看到数据表格。

【示例】
好的回复：VIP客户通过手机银行的平均交易金额为 12.5 万元，是普通客户的 3.2 倍。柜台渠道交易笔数最多（1,234 笔），但单笔金额最低。已生成交叉分析结果。

TABLE:
| 客户类型 | 渠道 | 平均金额 | 笔数 |
|---|---|---|---|
| VIP | 手机银行 | 125,000 | 320 |
| 普通 | 柜台 | 8,200 | 1,234 |
（共 2 行 × 4 列）

差的回复：本次分析已按客户类型与交易渠道完成交叉分组，具体数值结论需结合实际数据表进行解读。"""

_SYNTHESIZER_USER = """用户问题：{query}

已完成的步骤及观察结果：
{past_steps_summary}

请生成最终回复。"""


def synthesizer_node(state: PlanExecuteState) -> dict:
    """生成最终用户回复。"""
    query = ""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            query = m.content
            break

    llm = create_llm()
    prompt = _SYNTHESIZER_USER.format(
        query=query,
        past_steps_summary=_build_past_steps_summary(state.get("past_steps", [])),
    )
    resp = llm.invoke([
        SystemMessage(content=_SYNTHESIZER_SYSTEM),
        HumanMessage(content=prompt),
    ])
    return {"response": resp.content, "messages": [resp]}


# ── 条件路由 ──────────────────────────────────────────────────────────────────

# 错误关键词：observation 中出现这些词时触发 replanner 评估
_ERROR_KEYWORDS = ("error", "Error", "KeyError", "Traceback", "失败", "出错", "异常")
# 软失败关键词：结果为空 / 0 行（说明计划假设有误，如筛选条件不匹配实际字段值）
# 只有 analyse_tool 输出含 "TABLE:" + "共 0 行" 时才算，避免误判 chart/excel 输出
_EMPTY_RESULT_KEYWORDS = ("共 0 行",)


def _route_after_planner(state: PlanExecuteState) -> str:
    """planner 后：plan 为空 → END，否则 → executor。"""
    if not state.get("plan"):
        return END
    return "executor"


def _route_after_executor(state: PlanExecuteState) -> str:
    """executor 后的智能路由（减少不必要的 replanner LLM 调用）：
    - 上一步 observation 有错误关键词 → replanner（可能需要 replan）
    - 上一步 analyse_tool 返回 0 行（软失败）→ replanner（需重新探查字段值）
    - 所有步骤执行完且无失败 → synthesizer（跳过 replanner）
    - 正常情况 → 继续执行下一步（跳过 replanner）

    注意：失败检测必须在"所有步骤完成"判断之前，否则单步计划返回 0 行时
    会直接走 synthesizer，replanner 没机会自纠。
    """
    plan = state.get("plan", [])
    idx = state.get("current_step_idx", 0)

    # 先检查最近一步是否有异常（即便所有步骤已执行完，失败也要 replan）
    past_steps = state.get("past_steps", [])
    if past_steps:
        last_obs = str(past_steps[-1].get("observation", ""))
        # 硬失败：明确的错误关键词
        if any(kw in last_obs for kw in _ERROR_KEYWORDS):
            return "replanner"
        # 软失败：analyse_tool 返回 0 行（说明计划假设的字段值/筛选条件不匹配实际数据，
        # 如用户输入"杨家坪支行"但实际是"重庆杨家坪支行"，需 replan 先探查实际值）
        if "TABLE:" in last_obs and any(kw in last_obs for kw in _EMPTY_RESULT_KEYWORDS):
            return "replanner"

    # 所有步骤执行完且无失败，直接生成回复
    if idx >= len(plan):
        return "synthesizer"

    # 正常情况：跳过 replanner，直接执行下一步
    return "executor"


def _route_after_replanner(state: PlanExecuteState) -> str:
    """replanner 后：response=__finish__ → synthesizer，否则 → executor/planner。
    replanner 修改了 plan（replan 情况）→ 回 executor 继续执行新步骤。
    replanner 未改 plan（continue 情况）→ 回 executor 执行下一步。
    """
    if state.get("response") == "__finish__":
        return "synthesizer"
    return "executor"


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_plan_execute_graph():
    """构建并编译 Plan-Execute-Replan 图。"""
    graph = StateGraph(PlanExecuteState)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("replanner", replanner_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", _route_after_planner, {
        "executor": "executor",
        END: END,
    })
    # executor 后智能路由：正常→继续 executor，出错→replanner，完成→synthesizer
    graph.add_conditional_edges("executor", _route_after_executor, {
        "executor": "executor",
        "replanner": "replanner",
        "synthesizer": "synthesizer",
    })
    graph.add_conditional_edges("replanner", _route_after_replanner, {
        "executor": "executor",
        "synthesizer": "synthesizer",
    })
    graph.add_edge("synthesizer", END)

    # recursion_limit 提高：每步约消耗 4 个节点调用（executor + tools + replanner + 一次循环）
    # 6 步计划约需 24 次，加上 planner 和 synthesizer，设为 50 留余量。
    return graph.compile()
