# backend/api/skills.py
# Skill 管理 CRUD 路由（全局共享，挂在 /api/skills 下）。
# - GET    /skills                列出全部 skill（内置 + 自定义）
# - GET    /skills/{id}           获取单条
# - POST   /skills                新增自定义 / 更新已有（id 非空时为更新）
# - POST   /skills/{id}/toggle    启停切换
# - POST   /skills/{id}/reset     重置内置 skill 为默认配置
# - DELETE /skills/{id}           删除（仅自定义可删）
from fastapi import APIRouter, HTTPException

from schemas import SkillItem, SkillSaveRequest
from services import skill_store

router = APIRouter(prefix="/skills", tags=["skills"])


def _to_item(s) -> SkillItem:
    return SkillItem(
        id=s.id,
        name=s.name,
        description=s.description,
        keywords=list(s.keywords),
        runner=s.runner,
        enabled=s.enabled,
        builtin=s.builtin,
    )


@router.get("", response_model=list[SkillItem])
def list_skills():
    """列出全部 skill（内置在前，自定义在后）。"""
    return [_to_item(s) for s in skill_store.list_all()]


@router.get("/{sid}", response_model=SkillItem)
def get_skill(sid: str):
    s = skill_store.get(sid)
    if s is None:
        raise HTTPException(404, f"Skill 不存在：{sid}")
    return _to_item(s)


@router.post("", response_model=SkillItem)
def save_skill(req: SkillSaveRequest):
    """新增或更新 skill。
    - req.id 为空 → 新增自定义 skill
    - req.id 非空 → 更新已有；内置 skill 不允许改 runner/builtin
    """
    if not req.name.strip():
        raise HTTPException(400, "name 不能为空")
    if not req.keywords:
        raise HTTPException(400, "keywords 不能为空（至少 1 个，否则永远无法命中）")
    try:
        s = skill_store.upsert(
            sid=req.id,
            name=req.name.strip(),
            description=req.description.strip(),
            keywords=req.keywords,
            enabled=req.enabled,
            runner=req.runner.strip(),
        )
        return _to_item(s)
    except Exception as e:
        raise HTTPException(500, f"保存失败：{e}")


@router.post("/{sid}/toggle", response_model=SkillItem)
def toggle_skill(sid: str):
    """切换启用状态。"""
    s = skill_store.get(sid)
    if s is None:
        raise HTTPException(404, f"Skill 不存在：{sid}")
    updated = skill_store.set_enabled(sid, not s.enabled)
    if updated is None:
        raise HTTPException(500, "切换失败")
    return _to_item(updated)


@router.post("/{sid}/reset", response_model=SkillItem)
def reset_skill(sid: str):
    """重置内置 skill 为默认配置。"""
    s = skill_store.get(sid)
    if s is None:
        raise HTTPException(404, f"Skill 不存在：{sid}")
    if not s.builtin:
        raise HTTPException(400, "仅内置 skill 支持重置")
    updated = skill_store.reset_builtin(sid)
    if updated is None:
        raise HTTPException(500, "重置失败")
    return _to_item(updated)


@router.delete("/{sid}")
def delete_skill(sid: str):
    """删除 skill（仅自定义可删）。"""
    s = skill_store.get(sid)
    if s is None:
        raise HTTPException(404, f"Skill 不存在：{sid}")
    if s.builtin:
        raise HTTPException(400, "内置 skill 不可删除，可改为停用或重置")
    ok = skill_store.delete(sid)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True, "id": sid}
