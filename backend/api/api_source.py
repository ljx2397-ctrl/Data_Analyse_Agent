# backend/api/api_source.py
# API 数据源路由（精简版）：配置 CRUD + 预览 + 拉取到会话。
from fastapi import APIRouter, HTTPException, Query

from schemas import (
    ApiSourceProfile, ApiSourceSaveRequest, ApiPreviewResult, ApiFetchResult,
)
from services import api_source, session_store

router = APIRouter(prefix="/api-source", tags=["api-source"])


@router.get("/profiles", response_model=list[ApiSourceProfile])
def list_profiles():
    return [ApiSourceProfile(**p) for p in api_source.list_profiles()]


@router.post("/profiles", response_model=ApiSourceProfile)
def save_profile(req: ApiSourceSaveRequest):
    try:
        p = api_source.save_profile(
            profile_id=req.id,
            name=req.name,
            url=req.url,
            auth_type=req.auth_type,
            auth_token=req.auth_token,
            activate=req.activate,
        )
        return ApiSourceProfile(**p)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/profiles/{profile_id}/activate", response_model=ApiSourceProfile)
def activate_profile(profile_id: str):
    try:
        p = api_source.activate_profile(profile_id)
        return ApiSourceProfile(**p)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    try:
        return api_source.delete_profile(profile_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/profiles/{profile_id}/preview", response_model=ApiPreviewResult)
def preview(profile_id: str):
    """预览：请求 URL，返回字段/行数/前 5 行 Markdown，不写入会话。"""
    try:
        result = api_source.preview(profile_id)
        return ApiPreviewResult(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/profiles/{profile_id}/fetch", response_model=ApiFetchResult)
def fetch_to_session(profile_id: str, sid: str = Query(...)):
    """拉取并注册到指定会话（作为新数据集加入多表）。"""
    if not session_store.exists(sid):
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")
    try:
        result = api_source.fetch_to_session(profile_id, sid)
        return ApiFetchResult(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
