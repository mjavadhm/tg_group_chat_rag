from __future__ import annotations

import json
import logging
import os
import random
from typing import Any
import openai

from bot.config import get_setting, set_setting

logger = logging.getLogger("bot.providers")


def mask_key(key: str) -> str:
    """پنهان‌سازی امنیتی کلید API در پنل ادمین (مثال: sk-12••••34ab)."""
    if not key:
        return "بدون کلید"
    clean = key.strip()
    if len(clean) <= 8:
        return "••••••••"
    return f"{clean[:4]}••••{clean[-4:]}"


def normalize_provider_keys(raw_keys: list[Any]) -> list[dict[str, Any]]:
    """نرمال‌سازی کلیدها به ساختار نام‌دار: [{'name': '...', 'key': '...', 'is_default': bool}]"""
    normalized = []
    has_default = False
    for idx, item in enumerate(raw_keys, 1):
        if isinstance(item, str):
            clean = item.strip()
            if clean:
                is_def = not has_default
                if is_def:
                    has_default = True
                normalized.append({
                    "name": f"کلید {idx}",
                    "key": clean,
                    "is_default": is_def,
                })
        elif isinstance(item, dict) and "key" in item:
            clean = str(item.get("key", "")).strip()
            if clean:
                name = str(item.get("name", f"کلید {idx}")).strip()
                is_def = bool(item.get("is_default", False))
                if is_def:
                    has_default = True
                normalized.append({
                    "name": name,
                    "key": clean,
                    "is_default": is_def,
                })
    if normalized and not has_default:
        normalized[0]["is_default"] = True
    return normalized


