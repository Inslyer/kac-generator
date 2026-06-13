"""Поисковый агент: по позиции спецификации находит ≥MIN_SOURCES предложений,
проверяет самовывоз Москва/МО, извлекает цену и реквизиты, отбирает TOP_PRICES максимальных.

Извлечение цены/региона/ИНН со страницы товара делает Claude (по тексту страницы),
скриншот карточки — Playwright. Реквизиты добиваются через DaData по ИНН.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import CACHE_DIR, settings
from ..llm import complete_json
from ..models import OrgStatus, PositionResult, PriceOffer, Requisites, SpecPosition
from ..requisites.dadata import fetch_requisites, screenshot_requisites
from ..requisites.supplier import fetch_requisites_from_site
from .search_engine import search_candidates

QUERY_SYSTEM = (
    "Сформируй 3 коротких поисковых запроса для покупки этого товара в РФ (купить, цена, "
    "Москва). Верни JSON-массив строк."
)
EXTRACT_SYSTEM = (
    "Со страницы интернет-магазина извлеки JSON: "
    "price_with_vat (число — цена за единицу в рублях С НДС, без разделителей), "
    "product_title (строка), "
    "moscow_pickup (bool — доступен ли самовывоз/склад в Москве или МО), "
    "city (строка — город/регион склада или продавца, если указан, иначе пусто), "
    "seller_inn (строка — ИНН продавца, если указан на странице, иначе пусто). "
    "Если цена не найдена — price_with_vat: null. Только JSON."
)


def build_queries(pos: SpecPosition) -> list[str]:
    base = f"{pos.name} {pos.type_mark}".strip()
    try:
        qs = complete_json(f"Товар: {base}", system=QUERY_SYSTEM)
        if isinstance(qs, list) and qs:
            return [str(q) for q in qs][:3]
    except Exception:
        pass
    return [f"{base} купить цена Москва", f"{base} цена", base]


def _extract_from_page(page_text: str) -> dict | None:
    try:
        return complete_json(f"Текст страницы:\n\n{page_text[:12000]}",
                             system=EXTRACT_SYSTEM)
    except Exception:
        return None


def find_offers_for_position(browser, pos: SpecPosition) -> tuple[list[PriceOffer], int]:
    """Обходит кандидатов и собирает валидные офферы (самовывоз Москва/МО, есть цена).

    Возвращает (офферы, число_обойдённых_карточек).
    """
    queries = build_queries(pos)
    candidates: list[str] = []
    for q in queries:
        try:
            candidates += search_candidates(browser, q, n=settings.min_sources)
        except Exception:
            continue  # сбой/капча по одному запросу не валит всю позицию
    # дедуп по хосту
    seen, urls = set(), []
    for u in candidates:
        from urllib.parse import urlparse
        h = urlparse(u).netloc.replace("www.", "")
        if h and h not in seen:
            seen.add(h)
            urls.append(u)

    offers: list[PriceOffer] = []
    checked = 0
    for url in urls:
        if len(offers) >= settings.min_sources:
            break
        checked += 1
        try:
            with browser.page(url) as page:
                page.wait_for_timeout(1500)
                text = page.inner_text("body")
                data = _extract_from_page(text)
                if not data or not data.get("price_with_vat"):
                    continue
                # регион НЕ фильтруем жёстко: страница часто не пишет про самовывоз явно.
                # Берём оффер, проставляем предполагаемый регион — сметчик проверит/поправит
                # в таблице (не-Москва/МО подсвечивается).
                in_region = bool(data.get("moscow_pickup", False))
                inn = re.sub(r"\D", "", str(data.get("seller_inn") or ""))
                # ИНН с карточки товара бывает редко → обогащаем DaData (если есть токен),
                # иначе/дополнительно добираем реквизиты с сайта продавца (работает под VPN,
                # в отличие от checko/ФНС).
                req = fetch_requisites(inn) if len(inn) in (10, 12) else None
                if req is None or not req.inn or not req.name:
                    site_req = fetch_requisites_from_site(browser, url)
                    if site_req:
                        req = site_req
                if req is None:
                    req = Requisites(status=OrgStatus.SUPPLIER)
                if not in_region:
                    req.city = str(data.get("city") or "уточнить")
                shot = CACHE_DIR / f"prod_{abs(hash(url)) % 10**10}.png"
                page.screenshot(path=str(shot), clip={"x": 0, "y": 0,
                                                       "width": 1366, "height": 900})
            # скриншот карточки реквизитов по ИНН (ресурсы из config, не checko); если ни один
            # не отрисовался — в ТКП рисуется текстовая панель из req
            req_shot = screenshot_requisites(browser, req.inn) if req.inn else None
            offers.append(PriceOffer(
                price_with_vat=float(data["price_with_vat"]),
                url=url,
                product_title=str(data.get("product_title", "")),
                in_moscow_region=in_region,
                requisites=req,
                screenshot_product=str(shot),
                screenshot_requisites=str(req_shot) if req_shot else None,
            ))
        except Exception:
            continue
    return offers, checked


def research_position(browser, pos: SpecPosition, year: str, quarter: str) -> PositionResult:
    """Полный цикл по одной позиции: поиск → отбор TOP максимальных по возрастанию."""
    offers, checked = find_offers_for_position(browser, pos)
    offers.sort(key=lambda o: o.price_with_vat)  # по возрастанию
    top = offers[-settings.top_prices:] if offers else []
    return PositionResult(position=pos, offers=top, year=year, quarter=quarter,
                          sources_checked=checked, sources_found=len(offers),
                          sources_target=settings.min_sources)
