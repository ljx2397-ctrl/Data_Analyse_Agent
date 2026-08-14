# backend/services/llm_config.py
# LLM 运行时配置服务：支持保存多个模型配置档案（profile），随时切换激活。
# 持久化到 storage/meta/llm_config.json，结构：
#   { "profiles": [ {id, name, model, api_base, api_key, temperature} ],
#     "active_id": "xxx" }
# 未配置时回退到 settings.py 默认值 + 环境变量 API Key。
import json
import os
import threading
import uuid

from settings import META_DIR, LLM_MODEL, LLM_API_BASE, LLM_API_KEY_ENV, LLM_TEMPERATURE

_CONFIG_PATH = os.path.join(META_DIR, "llm_config.json")
_lock = threading.Lock()


def _default_config() -> dict:
    """默认配置：取 settings.py 的值，api_key 从环境变量读。"""
    return {
        "model": LLM_MODEL,
        "api_base": LLM_API_BASE,
        "api_key": os.environ.get(LLM_API_KEY_ENV, ""),
        "temperature": LLM_TEMPERATURE,
    }


def _read_store() -> dict:
    """读取持久化存储。返回 {profiles, active_id}。"""
    default = {"profiles": [], "active_id": None}
    if not os.path.exists(_CONFIG_PATH):
        return default
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        data.setdefault("profiles", [])
        data.setdefault("active_id", None)
        return data
    except Exception:
        return default


def _write_store(data: dict) -> None:
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _mask_key(key: str) -> str:
    """对 api_key 脱敏。"""
    if not key:
        return ""
    if len(key) > 8:
        return key[:4] + "*" * (len(key) - 8) + key[-4:]
    return "*" * len(key)


def get_config() -> dict:
    """读取当前生效的 LLM 配置（供 agent.py 使用）。
    优先用激活的 profile，无 profile 时回退到默认值。
    """
    data = _read_store()
    active_id = data.get("active_id")
    profiles = data.get("profiles", [])
    if active_id:
        for p in profiles:
            if p.get("id") == active_id:
                return {
                    "model": p.get("model") or LLM_MODEL,
                    "api_base": p.get("api_base") or LLM_API_BASE,
                    "api_key": p.get("api_key") or os.environ.get(LLM_API_KEY_ENV, ""),
                    "temperature": p.get("temperature", LLM_TEMPERATURE),
                }
    return _default_config()


def get_safe_config() -> dict:
    """读取当前生效配置，api_key 脱敏（供前端展示）。"""
    cfg = get_config()
    cfg["api_key_masked"] = _mask_key(cfg.get("api_key", ""))
    return cfg


def set_config(model: str | None = None, api_base: str | None = None,
               api_key: str | None = None, temperature: float | None = None) -> dict:
    """直接更新当前生效配置（向后兼容旧接口）。
    若存在激活 profile 则更新它，否则写入默认值并持久化为匿名配置。
    """
    with _lock:
        data = _read_store()
        active_id = data.get("active_id")
        profiles = data.get("profiles", [])
        if active_id:
            for p in profiles:
                if p.get("id") == active_id:
                    if model is not None and model.strip():
                        p["model"] = model.strip()
                    if api_base is not None and api_base.strip():
                        p["api_base"] = api_base.strip()
                    if api_key is not None and api_key.strip() and "*" not in api_key:
                        p["api_key"] = api_key.strip()
                    if temperature is not None:
                        try:
                            p["temperature"] = float(temperature)
                        except (ValueError, TypeError):
                            pass
                    _write_store(data)
                    break
        else:
            # 无激活 profile：更新默认值并写入一个"默认"profile
            cfg = _default_config()
            if model is not None and model.strip():
                cfg["model"] = model.strip()
            if api_base is not None and api_base.strip():
                cfg["api_base"] = api_base.strip()
            if api_key is not None and api_key.strip() and "*" not in api_key:
                cfg["api_key"] = api_key.strip()
            if temperature is not None:
                try:
                    cfg["temperature"] = float(temperature)
                except (ValueError, TypeError):
                    pass
            new_id = uuid.uuid4().hex[:8]
            profiles.append({
                "id": new_id,
                "name": "默认",
                **cfg,
            })
            data["profiles"] = profiles
            data["active_id"] = new_id
            _write_store(data)
    _notify_config_changed()
    return get_config()


# ── Profile CRUD ────────────────────────────────────────────────────────────

