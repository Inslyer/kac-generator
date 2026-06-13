"""Реквизиты организации по ИНН через DaData + скриншот карточки реквизитов для Тома ТКП."""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..config import CACHE_DIR, settings
from ..models import OrgStatus, Requisites

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


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
    addr_block = data.get("address") or {}
    addr = addr_block.get("data") or {}
    city = addr.get("city") or addr.get("region") or "Москва"
    return Requisites(
        name=suggestions[0].get("value", ""),
        inn=data.get("inn", inn),
        kpp=data.get("kpp", "") or "",
        ogrn=data.get("ogrn", "") or "",
        address=addr_block.get("value", "") or "",
        city=city,
        status=OrgStatus.SUPPLIER,
    )


def screenshot_requisites(browser, inn: str) -> Path | None:
    """Скриншот карточки реквизитов организации по ИНН для правой колонки ТКП.

    Пробует ресурсы из settings.requisites_sources_list по порядку и берёт первый, на котором
    карточка реально отрисовалась (ИНН присутствует в тексте). checko.ru исключён — недоступен
    под зарубежным VPN. None, если ни один ресурс не отдал карточку.
    """
    inn = re.sub(r"\D", "", inn or "")
    if len(inn) not in (10, 12):
        return None
    out = CACHE_DIR / f"req_{inn}.png"
    if out.exists():
        return out
    for tmpl in settings.requisites_sources_list:
        url = tmpl.format(inn=inn)
        try:
            with browser.page(url) as page:
                page.wait_for_timeout(2200)            # дать JS отрисовать карточку
                text = page.inner_text("body")
                if inn not in re.sub(r"\D", "", text):  # не та страница/карточка не нашлась
                    continue
                page.screenshot(path=str(out),
                                clip={"x": 0, "y": 0, "width": 1366, "height": 1100})
            return out
        except Exception:
            continue
    return None
