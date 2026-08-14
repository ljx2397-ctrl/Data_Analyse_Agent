# backend/core/skills/__init__.py
# Skill 层入口：
# - 导入各 *_skill 模块触发运行器注册
# - 提供 BUILTIN_SKILLS 默认配置（skill_store 启动时与磁盘合并）
# - 提供 dispatch_skill：按 skill_id 或关键词命中选定并执行 skill
from __future__ import annotations

from .base import (
    SkillSpec, SkillContext, SkillResult, Runner, RUNNERS,
    register_runner, get_runner, match_skill,
)
# 导入即注册 quality_check 运行器
from . import quality_check_skill  # noqa: F401


# 内置 Skill 默认配置。skill_store 启动时若磁盘没有则写入，
# 已存在则保留用户的 enabled/keywords 覆盖（仅可改字段，不可删 builtin）。
BUILTIN_SKILLS: list[SkillSpec] = [
    SkillSpec(
        id="quality_check",
        name="数据质量检测",
        description="确定性检测重复值/空值/金额异常/日期合法性，并导出 Excel 报告（不走 LLM）",
        keywords=[
            "数据质量", "质检", "数据清洗", "清洗", "去重",
            "重复值", "重复行", "空值", "缺失", "金额异常", "异常值", "离群",
        ],
        runner="quality_check",
        enabled=True,
        builtin=True,
    ),
]


def dispatch_skill(
    query: str,
    ctx: SkillContext,
    skill_id: str | None = None,
    skills: list[SkillSpec] | None = None,
) -> tuple[SkillSpec, SkillResult] | None:
    """按 skill_id 显式选择，否则按关键词命中第一条启用的 skill 执行。

    返回 (spec, result) 或 None（未命中）。
    skills 为空时只考虑内置默认表（不应发生，正常流程由 agent_service 传入完整列表）。
    """
    pool = skills if skills is not None else BUILTIN_SKILLS

    target: SkillSpec | None = None
    if skill_id:
        target = next((s for s in pool if s.id == skill_id and s.enabled), None)
    else:
        # 关键词命中：按列表顺序取第一条
        for s in pool:
            if match_skill(s, query):
                target = s
                break

    if target is None:
        return None

    runner = get_runner(target.runner)
    if runner is None:
        return None

    # 自定义 skill（runner 为空）不在此处执行，交给 LLM 分支
    if not target.runner:
        return None

    result = runner(ctx)
    return target, result


__all__ = [
    "SkillSpec", "SkillContext", "SkillResult", "Runner", "RUNNERS",
    "register_runner", "get_runner", "match_skill",
    "BUILTIN_SKILLS", "dispatch_skill",
]
