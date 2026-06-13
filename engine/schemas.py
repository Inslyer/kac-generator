"""Схемы запросов/ответов API (тело JSON между UI и движком)."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .config import CACHE_DIR
from .models import (OrgStatus, PositionResult, PriceOffer, Requisites,
                     SpecPosition)


def _resolve_shot(name: str) -> str | None:
    """Имя файла из кэша → абсолютный путь (или None)."""
    if not name:
        return None
    p = CACHE_DIR / Path(name).name
    return str(p) if p.exists() else None


class OfferIn(BaseModel):
    """Оффер, заданный/отредактированный вручную в UI."""
    price_with_vat: float
    url: str = ""
    org_name: str = ""
    inn: str = ""
    kpp: str = ""
    city: str = "Москва"
    status: int = 2
    from_letter: bool = False
    # имена файлов скриншотов в кэше движка (отдаются через GET /cache/{name})
    screenshot_product: str = ""
    screenshot_requisites: str = ""


class PositionIn(BaseModel):
    """Позиция в редактируемой таблице UI."""
    number: int
    name: str
    full_name: str = ""
    type_mark: str = ""
    unit: str = "шт"
    qty: float = 0.0
    is_unique: bool = False
    discipline: str = "ЭМ"
    offers: list[OfferIn] = Field(default_factory=list)
    # ручные данные ТСН (если сметчик вводит сам)
    tsn_base_price: float | None = None
    tsn_coefficient: float | None = None

    def to_result(self) -> PositionResult:
        pos = SpecPosition(
            number=self.number, name=self.name, full_name=self.full_name,
            type_mark=self.type_mark, unit=self.unit, qty=self.qty,
            is_unique=self.is_unique, discipline=self.discipline,
        )
        offers = [
            PriceOffer(
                price_with_vat=o.price_with_vat, url=o.url, from_letter=o.from_letter,
                requisites=Requisites(name=o.org_name, inn=o.inn, kpp=o.kpp,
                                      city=o.city, status=OrgStatus(o.status)),
                screenshot_product=_resolve_shot(o.screenshot_product),
                screenshot_requisites=_resolve_shot(o.screenshot_requisites),
            )
            for o in sorted(self.offers, key=lambda x: x.price_with_vat)
        ]
        return PositionResult(position=pos, offers=offers)


class ParseResponse(BaseModel):
    positions: list[PositionIn]


class BuildRequest(BaseModel):
    object_name: str
    fix_date: str = ""
    use_tsn: bool = True
    skip_search: bool = False           # не искать в интернете (цены заданы вручную)
    tsn_work_kind: str = "электромонтаж"  # вид работ для индекса пересчёта ТСН
    positions: list[PositionIn]
