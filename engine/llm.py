"""Тонкая обёртка над Claude API для структуризации/матчинга.

Если ключ не задан — функции бросают LLMUnavailable, чтобы вызывающий код мог
переключиться на ручной/демо-режим.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .config import settings


class LLMUnavailable(RuntimeError):
    pass


def _client():
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


def complete_json(
    prompt: str,
    *,
    system: str = "",
    smart: bool = False,
    max_tokens: int = 4096,
    images: list[str] | None = None,
) -> Any:
    """Запрос к Claude с ожиданием JSON-ответа. Возвращает распарсенный объект.

    smart=True → дорогая модель (matching, сложные случаи); иначе быстрая.
    images — пути к PNG (страницы скана) для распознавания через vision.
    """
    client = _client()
    model = settings.model_smart if smart else settings.model_fast
    content: list[dict] = [_image_block(p) for p in (images or [])]
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or "Ты помощник сметчика. Отвечай ТОЛЬКО валидным JSON без пояснений.",
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text").strip()
    # на случай ```json ... ```
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)