DEFAULT_PROVIDERS = {
    "tokenrouter": {
        "id": "tokenrouter",
        "name": "🚀 TokenRouter",
        "base_url": "https://api.tokenrouter.com/v1",
        "api_keys": [
            {"name": "کلید پیش‌فرض محیطی", "key": os.getenv("LLM_API_KEY", ""), "is_default": True}
        ] if os.getenv("LLM_API_KEY") else [],
        "models": [
            "z-ai/glm-5.3-free",
        ],
    },
    "google": {
        "id": "google",
        "name": "🌐 Google Gemini (AI Studio)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_keys": [],
        "models": [
            "gemini-2.0-flash",
            "gemini-2.0-flash-thinking-exp",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ],
    },
    "groq": {
        "id": "groq",
        "name": "⚡ Groq (Ultra Fast)",
        "base_url": "https://api.groq.com/openai/v1",
        "api_keys": [],
        "models": [
            "llama-3.3-70b-versatile",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
    },
    "openrouter": {
        "id": "openrouter",
        "name": "🧠 OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_keys": [],
        "models": [
            "google/gemini-2.0-flash-001",
            "deepseek/deepseek-r1:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
    },
    "deepseek": {
        "id": "deepseek",
        "name": "💻 DeepSeek Official",
        "base_url": "https://api.deepseek.com/v1",
        "api_keys": [],
        "models": [
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    },
    "ollama": {
        "id": "ollama",
        "name": "🏠 Local Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_keys": [{"name": "پیش‌فرض محلی", "key": "ollama", "is_default": True}],
        "models": [
            "qwen2.5:7b",
            "deepseek-r1:8b",
            "llama3.1:8b",
        ],
    },
}


class ProviderRegistry:
    """
    مدیریت جامع پروایدرها، کلیدهای چندگانه نام‌دار (API Keys) و مدل‌ها.
    ذخیره‌سازی دائمی پیکربندی در SQLite.
    """

    @classmethod
    def get_providers(cls) -> dict[str, dict[str, Any]]:
        raw = get_setting("CUSTOM_PROVIDERS_JSON", "")
        providers = dict(DEFAULT_PROVIDERS)
        if raw:
            try:
                custom = json.loads(raw)
                providers.update(custom)
            except Exception as e:
                logger.error(f"Error parsing CUSTOM_PROVIDERS_JSON: {e}")

        # نرمال‌سازی کلیدهای همه پروایدرها
        for p_id, p_data in providers.items():
            p_data["api_keys"] = normalize_provider_keys(p_data.get("api_keys", []))
            p_data["models"] = [m.strip() for m in p_data.get("models", []) if m.strip()]
        return providers

    @classmethod
    def save_providers(cls, providers: dict[str, dict[str, Any]]) -> None:
        set_setting("CUSTOM_PROVIDERS_JSON", json.dumps(providers, ensure_ascii=False))

    @classmethod
    def get_provider(cls, provider_id: str) -> dict[str, Any] | None:
        return cls.get_providers().get(provider_id.lower())

    @classmethod
    def add_provider(
        cls,
        provider_id: str,
        name: str,
        base_url: str,
        api_keys: list[Any] | None = None,
        models: list[str] | None = None,
    ) -> None:
        p_id = provider_id.strip().lower()
        providers = cls.get_providers()
        providers[p_id] = {
            "id": p_id,
            "name": name.strip(),
            "base_url": base_url.strip(),
            "api_keys": normalize_provider_keys(api_keys or []),
            "models": [m.strip() for m in (models or []) if m.strip()],
        }
        cls.save_providers(providers)

    @classmethod
    def rename_provider(cls, provider_id: str, new_name: str) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers:
            providers[p_id]["name"] = new_name.strip()
            cls.save_providers(providers)

    @classmethod
    def set_provider_base_url(cls, provider_id: str, new_base_url: str) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers:
            providers[p_id]["base_url"] = new_base_url.strip()
            cls.save_providers(providers)

    @classmethod
    def delete_provider(cls, provider_id: str) -> bool:
        p_id = provider_id.strip().lower()
        providers = cls.get_providers()
        if p_id in providers:
            del providers[p_id]
            cls.save_providers(providers)
            return True
        return False

    # --- مدیریت کلیدهای نام‌دار ---

    @classmethod
    def add_api_key(cls, provider_id: str, name: str, key: str, is_default: bool = False) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers:
            clean_k = key.strip()
            clean_name = name.strip() or f"کلید {len(providers[p_id]['api_keys']) + 1}"
            if clean_k:
                # حذف هم‌نام قبلی در صورت وجود
                providers[p_id]["api_keys"] = [k for k in providers[p_id]["api_keys"] if k["name"] != clean_name]
                if is_default or len(providers[p_id]["api_keys"]) == 0:
                    for k in providers[p_id]["api_keys"]:
                        k["is_default"] = False
                    is_default = True
                providers[p_id]["api_keys"].append({
                    "name": clean_name,
                    "key": clean_k,
                    "is_default": is_default,
                })
                cls.save_providers(providers)

    @classmethod
    def remove_api_key(cls, provider_id: str, key_name: str) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers:
            remaining = [k for k in providers[p_id]["api_keys"] if k["name"] != key_name]
            # اگر کلید حذف‌شده پیش‌فرض بود، به اولین کلید باقیمانده اختصاص می‌دهیم
            if remaining and not any(k.get("is_default") for k in remaining):
                remaining[0]["is_default"] = True
            providers[p_id]["api_keys"] = remaining
            cls.save_providers(providers)

    @classmethod
    def set_default_api_key(cls, provider_id: str, key_name: str) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers:
            for k in providers[p_id]["api_keys"]:
                k["is_default"] = (k["name"] == key_name)
            cls.save_providers(providers)

    # --- مدیریت مدل‌ها ---

    @classmethod
    def add_provider_model(cls, provider_id: str, model_name: str) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers:
            clean_m = model_name.strip()
            if clean_m and clean_m not in providers[p_id]["models"]:
                providers[p_id]["models"].append(clean_m)
                cls.save_providers(providers)

    @classmethod
    def remove_provider_model(cls, provider_id: str, model_name: str) -> None:
        providers = cls.get_providers()
        p_id = provider_id.lower()
        if p_id in providers and model_name in providers[p_id]["models"]:
            providers[p_id]["models"].remove(model_name)
            cls.save_providers(providers)

    # --- تنظیمات اسلات‌های مدل ---

    @classmethod
    def get_reasoning_slot(cls) -> tuple[str, str, str, str]:
        """برمی‌گرداند: (provider_id, model_name, base_url, api_key)"""
        provider_id = get_setting("REASONING_PROVIDER", "tokenrouter")
        model_name = get_setting("REASONING_MODEL", "z-ai/glm-5.3-free")
        prov = cls.get_provider(provider_id) or DEFAULT_PROVIDERS["tokenrouter"]
        base_url = prov.get("base_url", "https://api.tokenrouter.com/v1")
        keys = prov.get("api_keys", [])

        # انتخاب کلید پیش‌فرض یا اولین کلید فعال
        key = ""
        default_keys = [k["key"] for k in keys if k.get("is_default") and k.get("key")]
        if default_keys:
            key = default_keys[0]
        elif keys:
            key = keys[0].get("key", "")
        if not key:
            key = get_setting("LLM_API_KEY", "")
        return provider_id, model_name, base_url, key

    @classmethod
    def get_fast_slot(cls) -> tuple[str, str, str, str]:
        """برمی‌گرداند: (provider_id, model_name, base_url, api_key)"""
        provider_id = get_setting("FAST_PROVIDER", "tokenrouter")
        model_name = get_setting("FAST_MODEL", "z-ai/glm-5.3-free")
        prov = cls.get_provider(provider_id) or DEFAULT_PROVIDERS["tokenrouter"]
        base_url = prov.get("base_url", "https://api.tokenrouter.com/v1")
        keys = prov.get("api_keys", [])

        key = ""
        default_keys = [k["key"] for k in keys if k.get("is_default") and k.get("key")]
        if default_keys:
            key = default_keys[0]
        elif keys:
            key = keys[0].get("key", "")
        if not key:
            key = get_setting("LLM_API_KEY", "")
        return provider_id, model_name, base_url, key

    @classmethod
    def set_reasoning_slot(cls, provider_id: str, model_name: str) -> None:
        set_setting("REASONING_PROVIDER", provider_id.lower())
        set_setting("REASONING_MODEL", model_name.strip())
        prov = cls.get_provider(provider_id)
        if prov:
            set_setting("LLM_BASE_URL", prov["base_url"])
            set_setting("LLM_MODEL", model_name.strip())
            keys = prov.get("api_keys", [])
            if keys and keys[0].get("key"):
                set_setting("LLM_API_KEY", keys[0]["key"])

    @classmethod
    def set_fast_slot(cls, provider_id: str, model_name: str) -> None:
        set_setting("FAST_PROVIDER", provider_id.lower())
        set_setting("FAST_MODEL", model_name.strip())

    @classmethod
    def get_client_for_slot(cls, slot: str = "reasoning") -> tuple[openai.AsyncOpenAI, str]:
        """ایجاد کلاینت آماده AsyncOpenAI و نام مدل بر اساس اسلات انتخابی."""
        if slot == "fast":
            _, model, base_url, key = cls.get_fast_slot()
        else:
            _, model, base_url, key = cls.get_reasoning_slot()

        client = openai.AsyncOpenAI(
            api_key=key or "no-key-provided",
            base_url=base_url,
        )
        return client, model
