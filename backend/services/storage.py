# backend/services/storage.py
# 对象存储抽象层。
# 默认 LocalStorageBackend：写到本地 storage/ 目录，零外部依赖。
# 预留 MinioStorageBackend 接口，后续接 MinIO/S3 时实现即可。
# 同时提供 abspath_to_download_url：把工具产生的绝对路径转成前端可下载的 URL。
import os
import shutil

from settings import STORAGE_DIR, CHARTS_DIR, OUTPUT_DIR, DATA_DIR


class StorageBackend:
    """对象存储抽象接口。key 使用 '/' 分隔的相对路径，如 'sessions/sid/data.bin'。"""

    def put(self, key: str, data: bytes) -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    """本地文件系统存储后端。"""

    def __init__(self, root: str = STORAGE_DIR):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _full(self, key: str) -> str:
        # 防止路径穿越：去掉 key 开头的 /，并禁止 .. 
        key = key.lstrip("/").replace("\\", "/")
        parts = [p for p in key.split("/") if p not in ("", ".", "..")]
        return os.path.join(self.root, *parts)

    def put(self, key: str, data: bytes) -> str:
        path = self._full(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def get(self, key: str) -> bytes:
        with open(self._full(key), "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        return os.path.exists(self._full(key))

    def delete(self, key: str) -> None:
        try:
            os.remove(self._full(key))
        except FileNotFoundError:
            pass

    def abspath(self, key: str) -> str:
        return self._full(key)


# ── 工具路径 → 下载 URL 映射 ──────────────────────────────────────────────────
# chart_tool / excel_tool / quality_check 产生的文件落在 CHARTS_DIR / OUTPUT_DIR。
# 这些目录都在 STORAGE_DIR 下。把绝对路径映射为 /api/files/<kind>/<filename>。

def _rel_to_storage(abspath: str) -> str | None:
    """返回 abspath 相对 STORAGE_DIR 的相对路径；不在 STORAGE_DIR 下返回 None。"""
    try:
        rel = os.path.relpath(abspath, STORAGE_DIR)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel.replace("\\", "/")


def abspath_to_download_url(abspath: str) -> str:
    """
    把工具返回的绝对文件路径转成前端可下载的 URL。
    charts/xxx.png       → /api/files/charts/xxx.png
    exports/xxx.xlsx     → /api/files/exports/xxx.xlsx
    sessions/sid/data.bin→ /api/files/sessions/sid/data.bin
    无法识别时回退为原始路径。
    """
    rel = _rel_to_storage(abspath)
    if rel is None:
        return abspath
    return "/api/files/" + rel


# 默认后端实例（单例）
_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        # STORAGE_BACKEND=settings.STORAGE_BACKEND，目前仅实现 local
        _backend = LocalStorageBackend()
    return _backend
