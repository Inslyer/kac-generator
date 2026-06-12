"""Сквозной демо-прогон пайплайна на синтетических данных (без сети).

Запуск:  python -m engine.demo_run   (из корня репозитория)
Создаёт в engine/data/output: KAC_demo.xlsx, TKP_demo.pdf, TSN_demo.xlsx
и рендерит превью-PNG в /tmp для визуальной проверки.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.builders.kac_xlsx import build_kac
from engine.builders.tkp_pdf import build_tkp
from engine.builders.tsn_xlsx import build_tsn
from engine.config import OUTPUT_DIR
from engine.models import (OrgStatus, PositionResult, PriceOffer, Requisites,
                           SpecPosition, TsnRow)

OBJ = "ОФИСНО-ДЕЛОВОЕ ЗДАНИЕ (демонстрационный пример), г. Москва"

PROD_SHOT = "/tmp/tkp_p5.png"      # заглушка-скриншот карточки
REQ_SHOT = "/tmp/spec_p2.png"      # заглушка-скриншот реквизитов


def _offer(price, org, inn, kpp, with_shots=True):
    return PriceOffer(
        price_with_vat=price,
        url=f"https://shop.example.ru/{inn}",
        requisites=Requisites(name=org, inn=inn, kpp=kpp, city="Москва",
                              status=OrgStatus.SUPPLIER),
        screenshot_product=PROD_SHOT if with_shots else None,
        screenshot_requisites=REQ_SHOT if with_shots else None,
    )


def build_demo_results() -> list[PositionResult]:
    r1 = PositionResult(
        position=SpecPosition(number=1, name="Счетчик электрической энергии трехфазный", unit="шт"),
        offers=[_offer(22028, "ООО «Альфа»", "7700000001", "770001001"),
                _offer(22073.74, "ООО «Бета»", "7700000002", "770001001"),
                _offer(24033, "ООО «Гамма»", "7700000003", "770001001")],
        year="2026", quarter="1")
    r2 = PositionResult(
        position=SpecPosition(number=2, name="Кабель силовой ППГнг(А)-HF 1х95", unit="м"),
        offers=[_offer(2361.47, "ООО «Дельта»", "7700000004", "770001001"),
                _offer(2460, "АО «Эпсилон»", "7700000005", "770001001"),
                _offer(2677.96, "ООО «Дзета»", "7700000006", "770001001")],
        year="2026", quarter="1")
    # уникальное оборудование из письма: 1 строка, не попадает в ТКП
    r3 = PositionResult(
        position=SpecPosition(number=3, name="Щит распределительный (спецзаказ)", is_unique=True),
        offers=[PriceOffer(price_with_vat=1167443.13, from_letter=True,
                           requisites=Requisites(name="ООО «Спецзавод»", inn="7700000099",
                                                 kpp="770001001"))],
        year="2025", quarter="3")
    return [r1, r2, r3]


def main() -> None:
    results = build_demo_results()

    # 1. Том ТКП (проставляет tkp_page; письма исключаются)
    tkp_path, page_map = build_tkp(results, OBJ, OUTPUT_DIR / "TKP_demo.pdf")
    print("ТКП:", tkp_path, "| страницы позиций:", page_map)

    # 2. КАЦ (использует tkp_page из шага 1)
    kac_path = build_kac({"ЭМ": results}, OBJ, OUTPUT_DIR / "KAC_demo.xlsx")
    print("КАЦ:", kac_path)

    # 3. ТСН-2001: демо-расценки. Поз.2 — ТСН выше КАЦ → исключаем из КАЦ.
    tsn_rows = [
        TsnRow(number=1, name=results[0].position.name, unit="шт", qty=10,
               tsn_base_price=300.0, coefficient=58.2,
               kac_max_price=results[0].max_offer.price_with_vat),
        TsnRow(number=2, name=results[1].position.name, unit="м", qty=500,
               tsn_base_price=70.0, coefficient=58.2,  # 70*58.2=4074 > 2677 → исключить
               kac_max_price=results[1].max_offer.price_with_vat),
    ]
    tsn_path, excluded = build_tsn(tsn_rows, OBJ, OUTPUT_DIR / "TSN_demo.xlsx")
    print("ТСН:", tsn_path, "| исключены (ТСН>КАЦ):", excluded)

    # применяем исключения к КАЦ и пересобираем
    for res in results:
        if res.position.number in excluded:
            res.excluded_by_tsn = True
    build_kac({"ЭМ": results}, OBJ, kac_path)
    print("КАЦ пересобран с учётом фильтра ТСН")

    # превью
    import fitz
    d = fitz.open(tkp_path)
    d[min(1, d.page_count - 1)].get_pixmap(dpi=110).save("/tmp/demo_tkp_page.png")
    print("превью ТКП → /tmp/demo_tkp_page.png")


if __name__ == "__main__":
    main()
