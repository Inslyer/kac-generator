"""Реквизиты организации по ИНН через DaData + скриншот панели реквизитов для Тома ТКП."""
from __future__ import annotations

from pathlib import Path

import httpx

from ..config import CACHE_DIR, settings
from ..models import OrgStatus, Requisites

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
# Сервис для скриншота карточки реквизитов (панель «по данным ФНС»)
REQUISITES_PAGE = "https://checko.ru/company/{inn}"


def fetch_requisites(inn: str) -> Requisites:
    """Тянет наименование/КПП/адрес по ИНН. Без токена — возвращает заготовку с одним ИНН."""
    if not settings.dadata_token:
        return Requisites(inn=inn, status=OrgStatus.SUPPLIER)
    resp = httpx.post(
        DADATA_URL,
        headers={"Authorization": f"Token {settings.dadata_token}",
                 "Content-Type": "application/json"},
        json={"query": inn, "count": 1},
        timeout=15,
    )
    resp.raise_for_status()
    suggestions = resp.json().get("suggestions") or []
    if not suggestions:
        return Requisites(inn=inn)
    data = suggestions[0]["data"]
    city = ""
    addr = (data.get("address") or {}).get("data") or {}
    city = addr.get("city") or addr.get("region") or "Москва"
    return Requisites(
        name=suggestions[0].get("value", ""),
        inn=data.get("inn", inn),
        kpp=data.get("kpp", "") or "",
        city=city,
        status=OrgStatus.SUPPLIER,
    )


def screenshot_requisites(browser, inn: str) -> Path | None:
    """Скриншот панели реквизитов организации (правая колонка страницы ТКП)."""
    out = CACHE_DIR / f"req_{inn}.png"
    if out.exists():
        return out
    try:
        return browser.screenshot(REQUISITES_PAGE.format(inn=inn), out, clip_top=900)
    except Exception:
        return None
