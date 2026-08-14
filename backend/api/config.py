# backend/api/config.py
# LLM 运行时配置路由。
# - GET/PUT /config/llm        读取/更新当前生效配置（向后兼容）
# - GET    /config/llm/profiles  列出所有已保存档案
# - POST   /config/llm/profiles  新增/更新档案
# - POST   /config/llm/profiles/{id}/activate  切换激活
# - DELETE /config/llm/profiles/{id}  删除档案
from fastapi import APIRouter, HTTPException

from schemas import (
    LLMConfigOut, LLMConfigUpdate,
    LLMProfile, LLMProfileSaveRequest,
)
from services import llm_config

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/llm", response_model=LLMConfigOut)
def get_llm_config():
    """获取当前生效的 LLM 配置（api_key 脱敏）。"""
    cfg = llm_config.get_safe_config()
    return LLMConfigOut(
        model=cfg["model"],
        api_base=cfg["api_base"],
        api_key_masked=cfg.get("api_key_masked", ""),
        temperature=cfg.get("temperature", 0),
    )


@router.put("/llm", response_model=LLMConfigOut)
def update_llm_config(req: LLMConfigUpdate):
    """直接更新当前生效配置并热生效。"""
    cfg = llm_config.set_config(
        model=req.model,
        api_base=req.api_base,
        api_key=req.api_key,
        temperature=req.temperature,
    )
    safe = llm_config.get_safe_config()
    return LLMConfigOut(
        model=safe["model"],
        api_base=safe["api_base"],
        api_key_masked=safe.get("api_key_masked", ""),
        temperature=safe.get("temperature", 0),
    )


# ── Profile CRUD ────────────────────────────────────────────────────────────

@router.get("/llm/profiles", response_model=list[LLMProfile])
def list_profiles():
    """列出所有已保存的配置档案。"""
    return [LLMProfile(**p) for p in llm_config.list_profiles()]


@router.post("/llm/profiles", response_model=LLMProfile)
def save_profile(req: LLMProfileSaveRequest):
    """新增或更新配置档案。activate=True 时设为当前激活。"""
    try:
        p = llm_config.save_profile(
            profile_id=req.id,
            name=req.name,
            model=req.model,
            api_base=req.api_base,
            api_key=req.api_key,
            temperature=req.temperature,
            activate=req.activate,
        )
        return LLMProfile(**p)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/llm/profiles/{profile_id}/activate", response_model=LLMProfile)
def activate_profile(profile_id: str):
    """切换激活的配置档案。"""
    try:
        p = llm_config.activate_profile(profile_id)
        return LLMProfile(**p)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/llm/profiles/{profile_id}")
def delete_profile(profile_id: str):
    """删除配置档案。若删除的是激活档案，自动切换到第一个。"""
    try:
        return llm_config.delete_profile(profile_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
