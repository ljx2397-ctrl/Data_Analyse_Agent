# backend/api/files.py
# 文件下载路由：charts / exports / sessions 数据文件。
# 只允许访问 STORAGE_DIR 下的文件，防止路径穿越。
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from settings import STORAGE_DIR

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{file_path:path}")
def download_file(file_path: str):
    """
    下载 storage 目录下的文件。
    路径形如 charts/xxx.png、exports/xxx.xlsx。
    """
    # 安全校验：规范化路径并确保在 STORAGE_DIR 内
    requested = file_path.replace("\\", "/")
    parts = [p for p in requested.split("/") if p not in ("", ".", "..")]
    full = os.path.join(STORAGE_DIR, *parts)

    # 二次确认：realpath 必须在 STORAGE_DIR 下
    try:
        real = os.path.realpath(full)
        storage_real = os.path.realpath(STORAGE_DIR)
        if not real.startswith(storage_real + os.sep) and real != storage_real:
            raise HTTPException(403, "禁止访问该路径")
    except Exception:
        raise HTTPException(403, "禁止访问该路径")

    if not os.path.isfile(full):
        raise HTTPException(404, "文件不存在")

    # 根据扩展名设置 MIME
    ext = os.path.splitext(full)[1].lower()
    mime_map = {
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv":  "text/csv",
    }
    media_type = mime_map.get(ext, "application/octet-stream")

    return FileResponse(full, media_type=media_type, filename=os.path.basename(full))
