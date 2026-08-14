# backend/core/skills/base.py
# Skill 层（Layer 3）：把高频确定性流程从 LLM 手里收回来，关键词命中即直接跑。
# - SkillSpec: 一条 Skill 的可序列化配置（id/name/keywords/runner/enabled/builtin）
# - SkillContext: 运行时上下文（df/query/dfs/data_desc）
# - SkillResult: 统一返回结构（content/charts/xlsxs/tables/code_exec），与 SSE done 对齐
# - Runner: 执行函数协议 run(ctx) -> SkillResult
# 运行器注册表 RUNNERS 由各 *_skill.py 模块填充，__init__.py 负责导入触发注册。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Any

import pandas as pd


@dataclass
class SkillSpec:
    """一条 Skill 的配置（可被 skill_store 持久化为 JSON）。"""
    id: str
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    runner: str = ""          # 运行器标识，对应 RUNNERS 的 key；自定义 skill 留空
    enabled: bool = True
    builtin: bool = False     # 内置 skill 不可删除，但可编辑关键词/启停


@dataclass
class SkillContext:
    """运行时上下文，由 agent_service 注入。"""
    df: pd.DataFrame
    query: str
    dfs: dict[str, pd.DataFrame] | None = None
    data_desc: str = "数据集"
    sid: str = ""


@dataclass
class SkillResult:
    """Skill 执行结果，与 SSE done 事件字段对齐。"""
    content: str = ""
    charts: list[str] = field(default_factory=list)
    xlsxs: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    code_exec: list[str] = field(default_factory=list)
    # Skill 内部已经把绝对路径转成下载 URL；agent_service 直接转发即可
    chart_urls: list[str] = field(default_factory=list)
    xlsx_urls: list[str] = field(default_factory=list)


# 运行器协议：run(ctx) -> SkillResult
Runner = Callable[[SkillContext], SkillResult]

# 运行器注册表：runner 标识 -> 执行函数
RUNNERS: dict[str, Runner] = {}


def register_runner(name: str, fn: Runner) -> None:
    """注册一个运行器到全局表。"""
    RUNNERS[name] = fn


def get_runner(name: str) -> Runner | None:
    return RUNNERS.get(name)


def match_skill(spec: SkillSpec, query: str) -> bool:
    """关键词命中判断：query 包含任一关键词即命中。"""
    if not spec.enabled or not spec.keywords:
        return False
    q = query.lower()
    return any(k and k.lower() in q for k in spec.keywords)
