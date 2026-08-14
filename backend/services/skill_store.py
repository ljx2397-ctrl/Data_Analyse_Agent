# backend/services/skill_store.py
# Skill 配置持久化层。
# - 内置 skill（BUILTIN_SKILLS）与磁盘 user_overrides 合并：
#     内置条目保留 builtin=True，可被覆盖 enabled/keywords/name/description，不可删除
#     自定义条目 builtin=False，可任意增删改
# - 持久化文件：storage/meta/skills.json
# - 提供 list/get/upsert/delete/toggle/reset/seed 等接口供 api/skills.py 调用
import datetime
import json
import os
import threading
import uuid

from settings import META_DIR
from core.skills import BUILTIN_SKILLS, SkillSpec


_DATA_FILE = os.path.join(META_DIR, "skills.json")

# 内存索引：id -> SkillSpec
_items: dict[str, SkillSpec] = {}
_loaded = False
_lock = threading.RLock()


def _load() -> None:
    """从磁盘加载（仅一次）。内置 skill 始终作为基线，磁盘只存覆盖项 + 自定义项。"""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        # 1. 内置基线
        for s in BUILTIN_SKILLS:
            _items[s.id] = SkillSpec(
                id=s.id, name=s.name, description=s.description,
                keywords=list(s.keywords), runner=s.runner,
                enabled=s.enabled, builtin=True,
            )
        # 2. 合并磁盘覆盖（内置覆盖 + 自定义新增）
        try:
            if os.path.exists(_DATA_FILE):
                with open(_DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rec in data.get("items", []):
                    sid = rec.get("id")
                    if not sid:
                        continue
                    spec = SkillSpec(
                        id=sid,
                        name=rec.get("name", ""),
                        description=rec.get("description", ""),
                        keywords=list(rec.get("keywords") or []),
                        runner=rec.get("runner", ""),
                        enabled=bool(rec.get("enabled", True)),
                        builtin=bool(rec.get("builtin", False)),
                    )
                    _items[sid] = spec
        except Exception:
            pass
        _save_silent()
        _loaded = True


def _save_silent() -> None:
    """持久化内存索引（不加锁，调用方持锁）。"""
    try:
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"items": [_to_dict(s) for s in _items.values()]},
                f, ensure_ascii=False, indent=2,
            )
    except Exception:
        pass


def _save() -> None:
    with _lock:
        _save_silent()


def _to_dict(s: SkillSpec) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "keywords": list(s.keywords),
        "runner": s.runner,
        "enabled": s.enabled,
        "builtin": s.builtin,
    }


def _now_id() -> str:
    return "sk_" + uuid.uuid4().hex[:10]


# ── 公共 API ──────────────────────────────────────────────────────────────────

def list_all() -> list[SkillSpec]:
    """返回全部 skill（内置 + 自定义），按内置优先、再按 id 字母序。"""
    with _lock:
        _load()
        items = list(_items.values())
    items.sort(key=lambda s: (not s.builtin, s.id))
    return items


def get(sid: str) -> SkillSpec | None:
    with _lock:
        _load()
        return _items.get(sid)


def upsert(
    sid: str | None,
    name: str,
    description: str,
    keywords: list[str],
    enabled: bool = True,
    runner: str = "",
) -> SkillSpec:
    """新增或更新 skill。
    - sid 为空 → 新增自定义 skill（builtin=False，runner 通常为空）
    - sid 已存在 → 更新；内置 skill 不允许改 runner/builtin
    """
    with _lock:
        _load()
        if sid and sid in _items:
            spec = _items[sid]
            spec.name = name or spec.name
            spec.description = description
            # keywords 至少要保留一个，避免关键词被清空后永远无法命中
            spec.keywords = [k.strip() for k in keywords if k and k.strip()]
            spec.enabled = enabled
            if not spec.builtin:
                # 自定义 skill 允许改 runner（虽然目前没有内置 runner 可选）
                spec.runner = runner
            _save_silent()
            return spec
        # 新增
        new_id = sid or _now_id()
        spec = SkillSpec(
            id=new_id,
            name=name or "未命名 Skill",
            description=description or "",
            keywords=[k.strip() for k in keywords if k and k.strip()],
            runner=runner,
            enabled=enabled,
            builtin=False,
        )
        _items[new_id] = spec
        _save_silent()
        return spec


def delete(sid: str) -> bool:
    """删除 skill。内置 skill 不可删除（返回 False）。"""
    with _lock:
        _load()
        spec = _items.get(sid)
        if spec is None or spec.builtin:
            return False
        del _items[sid]
        _save_silent()
        return True


def set_enabled(sid: str, enabled: bool) -> SkillSpec | None:
    """启停一个 skill。"""
    with _lock:
        _load()
        spec = _items.get(sid)
        if spec is None:
            return None
        spec.enabled = enabled
        _save_silent()
        return spec


def reset_builtin(sid: str) -> SkillSpec | None:
    """重置内置 skill 为默认配置（恢复关键词/名称/描述/启用状态）。"""
    with _lock:
        _load()
        spec = _items.get(sid)
        if spec is None or not spec.builtin:
            return None
        default = next((b for b in BUILTIN_SKILLS if b.id == sid), None)
        if default is None:
            return None
        spec.name = default.name
        spec.description = default.description
        spec.keywords = list(default.keywords)
        spec.runner = default.runner
        spec.enabled = default.enabled
        _save_silent()
        return spec


def enabled_specs() -> list[SkillSpec]:
    """返回全部已启用的 skill（供 agent_service 调度使用）。"""
    return [s for s in list_all() if s.enabled]
