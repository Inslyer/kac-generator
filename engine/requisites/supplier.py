"""Реквизиты продавца с его собственного сайта (раздел «Контакты»/«Реквизиты»/подвал).

Альтернатива checko.ru/ФНС, которые блокируют зарубежный IP (VPN). Сайты самих магазинов
под VPN открываются, и продавцы публикуют там свои ИНН/ОГРН/наименование. ИНН со страницы
товара обычно отсутствует — поэтому добираем с типовых страниц реквизитов.

Результат кэшируется по хосту: у одного магазина реквизиты общие для всех позиций.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..llm import complete_json
from ..models import OrgStatus, Requisites

_REQ_SYSTEM = (
    "Из текста страниц интернет-магазина извлеки реквизиты юрлица-продавца (не производителя "
    "товара, а самого магазина). Верни JSON: {name: полное наименование (ООО/АО/ИП…), "
    "inn: ИНН (10 или 12 цифр), kpp: КПП, ogrn: ОГРН или ОГРНИП, address: юридический адрес, "
    "city: город}. Поле, которого нет в тексте, — пустая строка. Только JSON."
)

# Типовые пути к странице с реквизитами (по убыванию вероятности). Пустой путь — главная (подвал).
_REQ_PATHS = ["", "/contacts", "/kontakty", "/about", "/info",
              "/oplata-i-dostavka", "/rekvizity", "/requisites", "/payment"]

# Похоже на «ИНН 7701234567» — признак, что страница содержит реквизиты.
_INN_RE = re.compile(r"\bИНН[\s:]*\d{10,12}\b", re.IGNORECASE)

_cache: dict[str, Requisites | None] = {}


def fetch_requisites_from_site(browser, product_url: str) -> Requisites | None:
    """Ищет реквизиты продавца на его сайте. None, если ИНН найти не удалось.

    Обходит несколько типовых страниц, собирая «подвальный» текст; как только встречает
    шаблон ИНН — отдаёт собранное в LLM. Не делает больше 4 успешных загрузок на хост.
    """
    p = urlparse(product_url)
    if not p.netloc:
        return None
    host = p.netloc.replace("www.", "")
    if host in _cache:
        return _cache[host]

    base = f"{p.scheme}://{p.netloc}"
    texts: list[str] = []
    inn_seen = False
    loaded = 0
    for path in _REQ_PATHS:
        if loaded >= 4 or (inn_seen and texts):
            break
        try:
            with browser.page(base + path) as page:
                page.wait_for_timeout(700)
                body = page.inner_text("body")
        except Exception:
            continue
        loaded += 1
        texts.append(body[-3500:])      # реквизиты обычно в подвале страницы
        if _INN_RE.search(body):
            inn_seen = True

    if not texts:
        _cache[host] = None
        return None

    combined = "\n---СТРАНИЦА---\n".join(texts)[:13000]
    try:
        data = complete_json(f"Магазин: {host}\n\n{combined}", system=_REQ_SYSTEM, smart=True)
    except Exception:
        _cache[host] = None
        return None

    inn = re.sub(r"\D", "", str(data.get("inn") or ""))
    if len(inn) not in (10, 12):
        _cache[host] = None
        return None

    req = Requisites(
        name=str(data.get("name") or "").strip(),
        inn=inn,
        kpp=re.sub(r"\D", "", str(data.get("kpp") or "")),
        ogrn=re.sub(r"\D", "", str(data.get("ogrn") or "")),
        address=str(data.get("address") or "").strip(),
        city=str(data.get("city") or "").strip() or "Москва",
        status=OrgStatus.SUPPLIER,
    )
    _cache[host] = req
    return req
