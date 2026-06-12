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
    """Фолбэк: парсинг выдачи поисковика через Playwright (российский IP).

    Ссылки собираются ОДНИМ атомарным вызовом evaluate(), иначе страница выдачи
    успевает редиректнуть и контекст разрушается («Execution context was destroyed»).
    Любая ошибка/капча → возвращаем то, что успели (вплоть до пустого списка), без падения.
    """
    from ..config import settings

    def _grab(page):
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('a'))"
            ".map(a => a.href).filter(h => h && h.startsWith('http'))"
        ) or []

    def _is_captcha(page) -> bool:
        u = (page.url or "").lower()
        return "captcha" in u or "/checkcaptcha" in u or "showcaptcha" in u

    urls: list[str] = []
    search_url = f"https://yandex.ru/search/?text={quote_plus(query)}"
    try:
        with browser.page(search_url) as page:
            page.wait_for_timeout(2500)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            # Капча Яндекса. В видимом режиме ждём, пока пользователь решит её в окне браузера.
            if _is_captcha(page) and settings.browser_headed:
                print("⚠ Яндекс показал капчу — решите её в открытом окне браузера "
                      "(ждём до 90 сек)…")
                for _ in range(45):
                    page.wait_for_timeout(2000)
                    if not _is_captcha(page):
                        break
            urls = _grab(page)
    except Exception:
        return []
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
