"""Постоянная база связок «позиция → цены/скриншоты/реквизиты».

Большинство позиций повторяется от спецификации к спецификации, а поиск цен (выдача +
скриншоты + реквизиты) — самый долгий этап. Поэтому результаты `research_position`
складываются в один файл (`engine/data/base/index.json`) и переиспользуются: при обработке
спецификации сперва берём готовое из базы, вживую ищем только промахи.

Матчинг позиции с базой: сперва точное совпадение нормализованного ключа, затем LLM-фолбэк
(DeepSeek выбирает ту же позицию среди похожих по словам кандидатов). Дата в документах всегда
текущая — кэш по возрасту не ограничивается (обновляется вручную кнопкой «Обновить базу»).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date
from pathlib import Path

from ..config import BASE_FILE, CACHE_DIR, settings
from ..llm import complete_json
from ..models import PositionResult, PriceOffer, SpecPosition

_LOCK = threading.Lock()

_MATCH_SYSTEM = (
    "Тебе дан искомый товар и пронумерованный список кандидатов. Верни JSON "
    '{"match": N}, где N — номер кандидата, обозначающего ТОТ ЖЕ товар (та же марка/тип/'
    "типоразмер/исполнение), или 0, если ни один не подходит. Только JSON."
)


def _norm_key(pos: SpecPosition) -> str:
    """Нормализованный ключ позиции: имя|марка|дисциплина (нижний регистр, схлопнутые пробелы)."""
    def n(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    return f"{n(pos.name)}|{n(pos.type_mark)}|{n(pos.discipline)}"


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^\wа-яё]+", (s or "").lower()) if len(t) >= 3}


def _load() -> dict:
    if not BASE_FILE.exists():
        return {"last_refresh": None, "entries": {}}
    try:
        return json.loads(BASE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last_refresh": None, "entries": {}}


def _save(data: dict) -> None:
    tmp = BASE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(BASE_FILE)


def _result_from_entry(entry: dict, pos: SpecPosition, year: str, quarter: str) -> PositionResult:
    """Восстанавливает PositionResult из записи базы (год/квартал — текущие, переданные)."""
    offers = [PriceOffer(**o) for o in entry.get("offers", [])]
    cands = [PriceOffer(**o) for o in entry.get("candidates", [])] or offers
    return PositionResult(
        position=pos, offers=offers, all_offers=cands, year=year, quarter=quarter,
        sources_found=len(offers), sources_checked=len(offers),
        sources_target=settings.min_sources)


def _llm_match_key(pos: SpecPosition, entries: dict) -> str | None:
    """LLM-фолбэк: среди похожих по словам кандидатов выбрать ту же позицию. Ключ или None."""
    target_tokens = _tokens(pos.name)
    if not target_tokens:
        return None
    # префильтр: кандидаты с пересечением слов или вхождением подстроки (дёшево, без LLM)
    scored = []
    for key, e in entries.items():
        ov = len(target_tokens & _tokens(e.get("name", "")))
        nm = (e.get("name") or "").lower()
        if ov >= 2 or (nm and (nm in pos.name.lower() or pos.name.lower() in nm)):
            scored.append((ov, key, e))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    cands = scored[:20]
    listing = "\n".join(
        f"{i+1}. {e.get('name','')} {e.get('type_mark','')}".strip()
        for i, (_, _, e) in enumerate(cands))
    prompt = (f"Искомый товар: {pos.name} {pos.type_mark}".strip()
              + f"\n\nКандидаты:\n{listing}")
    try:
        data = complete_json(prompt, system=_MATCH_SYSTEM)
        n = int(data.get("match", 0))
    except Exception:
        return None
    if 1 <= n <= len(cands):
        return cands[n - 1][1]
    return None


def lookup(pos: SpecPosition, year: str, quarter: str) -> PositionResult | None:
    """Ищет позицию в базе: точное совпадение → LLM-фолбэк. None, если не найдено."""
    data = _load()
    entries = data.get("entries", {})
    if not entries:
        return None
    entry = entries.get(_norm_key(pos))
    if entry is None:
        key = _llm_match_key(pos, entries)
        entry = entries.get(key) if key else None
    if entry is None or not entry.get("offers"):
        return None
    return _result_from_entry(entry, pos, year, quarter)


def upsert(pos: SpecPosition, result: PositionResult) -> None:
    """Сохраняет/обновляет связку позиции в базе (по нормализованному ключу)."""
    if not result.offers:
        return
    with _LOCK:
        data = _load()
        data.setdefault("entries", {})[_norm_key(pos)] = {
            "key": _norm_key(pos),
            "name": pos.name, "type_mark": pos.type_mark,
            "unit": pos.unit, "discipline": pos.discipline,
            "captured_at": date.today().isoformat(),
            "offers": [o.model_dump() for o in result.offers],
            "candidates": [o.model_dump() for o in (result.all_offers or result.offers)],
        }
        _save(data)


def entries() -> list[dict]:
    """Сводка записей базы для UI (без полных офферов)."""
    data = _load()
    out = []
    for e in data.get("entries", {}).values():
        offers = e.get("offers", [])
        prices = [o.get("price_with_vat", 0) for o in offers]
        out.append({
            "key": e.get("key", ""), "name": e.get("name", ""),
            "type_mark": e.get("type_mark", ""), "unit": e.get("unit", ""),
            "discipline": e.get("discipline", ""),
            "offers_count": len(offers),
            "max_price": max(prices) if prices else 0,
            "captured_at": e.get("captured_at", ""),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def status() -> dict:
    data = _load()
    entries = data.get("entries", {})
    # занятое место: индекс + реально используемые базой скриншоты карточек (в CACHE_DIR)
    total = BASE_FILE.stat().st_size if BASE_FILE.exists() else 0
    seen: set[str] = set()
    for e in entries.values():
        for o in e.get("offers", []):
            sp = o.get("screenshot_product")
            if not sp:
                continue
            name = Path(sp).name
            if name in seen:
                continue
            seen.add(name)
            f = CACHE_DIR / name
            if f.exists():
                total += f.stat().st_size
    return {"count": len(entries), "last_refresh": data.get("last_refresh"),
            "disk_bytes": total}


def remove(key: str) -> bool:
    with _LOCK:
        data = _load()
        if key in data.get("entries", {}):
            del data["entries"][key]
            _save(data)
            return True
    return False


def _position_from_entry(e: dict) -> SpecPosition:
    return SpecPosition(number=1, name=e.get("name", ""), type_mark=e.get("type_mark", ""),
                        unit=e.get("unit", "шт") or "шт", discipline=e.get("discipline", "ЭМ"))


def positions_from_xlsx(path: str | Path, default_discipline: str = "ЭМ") -> list[SpecPosition]:
    """Прямой разбор Excel со списком позиций (БЕЗ LLM) для наполнения базы.

    Ищет строку-заголовок и сопоставляет колонки по подстрокам:
    наимен./название→name, тип/марка/обознач→type_mark, ед→unit, дисципл→discipline.
    Если заголовок не найден — берёт первый непустой текстовый столбец как наименование.
    Строки без наименования пропускаются.
    """
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [[("" if c is None else str(c).strip()) for c in r]
            for r in ws.iter_rows(values_only=True)]
    wb.close()
    if not rows:
        return []

    def find_col(row: list[str], *subs: str) -> int:
        for j, c in enumerate(row):
            cl = c.lower()
            if any(s in cl for s in subs):
                return j
        return -1

    # ищем заголовок в первых 10 строках (где есть «наимен»/«название»)
    hdr_idx, col = -1, {}
    for i, row in enumerate(rows[:10]):
        jn = find_col(row, "наимен", "название")
        if jn >= 0:
            hdr_idx = i
            col = {"name": jn,
                   "type_mark": find_col(row, "тип", "марка", "обознач", "артикул"),
                   "unit": find_col(row, "ед"),
                   "discipline": find_col(row, "дисципл", "лист")}
            break

    out: list[SpecPosition] = []
    if hdr_idx >= 0:
        for row in rows[hdr_idx + 1:]:
            name = row[col["name"]] if col["name"] < len(row) else ""
            if not name:
                continue
            def cell(key: str) -> str:
                j = col.get(key, -1)
                return row[j] if 0 <= j < len(row) else ""
            out.append(SpecPosition(
                number=len(out) + 1, name=name,
                type_mark=cell("type_mark"), unit=cell("unit") or "шт",
                discipline=cell("discipline") or default_discipline))
    else:
        # без заголовка: первый столбец с текстом (не числом) считаем наименованием
        for row in rows:
            name = next((c for c in row if c and not c.replace(",", ".").replace(".", "").isdigit()), "")
            if name:
                out.append(SpecPosition(number=len(out) + 1, name=name,
                                        unit="шт", discipline=default_discipline))
    return out


def import_positions(positions: list[SpecPosition], browser_factory, job=None) -> int:
    """Ищет цены по списку позиций и сохраняет блоки в базу. Возвращает число добавленных."""
    from .agent import research_position

    year = str(date.today().year)
    quarter = str((date.today().month - 1) // 3 + 1)
    if job:
        job.say(f"Импорт в базу: {len(positions)} позиций…", 0.05)
    added = 0
    if positions:
        with browser_factory() as browser:
            for i, pos in enumerate(positions, 1):
                if job:
                    job.step = f"Импорт: {pos.name[:40]}"
                n_offers = 0
                try:
                    res = research_position(browser, pos, year, quarter)
                    n_offers = len(res.offers)
                    if res.offers:
                        upsert(pos, res)
                        added += 1
                except Exception as ex:
                    if job:
                        job.say(f"  ✗ {pos.name[:40]}: {type(ex).__name__}")
                if job:
                    job.say(f"  [{i}/{len(positions)}] {pos.name[:40]} → {n_offers} цен",
                            0.05 + 0.9 * i / len(positions))
    if job:
        job.say(f"Импортировано: {added}/{len(positions)}.", 1.0)
    return added


def refresh_all(browser_factory, job=None) -> int:
    """Заново ищет цены по всем записям базы и обновляет их. Возвращает число обновлённых."""
    from .agent import research_position

    data = _load()
    items = list(data.get("entries", {}).values())
    year = str(date.today().year)
    quarter = str((date.today().month - 1) // 3 + 1)
    if job:
        job.say(f"Обновление базы: {len(items)} позиций…", 0.05)
    updated = 0
    if items:
        with browser_factory() as browser:
            for i, e in enumerate(items, 1):
                pos = _position_from_entry(e)
                if job:
                    job.step = f"База: {pos.name[:40]}"
                n_offers = 0
                try:
                    res = research_position(browser, pos, year, quarter)
                    n_offers = len(res.offers)
                    if res.offers:
                        upsert(pos, res)
                        updated += 1
                except Exception as ex:  # одна позиция не валит весь прогон
                    if job:
                        job.say(f"  ✗ {pos.name[:40]}: {type(ex).__name__}")
                if job:
                    job.say(f"  [{i}/{len(items)}] {pos.name[:40]} → {n_offers} цен",
                            0.05 + 0.9 * i / len(items))
    with _LOCK:
        data = _load()
        data["last_refresh"] = date.today().isoformat()
        _save(data)
    if job:
        job.say(f"База обновлена: {updated}/{len(items)}.", 1.0)
    return updated
