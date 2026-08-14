# backend/core/skills/quality_check_skill.py
# 内置 Skill：数据质量检测。
# 把原 agent_service 里的 _is_quality_intent 分支收敛到这里，
# 由 skill_store 维护关键词，运行时由 base.get_runner("quality_check") 调度。
from __future__ import annotations

from ._quality_check_impl import (
    run_quality_check, format_quality_report, export_quality_excel,
)
from core.render import parse_output
from services.storage import abspath_to_download_url
from .base import SkillContext, SkillResult, register_runner


def run(ctx: SkillContext) -> SkillResult:
    """确定性数据质量检测：重复值/空值/金额异常/日期合法性 + Excel 报告导出。
    不走 LLM，直接跑检测函数并格式化输出。
    """
    report = run_quality_check(ctx.df)
    report_text = format_quality_report(report, ctx.df, ctx.data_desc)
    clean_text, _, _, tables = parse_output(report_text)

    xlsx_urls: list[str] = []
    try:
        xlsx_path = export_quality_excel(report, ctx.df, ctx.data_desc)
        xlsx_urls.append(abspath_to_download_url(xlsx_path))
    except Exception as e:
        clean_text = f"{clean_text}\n\n⚠️ Excel 分析报告生成失败：{e}"

    return SkillResult(
        content=clean_text,
        tables=tables,
        xlsx_urls=xlsx_urls,
    )


# 模块导入即注册
register_runner("quality_check", run)
