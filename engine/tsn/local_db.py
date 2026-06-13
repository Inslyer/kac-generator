"""Локальный источник ТСН-2001: справочник расценок и индексы пересчёта из MD-файлов.

Замена нестабильному mos_ru.py (mos.ru недоступен с зарубежного IP/VPN). Базовые расценки
берутся из «ТСН-2001_электро_автоматика.md», коэффициент пересчёта — из «индексы.md».

Оба файла редактируются человеком (индексы обновляются раз в квартал из приказа
Москомэкспертизы), поэтому парсеры устойчивы к отсутствию данных: при неудаче возвращают None,
и сметчик вводит значение вручную через UI.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..config import TSN_INDICES_FILE, TSN_REFERENCE_FILE
from ..llm import complete_json
from ..models import TsnRow

# Сопоставление вида работ → подстрока в наименовании строки таблицы индексов.
INDEX_WORK_KINDS: dict[str, str] = {
    "электромонтаж": "электромонтажные",
    "связь": "связи",
    "автоматика": "приборов и автоматики",
    "пнр_электро": "пусконаладочные работы — электро",
    "пнр_асу": "пусконаладочные работы — асу",
}

_MATCH_SYSTEM = (
    "Тебе дан фрагмент справочника ТСН-2001 (расценки в ценах 2000 г.) и наименование ресурса "
    "из спецификации. Найди наиболее подходящую расценку. Верни JSON: "
    "{code: строка-шифр расценки, name: строка-наименование расценки, "
    "base_price: число (прямые затраты в руб., ценах 2000 г.), found: bool}. "
    "Если подходящей расценки во фрагменте нет — {found:false}. Только JSON."
)


@lru_cache(maxsize=1)
def _reference_text() -> str:
    """Содержимое справочника расценок (кэшируется на процесс)."""
    try:
        return TSN_REFERENCE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


@lru_cache(maxsize=1)
def load_indices() -> dict[str, float]:
    """Парсит таблицу значений из индексы.md → {вид работ: коэффициент}.

    Распознаёт строки markdown-таблицы вида «| Название | 9.18 | ... |». Ключи —
    из INDEX_WORK_KINDS плюс «материалы»/«оборудование». Строки без числа (заглушки
    «_уточнить_») пропускаются.
    """
    result: dict[str, float] = {}
    try:
        text = TSN_INDICES_FILE.read_text(encoding="utf-8")
    except OSError:
        return result

    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = cells[0].lower()
        # ищем первое число в любой ячейке строки (формат 9.18 или 9,18)
        value: float | None = None
        for c in cells[1:]:
            m = re.search(r"(\d+[.,]\d+)", c)
            if m:
                value = float(m.group(1).replace(",", "."))
                break
        if value is None:
            continue
        if "материал" in label:
            result["материалы"] = value
        elif "оборудован" in label:
            result["оборудование"] = value
        else:
            for key, needle in INDEX_WORK_KINDS.items():
                if needle.split("—")[0].strip() in label:
                    result[key] = value
                    break
    return result


def get_coefficient(work_kind: str = "электромонтаж") -> float | None:
    """Коэффициент пересчёта для вида работ. None, если в индексы.md значение не заполнено."""
    indices = load_indices()
    return indices.get(work_kind) or indices.get("оборудование")


def _relevant_fragment(name: str, max_chars: int = 14000) -> str:
    """Грубо отбирает куски справочника по ключевым словам наименования.

    Справочник ~1 МБ — целиком в LLM не помещается. Берём строки-заголовки таблиц и расценок,
    содержащие значимые слова из наименования ресурса, плюс соседний контекст.
    """
    text = _reference_text()
    if not text:
        return ""
    words = [w.lower() for w in re.findall(r"[А-Яа-яA-Za-z]{4,}", name)]
    if not words:
        return text[:max_chars]
    lines = text.splitlines()
    picked: list[str] = []
    for i, line in enumerate(lines):
        low = line.lower()
        if any(w in low for w in words):
            lo, hi = max(0, i - 1), min(len(lines), i + 3)
            picked.extend(lines[lo:hi])
            if sum(len(p) for p in picked) > max_chars:
                break
    return "\n".join(picked)[:max_chars] if picked else text[:max_chars]


def lookup_tsn_price(name: str) -> dict | None:
    """Ищет базовую расценку ТСН-2001 по наименованию ресурса в локальном справочнике."""
    fragment = _relevant_fragment(name)
    if not fragment:
        return None
    try:
        data = complete_json(
            f"Наименование: {name}\n\nФрагмент справочника ТСН-2001:\n\n{fragment}",
            system=_MATCH_SYSTEM, smart=True,
        )
    except Exception:
        return None
    return data if data.get("found") else None


def build_tsn_rows(results, work_kind: str = "электромонтаж") -> list[TsnRow]:
    """Собирает строки ТСН из локального справочника (базовая расценка + коэффициент + цена КАЦ).

    results — список PositionResult. Совместима по сигнатуре с mos_ru.build_tsn_rows,
    но не требует браузера и доступа к mos.ru.
    """
    coeff = get_coefficient(work_kind)
    rows: list[TsnRow] = []
    for res in results:
        pos = res.position
        tsn = lookup_tsn_price(pos.name) if not pos.is_unique else None
        rows.append(TsnRow(
            number=pos.number,
            name=pos.name,
            unit=pos.unit,
            qty=pos.qty,
            tsn_base_price=(tsn or {}).get("base_price"),
            coefficient=coeff,
            kac_max_price=res.max_offer.price_with_vat if res.max_offer else None,
        ))
    return rows
