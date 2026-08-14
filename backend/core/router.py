# backend/core/router.py
# 路由层：决定一条 query 走 ReAct 还是 Plan-Execute-Replan。
#
# 策略：规则 fast-path（零开销）+ LLM 结构化分类（不确定时调用）。
#   - 明显简单 → react       （如"多少行"、"前 5 行"）
#   - 明显复杂 → plan         （如"完整分析"、"多表关联"）
#   - 不确定   → LLM router   （输出 mode/complexity/estimated_steps/reason）
#
# 兜底规则：
#   complexity ≤ 4 → react
#   complexity ≥ 7 → plan
#   5~6          → 由 LLM 根据 mode 字段判断
#
# 路由结果会作为 SSE 事件回传前端，便于在轨迹中展示。
import json
import re
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from core.agent import create_llm


# ── 结构化输出模型 ────────────────────────────────────────────────────────────

class RouteDecision(BaseModel):
    """Router 的结构化输出。"""
    mode: Literal["react", "plan"] = Field(
        description="模式选择：react（简单任务）或 plan（复杂任务）"
    )
    complexity: int = Field(
        description="任务复杂度评分 1~10。1=极简单，10=极复杂。"
                    "≤4 走 react，≥7 走 plan，5~6 由 mode 决定。"
    )
    estimated_steps: int = Field(
        description="预计需要的工具调用次数。1~2 次→react，3+次→plan。"
    )
    reason: str = Field(
        description="判断理由（一句话，如'只需一次统计即可完成'）"
    )


# ── 规则 fast-path ────────────────────────────────────────────────────────────

# 明确复杂：多维度交叉 / 多表关联 / 完整报告，直接走 plan，跳过 LLM
_PLAN_MARKERS = (
    "完整分析", "综合分析", "深度分析",
    "交叉分析", "联动分析", "对比分析",
)

# 多表关联信号：JOIN / merge / 关联
_JOIN_MARKERS = ("join", "merge", "关联", "连接", "匹配", "对应到")

# 明确简单：一问一答，直接走 react
_SIMPLE_PATTERNS = (
    r"^\s*(多少行|多少列|行列数|数据规模|共几行|共几列)",
    r"^\s*(前\s*\d+\s*行|后\s*\d+\s*行|看看数据|预览)",
    r"^\s*(字段|列名|表头|schema)\s*[是有什么]",
    r"^\s*(你好|在吗|谢谢|ok|好的|hi|hello)\s*[!！。.]?$",
)


def _rule_route(query: str, has_multi_tables: bool) -> RouteDecision | None:
    """规则判断：返回 RouteDecision 或 None（不确定，交 LLM）。"""
    q = query.strip()
    if not q:
        return RouteDecision(mode="react", complexity=1, estimated_steps=0,
                             reason="空查询")

    ql = q.lower()

    # 1. 简单任务命中 → react
    for pat in _SIMPLE_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            return RouteDecision(mode="react", complexity=1, estimated_steps=1,
                                 reason="简单查询，1 步即可完成")

    # 2. 明确复杂标记 → plan
    for marker in _PLAN_MARKERS:
        if marker in q:
            return RouteDecision(mode="plan", complexity=8, estimated_steps=4,
                                 reason=f"命中复杂标记：{marker}")

    # 3. 多表关联信号 → plan
    for marker in _JOIN_MARKERS:
        if marker in ql:
            return RouteDecision(mode="plan", complexity=7, estimated_steps=3,
                                 reason=f"多表关联：{marker}")

    return None  # 不确定，交 LLM


# ── LLM Router ────────────────────────────────────────────────────────────────

_ROUTER_PROMPT = """你是任务路由器。判断下面这个数据分析问题的复杂度，决定走 ReAct 还是 Plan-Execute-Replan。

【判断维度】
1. 是否包含多个明确子任务
2. 预计需要调用的 Tool 数量
3. 是否涉及多个分析维度或多个数据处理步骤
4. 是否需要多个阶段的中间结果作为后续步骤输入
5. 是否需要生成复杂的综合结果（如完整报告、多个图表、跨维度分析）
6. 执行过程中是否可能需要根据中间结果调整后续计划

【分流规则】
- ReAct：单一目标、步骤少、1~2 次 Tool 调用即可完成。complexity ≤ 4。
  例如：各支行交易金额排名、画一个饼图、异常交易有多少条、分析+导出报告
- Plan-Execute-Replan：多个子任务、预计 3+ 步骤、后续步骤依赖前面结果。complexity ≥ 7。
  例如：交叉分析客户类型与交易类型的关系、完整分析客户风险与交易行为、多表关联后做多维度分析

【复杂度评分标准】
1-2：单步查询（计数、预览、单字段统计）
3-4：单分析+可选导出/画图（1-2 次 Tool 调用）
5-6：中等复杂，2-3 个分析维度但可合并为 1-2 步
7-8：多维度交叉、多表关联、需要 3-4 步
9-10：完整分析报告、多阶段依赖、需要 5+ 步

数据集信息：{data_desc}{multi_table_hint}

用户问题：{query}

请输出 JSON 对象，包含 mode / complexity / estimated_steps / reason 四个字段。
示例：
{{"mode":"react","complexity":2,"estimated_steps":1,"reason":"只需要一次统计即可完成"}}
{{"mode":"plan","complexity":8,"estimated_steps":4,"reason":"涉及多个维度交叉分析，需要多步规划"}}"""


def _parse_router_json(text: str) -> dict | None:
    """从 LLM 文本中解析 JSON 对象（兼容 markdown 代码块和带前后文字）。"""
    # 1. 直接解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # 2. markdown 代码块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text or "")
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 3. 提取 { ... }
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _llm_route(query: str, data_desc: str, has_multi_tables: bool) -> RouteDecision:
    """调用 LLM 结构化路由。失败时降级到 react。"""
    multi_table_hint = ""
    if has_multi_tables:
        multi_table_hint = "\n当前加载了多张数据表，多表关联任务复杂度应提高。"

    prompt = _ROUTER_PROMPT.format(
        data_desc=data_desc,
        multi_table_hint=multi_table_hint,
        query=query,
    )

    try:
        llm = create_llm()
        resp = llm.invoke([HumanMessage(content=prompt)])
        data = _parse_router_json(resp.content or "")
        if data is None:
            return RouteDecision(mode="react", complexity=3, estimated_steps=2,
                                 reason="LLM 输出解析失败，降级 react")
        decision = RouteDecision.model_validate(data)
        # 兜底规则：complexity 覆盖 mode
        if decision.complexity <= 4:
            decision.mode = "react"
        elif decision.complexity >= 7:
            decision.mode = "plan"
        # 5~6 由 LLM 的 mode 决定
        return decision
    except Exception:
        return RouteDecision(mode="react", complexity=3, estimated_steps=2,
                             reason="LLM 路由异常，降级 react")


# ── 对外入口 ──────────────────────────────────────────────────────────────────

def route(query: str, data_desc: str, has_multi_tables: bool = False) -> RouteDecision:
    """决定一条 query 走 ReAct 还是 Plan-Execute-Replan。
    返回 RouteDecision 对象（含 mode/complexity/estimated_steps/reason）。
    永不抛异常（路由失败降级到 react）。
    """
    # 1. 规则 fast-path
    decision = _rule_route(query, has_multi_tables)
    if decision is not None:
        return decision

    # 2. LLM 结构化路由
    return _llm_route(query, data_desc, has_multi_tables)
