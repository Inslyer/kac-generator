"""Парсер базы ТСН-2001 с mos.ru (Playwright) + коэффициенты пересчёта в текущие цены.

Источник: https://www.mos.ru/mke/function/tcenoobrazovanie/baza-smetnykh-normativov-tsn-2001
Доступ к mos.ru стабильно работает только из локального браузера с российским IP
(серверный fetch отдаёт таймаут).

Структура mos.ru периодически меняется, поэтому селекторы вынесены в константы. Если автопарсинг
не сработал — модуль возвращает None, и сметчик вводит базовую расценку/коэффициент вручную (UI).
"""
from __future__ import annotations

from datetime import date

from ..llm import complete_json
from ..models import TsnRow

BASE_URL = ("https://www.mos.ru/mke/function/tcenoobrazovanie/"
            "baza-smetnykh-normativov-tsn-2001")

MATCH_SYSTEM = (
    "Тебе дан фрагмент сборника ТСН-2001 (текст со страницы) и наименование ресурса. "
    "Найди наиболее подходящую расценку. Верни JSON: "
    "{code: строка-шифр, name: строка, base_price: число (базовая стоимость в ценах 2000 г.), "
    "found: bool}. Если не найдено — found:false. Только JSON."
)


def fetch_current_coefficient(browser, as_of: date | None = None) -> float | None:
    """Извлекает индекс/коэффициент пересчёта ТСН-2001 в текущие цены на дату as_of.

    На mos.ru публикуются ежеквартальные индексы пересчёта. Возвращает коэффициент или None.
    """
    try:
        with browser.page(BASE_URL) as page:
            page.wait_for_timeout(3000)
            text = page.inner_text("body")
        data = complete_json(
            f"Дата: {(as_of or date.today()).isoformat()}\n\nТекст страницы mos.ru про индексы "
            f"пересчёта ТСН-2001:\n\n{text[:15000]}",
            system="Найди актуальный коэффициент (индекс) пересчёта ТСН-2001 в текущие цены на "
                   "указанную дату. Верни JSON {coefficient: число, period: строка}. Только JSON.",
            smart=True,
        )
        return float(data["coefficient"])
    except Exception:
        return None


def lookup_tsn_price(browser, name: str) -> dict | None:
    """Ищет базовую расценку ТСН-2001 по наименованию ресурса. None при неудаче."""
    try:
        with browser.page(BASE_URL) as page:
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
        data = complete_json(
            f"Наименование: {name}\n\nФрагмент сборника ТСН-2001:\n\n{text[:15000]}",
            system=MATCH_SYSTEM, smart=True,
        )
        return data if data.get("found") else None
    except Exception:
        return None


def build_tsn_rows(browser, results, as_of: date | None = None) -> list[TsnRow]:
    """По итогам КАЦ собирает строки ТСН (базовая расценка + коэффициент + макс. цена КАЦ).

    results — список PositionResult. Возвращает список TsnRow (без записи в файл).
    """
    coeff = fetch_current_coefficient(browser, as_of)
    rows: list[TsnRow] = []
    for res in results:
        pos = res.position
        tsn = lookup_tsn_price(browser, pos.name) if not pos.is_unique else None
        rows.append(TsnRow(
            number=pos.number,
            name=pos.name,
            unit=pos.unit,
            qty=pos.qty,
            tsn_base_price=(tsn or {}).get("base_price"),
            coefficient=coeff,
            kac_max_price=res.max_offer.price_with_vat if res.max_offer else None,
        ))
    return rows
