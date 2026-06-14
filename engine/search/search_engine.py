"""Поиск кандидатов-магазинов по запросу: Yandex Search API или браузерный фолбэк."""
from __future__ import annotations

import base64
import re
import time
from urllib.parse import quote_plus, urlparse

import httpx

from ..config import settings

# домены-агрегаторы/маркетплейсы, которые часто не дают «отпускную цену поставщика»
_SKIP_HOSTS = {"market.yandex.ru", "ozon.ru", "wildberries.ru", "avito.ru", "youla.ru"}

# Yandex Search API v2 (Yandex Cloud): асинхронный веб-поиск, авторизация Api-Key.
_V2_SEARCH = "https://searchapi.api.cloud.yandex.net/v2/web/searchAsync"
_V2_OP = "https://operation.api.cloud.yandex.net/operations/{id}"
# Legacy XML (для аккаунтов, где включён старый интерфейс).
_XML_GET = "https://yandex.ru/search/xml"


def _host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _urls_from_xml(xml: str) -> list[str]:
    return re.findall(r"<url>(.*?)</url>", xml)


def yandex_api_v2(query: str, n: int = 20) -> list[str]:
    """Yandex Search API v2 (асинхронный). Возвращает список URL или [] при недоступности."""
    key, folder = settings.yandex_search_api_key, settings.yandex_search_folder_id
    if not (key and folder):
        return []
    headers = {"Authorization": f"Api-Key {key}"}
    body = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
        "folderId": folder,
        "groupSpec": {"groupMode": "GROUP_MODE_FLAT",
                      "groupsOnPage": str(n), "docsInGroup": "1"},
        "responseFormat": "FORMAT_XML",
    }
    try:
        r = httpx.post(_V2_SEARCH, json=body, headers=headers, timeout=30)
        if r.status_code >= 400:
            print(f"⚠ Yandex API v2: HTTP {r.status_code} — {r.text[:200]}")
            return []
        op_id = r.json().get("id")
        if not op_id:
            return []
        for _ in range(30):  # опрос операции до 60 сек
            time.sleep(2)
            o = httpx.get(_V2_OP.format(id=op_id), headers=headers, timeout=30).json()
            if o.get("done"):
                if "error" in o:
                    print(f"⚠ Yandex API v2: операция с ошибкой — {str(o['error'])[:200]}")
                    return []
                raw = (o.get("response") or {}).get("rawData", "")
                xml = base64.b64decode(raw).decode("utf-8", "ignore") if raw else ""
                return _urls_from_xml(xml)
    except Exception as e:
        print(f"⚠ Yandex API v2: {type(e).__name__}: {e}")
    return []


def yandex_api_xml(query: str, n: int = 20) -> list[str]:
    """Legacy Yandex XML (GET с apikey/folderid). Запасной путь."""
    key, folder = settings.yandex_search_api_key, settings.yandex_search_folder_id
    if not (key and folder):
        return []
    try:
        r = httpx.get(_XML_GET, params={
            "folderid": folder, "apikey": key, "query": query,
            "l10n": "ru", "sortby": "rlv", "groupby": f"groups-on-page={n}",
        }, timeout=20)
        if r.status_code >= 400:
            return []
        return _urls_from_xml(r.text)
    except Exception:
        return []


def yandex_api_search(query: str, n: int = 20) -> list[str]:
    """Через Yandex Search API: сперва v2, затем legacy-XML. [] если ключ не задан/недоступен."""
    return yandex_api_v2(query, n) or yandex_api_xml(query, n)


def xmlriver_search(query: str, n: int = 20) -> list[str]:
    """Выдача Яндекса через SERP-API XMLRiver/XMLStock (XML, без капчи).

    Возвращает список URL или [] если ключи не заданы/сервис недоступен. Формат ответа —
    Yandex XML (<doc><url>…</url></doc>), парсится тем же _urls_from_xml.
    """
    user, key = settings.xmlriver_user, settings.xmlriver_key
    if not (user and key):
        return []
    try:
        r = httpx.get(settings.xmlriver_url, params={
            "user": user, "key": key, "query": query,
            "groupby": min(n, 100), "lr": settings.xmlriver_lr,
        }, timeout=30)
        if r.status_code >= 400:
            print(f"⚠ XMLRiver: HTTP {r.status_code} — {r.text[:200]}")
            return []
        # сервис кладёт текст ошибки в <error> вместо результатов
        err = re.search(r"<error[^>]*>(.*?)</error>", r.text, re.DOTALL)
        if err:
            print(f"⚠ XMLRiver: {err.group(1)[:200]}")
            return []
        return _urls_from_xml(r.text)
    except Exception as e:
        print(f"⚠ XMLRiver: {type(e).__name__}: {e}")
        return []


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
            if _is_captcha(page):
                if settings.browser_headed:
                    print("⚠ Яндекс показал капчу — решите её в открытом окне браузера "
                          "(ждём до 90 сек)…")
                    for _ in range(45):
                        page.wait_for_timeout(2000)
                        if not _is_captcha(page):
                            break
                else:
                    print("⚠ Яндекс показал капчу в headless-режиме. Cookie устарели/не решены. "
                          "Разово запустите поиск с BROWSER_HEADED=true, решите капчу — cookie "
                          "сохранятся, затем верните BROWSER_HEADED=false.")
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
    """Выдача: XMLRiver/XMLStock (SERP-API, без капчи) → Yandex API → браузерный фолбэк."""
    api = xmlriver_search(query, n) or yandex_api_search(query, n)
    if api:
        return [u for u in api if _host(u) not in _SKIP_HOSTS][:n]
    return browser_search(browser, query, n)
