"""Доверенные поставщики из Поставщики.md — приоритет в поиске цен и готовые реквизиты.

Поиск цен сначала идёт по сайтам из этого списка (через site:-ограниченную выдачу), и только
если у них ничего не нашлось — по общему интернету. Для офферов с этих сайтов реквизиты
(наименование/ИНН/КПП) берутся прямо из файла, без выскребания со страницы.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..config import SUPPLIERS_FILE
from ..models import OrgStatus, Requisites


def _host(url: str) -> str:
    netloc = urlparse(url if "//" in url else "//" + url).netloc
    return netloc.replace("www.", "").lower()


def _load() -> dict[str, dict]:
    """Парсит таблицу Поставщики.md → {host: {name, kpp, inn, url}}."""
    out: dict[str, dict] = {}
    if not SUPPLIERS_FILE.exists():
        return out
    for line in SUPPLIERS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        name, kpp, inn, url = cells[0], cells[1], cells[2], cells[3]
        # пропускаем шапку и разделитель
        if not re.search(r"https?://", url) or not inn.isdigit():
            continue
        host = _host(url)
        if host:
            out[host] = {"name": name, "kpp": kpp, "inn": inn, "url": url}
    return out


_BY_HOST = _load()


def whitelist_hosts() -> list[str]:
    return list(_BY_HOST)


def is_whitelisted(url: str) -> bool:
    return _host(url) in _BY_HOST


def requisites_for(url: str) -> Requisites | None:
    """Готовые реквизиты поставщика из файла по хосту URL (или None, если не наш)."""
    rec = _BY_HOST.get(_host(url))
    if not rec:
        return None
    return Requisites(name=rec["name"], inn=rec["inn"], kpp=rec["kpp"],
                      city="Москва", status=OrgStatus.SUPPLIER)
