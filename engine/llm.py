"""Тонкая обёртка над LLM (Claude или DeepSeek) для структуризации/матчинга.

Провайдер выбирается настройкой LLM_PROVIDER (.env): "anthropic" или "deepseek".
DeepSeek совместим с OpenAI API, дешевле, но БЕЗ vision — изображения принимает только
Claude. При провайдере deepseek вызов с images бросает LLMUnavailable (сканы должны
приходить уже распознанными локальным OCR — см. parsers/spec_pdf.py).

Если ключ не задан — функции бросают LLMUnavailable, чтобы вызывающий код мог
переключиться на ручной/демо-режим.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from .config import settings


class LLMUnavailable(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """Парсит JSON из ответа модели, срезая обёртку ```json ... ``` если есть."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)


# ----------------------------- Anthropic (Claude) -----------------------------

def _anthropic_client():
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY не задан (.env)")
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise LLMUnavailable("пакет anthropic не установлен") from e
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _image_block(path: str | Path) -> dict:
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _complete_anthropic(prompt, system, smart, max_tokens, images) -> Any:
    client = _anthropic_client()
    model = settings.model_smart if smart else settings.model_fast
    content: list[dict] = [_image_block(p) for p in (images or [])]
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or "Ты помощник сметчика. Отвечай ТОЛЬКО валидным JSON без пояснений.",
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    return _extract_json(text)


# ------------------------------- DeepSeek -------------------------------------

def _complete_deepseek(prompt, system, smart, max_tokens, images) -> Any:
    if images:
        raise LLMUnavailable(
            "DeepSeek не поддерживает изображения (vision). Скан должен быть распознан "
            "локально (OCR) до отправки в модель.")
    if not settings.deepseek_api_key:
        raise LLMUnavailable("DEEPSEEK_API_KEY не задан (.env)")
    model = settings.deepseek_model_smart if smart else settings.deepseek_model_fast
    sys_prompt = system or "Ты помощник сметчика. Отвечай ТОЛЬКО валидным JSON без пояснений."
    # response_format=json_object у DeepSeek требует слова "json" в промпте
    if "json" not in (sys_prompt + prompt).lower():
        sys_prompt += " Ответ верни в формате JSON."
    resp = httpx.post(
        settings.deepseek_base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise LLMUnavailable(f"DeepSeek API {resp.status_code}: {resp.text[:300]}")
    text = resp.json()["choices"][0]["message"]["content"]
    return _extract_json(text)


# ------------------------------- Диспетчер ------------------------------------

def complete_json(
    prompt: str,
    *,
    system: str = "",
    smart: bool = False,
    max_tokens: int = 4096,
    images: list[str] | None = None,
) -> Any:
    """Запрос к LLM с ожиданием JSON-ответа. Возвращает распарсенный объект.

    smart=True → более сильная модель (matching, сложные случаи); иначе быстрая/дешёвая.
    images — пути к PNG (страницы скана) для vision; поддерживается только Anthropic.
    """
    if settings.llm_provider.lower() == "deepseek":
        return _complete_deepseek(prompt, system, smart, max_tokens, images)
    return _complete_anthropic(prompt, system, smart, max_tokens, images)
