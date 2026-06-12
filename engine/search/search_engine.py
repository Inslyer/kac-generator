"""Поиск кандидатов-магазинов по запросу: Yandex Search API или браузерный фолбэк."""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

import httpx

from ..config import settings

# домены-агрегаторы/маркетплейсы, которые часто не дают «отпускную цену поставщика»
_SKIP_HOSTS = {"market.yandex.ru", "ozon.ru", "wildberries.ru", "avito.ru", "youla.ru"}


def _host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def yandex_api_search(query: str, n: int = 20) -> list[str]:
    """Yandex Search API (xml). Требует YANDEX_SEARCH_API_KEY + folder_id."""
    if not (settings.yandex_search_api_key and settings.yandex_search_folder_id):
        return []
    url = "https://yandex.ru/search/xml"
    params = {
        "folderid": settings.yandex_search_folder_id,
        "apikey": settings.yandex_search_api_key,
        "query": query, "l10n": "ru", "sortby": "rlv", "groupby": f"groups-on-page={n}",
    }
    r = httpx.get(url, params=params, timeout=20)
    r.raise_for_status()
    return re.findall(r"<url>(.*?)</url>", r.text)


def browser_search(browser, query: str, n: int = 20) -> list[str]:
    """Фолбэк: парсинг выдачи поисковика через Playwright (российский IP)."""
    urls: list[str] = []
    search_url = f"https://yandex.ru/search/?text={quote_plus(query)}"
    with browser.page(search_url) as page:
        page.wait_for_timeout(2000)
        for a in page.query_selector_all("a.Link, a.OrganicTitle-Link, a[href^='http']"):
            href = a.get_attribute("href") or ""
            if href.startswith("http"):
                urls.append(href)
    # дедуп по хосту, отбрасываем маркетплейсы и сам яндекс
    seen, out = set(), []
    for u in urls:
        h = _host(u)
        if not h or "yandex" in h or h in _SKIP_HOSTS or h in seen:
            continue
        seen.add(h)
        out.append(u)
        if len(out) >= n:
            break
    return out


def search_candidates(browser, query: str, n: int = 20) -> list[str]:
    api = yandex_api_search(query, n)
    if api:
        return [u for u in api if _host(u) not in _SKIP_HOSTS][:n]
    return browser_search(browser, query, n)
