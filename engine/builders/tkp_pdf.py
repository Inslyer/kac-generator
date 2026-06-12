"""Сборщик Тома ТКП (PDF) в формате эталонного образца.

Каждая страница соответствует одной искомой позиции и содержит до 3 строк
(по числу ценовых предложений). Строка = пара скриншотов:
  слева  — карточка товара с сайта-магазина,
  справа — панель реквизитов продавца (ОГРН/ИНН/КПП).

Шапка справа сверху: «УТВЕРЖДАЮ:», линии для подписи, «М.П.», «Дата фиксации: ДД.ММ.ГГГГ».
Подвал слева снизу: «Цена указана включая НДС {vat}%».
Номер страницы проставляется в графу Q соответствующей позиции КАЦ.

Позиции из писем (уникальное оборудование) в Том ТКП НЕ включаются.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF

from ..config import settings
from ..models import PositionResult

A4 = fitz.paper_rect("a4")  # 595 x 842 pt
MARGIN = 28
HEADER_H = 96
FOOTER_H = 24
FONT = "cyr"  # имя встроенного кириллического шрифта
FONT_FILE = str(Path(__file__).resolve().parent.parent / "templates" / "fonts" / "Ubuntu-Regular.ttf")


_FONT_OBJ = fitz.Font(fontfile=FONT_FILE)


def _register_font(page: fitz.Page) -> None:
    page.insert_font(fontname=FONT, fontfile=FONT_FILE)


def _text_len(s: str, size: float) -> float:
    return _FONT_OBJ.text_length(s, fontsize=size)


def _draw_header(page: fitz.Page, fix_date: str) -> None:
    right_x = A4.width - MARGIN
    y = MARGIN
    lines_right = ["УТВЕРЖДАЮ:", "______________________________", "______________________________"]
    page.insert_text((right_x - 200, y + 10), "М.П.", fontname=FONT, fontsize=9)
    ty = y
    for ln in lines_right:
        tw = _text_len(ln, 9)
        page.insert_text((right_x - tw, ty + 10), ln, fontname=FONT, fontsize=9)
        ty += 14
    page.insert_text((right_x - 170, ty + 16), f"Дата фиксации: {fix_date}",
                     fontname=FONT, fontsize=9)


def _draw_footer(page: fitz.Page) -> None:
    page.insert_text((MARGIN, A4.height - 12),
                     f"Цена указана включая НДС {settings.vat_rate}%",
                     fontname=FONT, fontsize=9)


def _place_image(page: fitz.Page, rect: fitz.Rect, img_path: str | None, label: str) -> None:
    if img_path and Path(img_path).exists():
        page.insert_image(rect, filename=img_path, keep_proportion=True)
        page.draw_rect(rect, color=(0.8, 0.8, 0.8), width=0.5)
    else:
        # плейсхолдер, если скриншот не получен
        page.draw_rect(rect, color=(0.7, 0.7, 0.7), width=0.5)
        page.insert_text((rect.x0 + 6, rect.y0 + 16), f"[{label}: скриншот не получен]",
                         fontname=FONT, fontsize=8, color=(0.5, 0.5, 0.5))


def _draw_position_page(doc: fitz.Document, res: PositionResult, fix_date: str) -> None:
    page = doc.new_page(width=A4.width, height=A4.height)
    _register_font(page)
    _draw_header(page, fix_date)
    _draw_footer(page)

    top = MARGIN + HEADER_H
    bottom = A4.height - FOOTER_H - MARGIN
    avail_h = bottom - top
    offers = [o for o in res.offers if not o.from_letter]
    n = max(1, len(offers))
    row_h = avail_h / 3  # макет всегда на 3 строки
    gap = 8
    split_x = A4.width * 0.6  # слева карточка (60%), справа реквизиты (40%)

    for k in range(n):
        ry0 = top + k * row_h
        ry1 = ry0 + row_h - gap
        left = fitz.Rect(MARGIN, ry0, split_x - gap / 2, ry1)
        right = fitz.Rect(split_x + gap / 2, ry0, A4.width - MARGIN, ry1)
        offer = offers[k] if k < len(offers) else None
        _place_image(page, left, offer.screenshot_product if offer else None, "карточка товара")
        _place_image(page, right, offer.screenshot_requisites if offer else None, "реквизиты")


def _draw_cover(doc: fitz.Document, object_name: str, fix_date: str) -> None:
    page = doc.new_page(width=A4.width, height=A4.height)
    _register_font(page)
    cx = A4.width / 2
    page.insert_text((cx - 120, 200), "ТОМ", fontname=FONT, fontsize=22)
    page.insert_text((cx - 230, 240),
                     "Технико-коммерческие предложения (ТКП)", fontname=FONT, fontsize=14)
    # наименование объекта (перенос вручную)
    y = 300
    for chunk in _wrap(object_name, 70):
        page.insert_text((MARGIN, y), chunk, fontname=FONT, fontsize=11)
        y += 16
    page.insert_text((MARGIN, y + 30), f"Дата фиксации: {fix_date}", fontname=FONT, fontsize=11)
    page.insert_text((MARGIN, A4.height - 40),
                     f"Цена указана включая НДС {settings.vat_rate}%", fontname=FONT, fontsize=10)


def _wrap(text: str, width: int) -> list[str]:
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def build_tkp(
    results: Iterable[PositionResult],
    object_name: str,
    out_path: str | Path,
    fix_date: str | None = None,
) -> tuple[Path, dict[int, int]]:
    """Собирает Том ТКП. Возвращает (путь, отображение № позиции → № страницы в томе).

    Каждой искомой позиции (есть онлайн-офферы) выделяется одна страница.
    Номер страницы (1-based, без титула) записывается в res.tkp_page и возвращается в маппинге.
    """
    fix_date = fix_date or date.today().strftime("%d.%m.%Y")
    doc = fitz.open()
    _draw_cover(doc, object_name, fix_date)

    page_no = 0
    page_map: dict[int, int] = {}
    for res in results:
        online = [o for o in res.offers if not o.from_letter]
        if not online:  # позиции из писем не попадают в ТКП
            res.tkp_page = None
            continue
        page_no += 1
        res.tkp_page = page_no
        page_map[res.position.number] = page_no
        _draw_position_page(doc, res, fix_date)

    out_path = Path(out_path)
    doc.save(out_path, deflate=True)
    doc.close()
    return out_path, page_map
