# backend/main.py
# FastAPI 入口：组装应用、CORS、路由挂载、健康检查。
# 启动：uvicorn main:app --host 0.0.0.0 --port 8000 --reload
import os
import sys

# 把 backend 目录加入 sys.path，使 `from core.xxx` / `from services.xxx` 可用
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from settings import CORS_ORIGINS, PROJECT_DIR
from api import sessions as sessions_api
from api import upload as upload_api
from api import chat as chat_api
from api import code as code_api
from api import files as files_api
from api import knowledge as knowledge_api
from api import config as config_api
from api import api_source as api_source_api
from api import skills as skills_api
from api import stats as stats_api

app = FastAPI(title="数据分析 Agent API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 路由统一挂在 /api 下
app.include_router(sessions_api.router, prefix="/api")
app.include_router(upload_api.router, prefix="/api")
app.include_router(chat_api.router, prefix="/api")
app.include_router(code_api.router, prefix="/api")
app.include_router(files_api.router, prefix="/api")
app.include_router(knowledge_api.router, prefix="/api")
app.include_router(config_api.router, prefix="/api")
app.include_router(api_source_api.router, prefix="/api")
app.include_router(skills_api.router, prefix="/api")
app.include_router(stats_api.router, prefix="/api")


@app.on_event("startup")
def _startup():
    # 触发 settings 目录创建 + Redis 连接初始化（fakeredis 兜底）
    from services import session_store
    session_store._get_client()


# ── 静态托管前端 dist（打包/单端口模式）──────────────────────────────────
# 开发模式仍用 vite dev server（5173，HMR）；此处让 :8000 也能直接访问
# 已构建的前端页面，供 PyInstaller exe 单端口运行使用。
# /api/* 路由已先注册，不会被 mount("/", ...) 拦截。
from pathlib import Path
from fastapi.staticfiles import StaticFiles


def _resolve_frontend_dist() -> Path | None:
    """定位 frontend/dist：优先打包内 _MEIPASS，其次源码 PROJECT_DIR。"""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "frontend" / "dist")
    candidates.append(Path(PROJECT_DIR) / "frontend" / "dist")
    for p in candidates:
        if (p / "index.html").is_file():
            return p
    return None


_dist = _resolve_frontend_dist()
if _dist is not None:
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
