"""Доменные модели, общие для всех модулей движка."""
from __future__ import annotations

from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field


class OrgStatus(IntEnum):
    MANUFACTURER = 1  # Производитель
    SUPPLIER = 2      # Поставщик


class SpecPosition(BaseModel):
    """Позиция спецификации (вход)."""
    number: int                      # № п.п. (графа A)
    name: str                        # наименование строительного ресурса (графа C)
    full_name: str = ""              # полное наименование (графа D); по умолчанию = name
    type_mark: str = ""              # тип, марка, обозначение
    unit: str = "шт"                 # ед. изм. (графа E)
    qty: float = 0.0                 # количество (для ТСН)
    is_unique: bool = False          # уникальное оборуд. из письма (цена не ищется в интернете)
    discipline: str = "ЭМ"           # дисциплина/лист: ЭМ | АК

    def model_post_init(self, _ctx) -> None:
        if not self.full_name:
            self.full_name = self.name


class Requisites(BaseModel):
    """Реквизиты организации-продавца (для граф K, L, M, O)."""
    name: str = ""                   # наименование (графа K)
    inn: str = ""                    # ИНН (графа M)
    kpp: str = ""                    # КПП (графа L)
    city: str = "Москва"             # населённый пункт склада (графа O)
    status: OrgStatus = OrgStatus.SUPPLIER  # графа P


class PriceOffer(BaseModel):
    """Одно ценовое предложение по позиции (одна строка КАЦ + один скриншот ТКП)."""
    price_with_vat: float            # графа G — цена С НДС
    url: str = ""                    # графа N — ссылка на карточку
    product_title: str = ""          # как называется товар на сайте
    in_moscow_region: bool = True    # самовывоз Москва/МО
    requisites: Requisites = Field(default_factory=Requisites)
    # пути к скриншотам (карточка + панель реквизитов) для сборки страницы ТКП
    screenshot_product: Optional[str] = None
    screenshot_requisites: Optional[str] = None
    # для позиций из писем: цена задана напрямую, в ТКП не попадает
    from_letter: bool = False

    def price_without_vat(self, divisor: float) -> float:
        """Графа H = G / divisor (НДС)."""
        return self.price_with_vat / divisor


class PositionResult(BaseModel):
    """Итог по позиции: до 3 ценовых предложений + метаданные периода."""
    position: SpecPosition
    offers: list[PriceOffer] = Field(default_factory=list)  # отсортированы по возрастанию цены
    year: str = ""                   # графа I
    quarter: str = ""                # графа J
    tkp_page: Optional[int] = None   # графа Q — № страницы в Томе ТКП
    excluded_by_tsn: bool = False    # ТСН-2001 > КАЦ → исключить из КАЦ
    # статистика поиска (для индикатора «найдено N из target»)
    sources_checked: int = 0         # сколько карточек обойдено
    sources_found: int = 0           # сколько валидных офферов (Москва/МО) найдено
    sources_target: int = 0          # цель (MIN_SOURCES)

    @property
    def max_offer(self) -> Optional[PriceOffer]:
        return self.offers[-1] if self.offers else None


class TsnRow(BaseModel):
    """Строка файла сравнения с ТСН-2001."""
    number: int
    name: str
    unit: str
    qty: float
    tsn_base_price: Optional[float] = None     # базовая расценка ТСН-2001
    coefficient: Optional[float] = None        # коэффициент пересчёта (mos.ru)
    tsn_current_price: Optional[float] = None  # расценка в текущих ценах
    kac_max_price: Optional[float] = None      # макс. цена из КАЦ
    ratio_percent: Optional[float] = None      # КАЦ / ТСН, %
    decision: str = ""                         # "включить" | "исключить (ТСН>КАЦ)"
