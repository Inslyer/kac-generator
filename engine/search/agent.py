"""Поисковый агент: по позиции спецификации находит ≥MIN_SOURCES предложений,
проверяет самовывоз Москва/МО, извлекает цену и реквизиты, отбирает TOP_PRICES максимальных.

Извлечение цены/региона/ИНН со страницы товара делает Claude (по тексту страницы),
скриншот карточки — Playwright. Реквизиты добиваются через DaData по ИНН.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from ..config import CACHE_DIR, settings
from ..llm import complete_json
from ..models import OrgStatus, PositionResult, PriceOffer, Requisites, SpecPosition
from ..requisites.dadata import fetch_requisites
from ..requisites.supplier import fetch_requisites_from_site, valid_inn
from .search_engine import search_candidates, search_on_sites
from .whitelist import requisites_for as wl_requisites_for
from .whitelist import whitelist_hosts

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


def _log(msg: str) -> None:
    """Диагностика поиска в окне движка (start.command)."""
    print(f"[поиск] {msg}", file=sys.stderr, flush=True)


def build_queries(pos: SpecPosition) -> list[str]:
    base = f"{pos.name} {pos.type_mark}".strip()
    try:
        qs = complete_json(f"Товар: {base}", system=QUERY_SYSTEM)
        if isinstance(qs, list) and qs:
            return [str(q) for q in qs][:3]
    except Exception:
        pass
    return [f"{base} купить цена Москва", f"{base} цена", base]


# Антибот/капча-интерстишелы (Yandex SmartCaptcha, Cloudflare, «я не робот»). На таких
# страницах нет товара, а LLM может выдумать цену/ИНН — оффер отбрасываем.
# STRONG — фразы, которых на реальной карточке товара практически не бывает: срабатывают
# при любой длине (капча Яндекса бывает многословной — «Разверните картинку» и пр.).
_BOT_MARKERS_STRONG = (
    "подтвердите, что вы", "подтвердите, что запросы", "запросы отправляли вы",
    "вы не робот", "я не робот", "это не я", "разверните картинку",
    "именно реальный человек", "получить доступ к сайту", "доступ ограничен",
    "проверка безопасности", "checking your browser", "verify you are human",
    "необычный трафик", "unusual traffic", "showcaptcha", "smartcaptcha",
)
# WEAK — общие слова, которые изредка встречаются и в нормальном тексте: только на коротких
# страницах (на реальной карточке товара текста много).
_BOT_MARKERS_WEAK = ("captcha", "капча", "robot")


def _looks_like_bot_check(text: str) -> bool:
    """Похоже ли на страницу-заглушку антибота/капчи (а не карточку товара)."""
    low = text.lower()
    if any(m in low for m in _BOT_MARKERS_STRONG):
        return True
    return len(text) < 1500 and any(m in low for m in _BOT_MARKERS_WEAK)


def _extract_from_page(page_text: str) -> dict | None:
    try:
        return complete_json(f"Текст страницы:\n\n{page_text[:12000]}",
                             system=EXTRACT_SYSTEM)
    except Exception as e:
        _log(f"    извлечение LLM упало: {type(e).__name__}: {e}")
        return None


def _dedup_by_host(urls: list[str], skip_hosts: set[str] | None = None) -> list[str]:
    """Убирает дубли по хосту (и хосты из skip_hosts), сохраняя порядок."""
    from urllib.parse import urlparse
    seen = set(skip_hosts or ())
    out: list[str] = []
    for u in urls:
        h = urlparse(u).netloc.replace("www.", "")
        if h and h not in seen:
            seen.add(h)
            out.append(u)
    return out


def _collect_offers(browser, urls: list[str], want: int) -> tuple[list[PriceOffer], int]:
    """Обходит список URL, извлекает цену/реквизиты, возвращает (офферы, обойдено)."""
    offers: list[PriceOffer] = []
    checked = 0
    for url in urls:
        if len(offers) >= want:
            break
        checked += 1
        try:
            with browser.page(url) as page:
                page.wait_for_timeout(1500)
                text = page.inner_text("body")
                # антибот/капча: даём второй шанс — перезагрузка + ожидание (часто JS-челлендж
                # или cookie проходят со 2-й попытки). Упорная капча (напр. vseinstrumenti.ru)
                # требует резидентного прокси/cookie — тогда страница пропускается.
                if _looks_like_bot_check(text):
                    _log(f"  ↻ {url[:60]} → капча, повтор через 3.5с…")
                    try:
                        page.wait_for_timeout(3500)
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                        text = page.inner_text("body")
                    except Exception:
                        pass
                if _looks_like_bot_check(text):
                    _log(f"  ✗ {url[:60]} → антибот/капча-страница, пропуск")
                    continue
                data = _extract_from_page(text)
                if not data:
                    _log(f"  ✗ {url[:60]} → извлечение не дало JSON (текст {len(text)} симв.)")
                    continue
                if not data.get("price_with_vat"):
                    _log(f"  ✗ {url[:60]} → цена не найдена на странице")
                    continue
                _log(f"  ✓ {url[:60]} → цена {data.get('price_with_vat')}")
                # регион НЕ фильтруем жёстко: страница часто не пишет про самовывоз явно.
                in_region = bool(data.get("moscow_pickup", False))
                # Реквизиты доверенного поставщика — прямо из Поставщики.md (надёжно, без скрейпа).
                req = wl_requisites_for(url)
                if req is None:
                    inn = re.sub(r"\D", "", str(data.get("seller_inn") or ""))
                    if not valid_inn(inn):   # отсекаем выдуманные ИНН (галлюцинация)
                        inn = ""
                    req = fetch_requisites(inn) if inn else None
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
            # реквизиты в ТКП рисуются текстом из req (наименование/ИНН/КПП) — скриншот не нужен
            offers.append(PriceOffer(
                price_with_vat=float(data["price_with_vat"]),
                url=url,
                product_title=str(data.get("product_title", "")),
                in_moscow_region=in_region,
                requisites=req,
                screenshot_product=str(shot),
            ))
        except Exception as e:
            _log(f"  ✗ {url[:60]} → ошибка: {type(e).__name__}: {e}")
            continue
    return offers, checked


def find_offers_for_position(browser, pos: SpecPosition) -> tuple[list[PriceOffer], int]:
    """Сначала ищет у доверенных поставщиков (Поставщики.md); если их меньше TOP_PRICES —
    добирает из общего интернета. Возвращает (офферы, число_обойдённых_карточек).
    """
    queries = build_queries(pos)
    _log(f"позиция «{pos.name[:50]}» → запросы: {queries}")
    # выдачу берём с запасом: после фильтров (маркетплейсы, дубли, нет цены, капча) отсев большой
    per_query = max(20, settings.min_sources * 4)
    want = settings.min_sources

    # Фаза 1 — доверенные поставщики (site:-ограниченная выдача).
    hosts = whitelist_hosts()
    wl_urls: list[str] = []
    for q in queries:
        try:
            wl_urls += search_on_sites(q, hosts, n=per_query)
        except Exception as e:
            _log(f"  (поставщики) запрос «{q[:40]}» → сбой: {type(e).__name__}: {e}")
    wl_urls = _dedup_by_host(wl_urls)
    _log(f"фаза 1 (поставщики): кандидатов {len(wl_urls)}")
    offers, checked = _collect_offers(browser, wl_urls, want)
    used_hosts = {urlparse(o.url).netloc.replace('www.', '') for o in offers}

    # Фаза 2 — общий интернет, только если у поставщиков набралось меньше TOP_PRICES.
    if len(offers) < settings.top_prices:
        _log(f"фаза 1: офферов {len(offers)} (<{settings.top_prices}) → добор из интернета")
        net_urls: list[str] = []
        for q in queries:
            try:
                found = search_candidates(browser, q, n=per_query)
                _log(f"  запрос «{q[:40]}» → {len(found)} ссылок")
                net_urls += found
            except Exception as e:
                _log(f"  запрос «{q[:40]}» → сбой: {type(e).__name__}: {e}")
        net_urls = _dedup_by_host(net_urls, skip_hosts=used_hosts)
        net_offers, net_checked = _collect_offers(browser, net_urls, want - len(offers))
        offers += net_offers
        checked += net_checked
    else:
        _log(f"фаза 1: офферов {len(offers)} (≥{settings.top_prices}) — интернет не нужен")

    _log(f"итог по позиции: офферов {len(offers)} из {checked} обойдённых")
    return offers, checked


def research_position(browser, pos: SpecPosition, year: str, quarter: str) -> PositionResult:
    """Полный цикл по одной позиции: поиск → отбор TOP максимальных по возрастанию.

    В all_offers сохраняются ВСЕ найденные кандидаты (для замены позиции в топ-3 в UI).
    """
    offers, checked = find_offers_for_position(browser, pos)
    offers.sort(key=lambda o: o.price_with_vat)  # по возрастанию
    top = offers[-settings.top_prices:] if offers else []
    return PositionResult(position=pos, offers=top, all_offers=offers, year=year, quarter=quarter,
                          sources_checked=checked, sources_found=len(offers),
                          sources_target=settings.min_sources)