def list_profiles() -> list[dict]:
    """列出所有已保存的配置档案，api_key 脱敏。"""
    data = _read_store()
    active_id = data.get("active_id")
    result = []
    for p in data.get("profiles", []):
        result.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "model": p.get("model", ""),
            "api_base": p.get("api_base", ""),
            "api_key_masked": _mask_key(p.get("api_key", "")),
            "temperature": p.get("temperature", 0),
            "active": p.get("id") == active_id,
        })
    return result


def get_active_profile() -> dict | None:
    """获取当前激活的 profile（脱敏）。"""
    for p in list_profiles():
        if p.get("active"):
            return p
    return None


def save_profile(profile_id: str | None = None, name: str = "",
                 model: str = "", api_base: str = "",
                 api_key: str | None = None, temperature: float = 0,
                 activate: bool = True) -> dict:
    """新增或更新一个配置档案。
    - profile_id 为空时新增，否则更新已有档案。
    - api_key 为 None 表示不修改；为非空字符串（不含 *）表示更新。
    - activate=True 时设为当前激活。
    返回更新后的 profile（脱敏）。
    """
    with _lock:
        data = _read_store()
        profiles = data.get("profiles", [])
        if profile_id:
            # 更新已有
            target = None
            for p in profiles:
                if p.get("id") == profile_id:
                    target = p
                    break
            if target is None:
                raise ValueError(f"配置档案不存在：{profile_id}")
            if name.strip():
                target["name"] = name.strip()
            if model.strip():
                target["model"] = model.strip()
            if api_base.strip():
                target["api_base"] = api_base.strip()
            if api_key is not None and api_key.strip() and "*" not in api_key:
                target["api_key"] = api_key.strip()
            try:
                target["temperature"] = float(temperature)
            except (ValueError, TypeError):
                pass
        else:
            # 新增
            new_id = uuid.uuid4().hex[:8]
            target = {
                "id": new_id,
                "name": name.strip() or f"配置 {len(profiles) + 1}",
                "model": model.strip() or LLM_MODEL,
                "api_base": api_base.strip() or LLM_API_BASE,
                "api_key": (api_key.strip() if api_key and "*" not in api_key
                            else os.environ.get(LLM_API_KEY_ENV, "")),
                "temperature": float(temperature) if temperature is not None else LLM_TEMPERATURE,
            }
            profiles.append(target)
        if activate:
            data["active_id"] = target["id"]
        _write_store(data)
        result = {
            "id": target["id"],
            "name": target["name"],
            "model": target["model"],
            "api_base": target["api_base"],
            "api_key_masked": _mask_key(target.get("api_key", "")),
            "temperature": target.get("temperature", 0),
            "active": target["id"] == data.get("active_id"),
        }
    _notify_config_changed()
    return result


def activate_profile(profile_id: str) -> dict:
    """切换激活的配置档案。返回更新后的 profile（脱敏）。"""
    with _lock:
        data = _read_store()
        profiles = data.get("profiles", [])
        found = None
        for p in profiles:
            if p.get("id") == profile_id:
                found = p
                break
        if found is None:
            raise ValueError(f"配置档案不存在：{profile_id}")
        data["active_id"] = profile_id
        _write_store(data)
        result = {
            "id": found["id"],
            "name": found["name"],
            "model": found["model"],
            "api_base": found["api_base"],
            "api_key_masked": _mask_key(found.get("api_key", "")),
            "temperature": found.get("temperature", 0),
            "active": True,
        }
    _notify_config_changed()
    return result


def delete_profile(profile_id: str) -> dict:
    """删除一个配置档案。若删除的是激活档案，自动切换到第一个。"""
    with _lock:
        data = _read_store()
        profiles = data.get("profiles", [])
        new_profiles = [p for p in profiles if p.get("id") != profile_id]
        if len(new_profiles) == len(profiles):
            raise ValueError(f"配置档案不存在：{profile_id}")
        data["profiles"] = new_profiles
        if data.get("active_id") == profile_id:
            data["active_id"] = new_profiles[0]["id"] if new_profiles else None
        _write_store(data)
    _notify_config_changed()
    return {"remaining": len(new_profiles), "active_id": data.get("active_id")}


def _notify_config_changed() -> None:
    """配置变更后，重置 LLM 单例并清空所有会话的 Agent 缓存。"""
    try:
        from core.agent import reset_llm
        reset_llm()
    except Exception:
        pass
    try:
        from services import agent_service
        agent_service.reset_all_agents()
    except Exception:
        pass
