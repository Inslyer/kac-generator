"""Оркестрация полного пайплайна конъюнктурного анализа.

Этапы:
  1. Парсинг спецификаций → позиции; парсинг писем → офферы уникального оборудования.
  2. Поиск цен по каждой неуникальной позиции (≥MIN_SOURCES → TOP максимальных).
  3. Сборка Тома ТКП (проставляет № страниц = графа Q).
  4. Сборка КАЦ.
  5. ТСН-2001: расценки + коэффициент → файл ТСН + фильтр (ТСН>КАЦ → исключить).
  6. Пересборка КАЦ с учётом фильтра.

Состояние задачи (progress/лог) хранится в JobState для отдачи в UI.
"""
from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .builders.kac_xlsx import build_kac
from .builders.tkp_pdf import build_tkp
from .builders.tsn_xlsx import build_tsn
from .config import OUTPUT_DIR
from .models import PositionResult


@dataclass
class JobState:
    job_id: str
    object_name: str
    status: str = "pending"          # pending|running|done|error
    step: str = ""
    progress: float = 0.0            # 0..1
    log: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)  # kind -> path
    error: str = ""

    def say(self, msg: str, progress: float | None = None) -> None:
        self.log.append(msg)
        if progress is not None:
            self.progress = progress


@dataclass
class JobInputs:
    object_name: str
    spec_results: list[PositionResult]     # уже распарсенные позиции (+ офферы писем)
    use_tsn: bool = True
    fix_date: str = ""
    skip_search: bool = False              # не искать в интернете (цены заданы вручную)
    tsn_work_kind: str = "электромонтаж"   # вид работ для выбора индекса пересчёта (см. local_db)
    # ручные данные ТСН по № позиции: {number: (base_price, coefficient)}
    manual_tsn: dict[int, tuple[float | None, float | None]] = field(default_factory=dict)


def run_pipeline(job: JobState, inputs: JobInputs, browser_factory) -> None:
    """Синхронный прогон. browser_factory() → контекстный менеджер Browser (или None для демо)."""
    from .search.agent import research_position
    from .search import base_db
    from .tsn.local_db import build_tsn_rows

    def _apply(res, found):
        res.offers = found.offers
        res.year, res.quarter = year, quarter
        res.sources_checked = found.sources_checked
        res.sources_found = found.sources_found
        res.sources_target = found.sources_target

    try:
        job.status = "running"
        results = inputs.spec_results
        year = str(date.today().year)
        quarter = str((date.today().month - 1) // 3 + 1)

        # 2. Поиск цен (неуникальные позиции без офферов)
        to_search = [] if inputs.skip_search else [
            r for r in results if not r.position.is_unique and not r.offers]
        if inputs.skip_search:
            job.say("Поиск пропущен (цены заданы вручную).", 0.6)
        if to_search:
            job.say(f"Поиск цен по {len(to_search)} позициям…", 0.1)
            # cache-first: сперва берём готовое из базы (без браузера), вживую ищем только промахи
            misses = []
            for res in to_search:
                hit = base_db.lookup(res.position, year, quarter)
                if hit:
                    _apply(res, hit)
                    job.say(f"  из базы: {res.position.name[:40]} ({len(hit.offers)} цен)", 0.1)
                else:
                    misses.append(res)
            job.say(f"Из базы: {len(to_search) - len(misses)}, искать вживую: {len(misses)}", 0.15)
            if misses:
                with browser_factory() as browser:
                    for i, res in enumerate(misses, 1):
                        job.step = f"Поиск: {res.position.name[:40]}"
                        found = research_position(browser, res.position, year, quarter)
                        _apply(res, found)
                        base_db.upsert(res.position, found)  # пополняем базу
                        flag = "" if found.sources_found >= found.sources_target else "  ⚠ мало источников"
                        job.say(f"  [{i}/{len(misses)}] найдено {found.sources_found}/"
                                f"{found.sources_target} ист., в КАЦ {len(res.offers)} цен{flag}",
                                0.15 + 0.45 * i / len(misses))

        # 3. Том ТКП
        job.say("Сборка Тома ТКП…", 0.65)
        tkp_path, _ = build_tkp(results, inputs.object_name, OUTPUT_DIR / f"{job.job_id}_TKP.pdf",
                                fix_date=inputs.fix_date or None)
        job.outputs["tkp"] = str(tkp_path)

        # 4. КАЦ
        job.say("Сборка КАЦ…", 0.75)
        by_disc: dict[str, list[PositionResult]] = {}
        for r in results:
            by_disc.setdefault(r.position.discipline, []).append(r)
        kac_path = build_kac(by_disc, inputs.object_name, OUTPUT_DIR / f"{job.job_id}_KAC.xlsx")
        job.outputs["kac"] = str(kac_path)

        # 5. ТСН-2001 + фильтр
        if inputs.use_tsn:
            manual = inputs.manual_tsn or {}
            have_manual = any(manual.get(r.position.number, (None, None))[0] is not None
                              for r in results)
            if have_manual or inputs.skip_search:
                # ручные базовые расценки/коэффициенты (без обращения к mos.ru)
                from .models import TsnRow
                tsn_rows = []
                for r in results:
                    base, coeff = manual.get(r.position.number, (None, None))
                    tsn_rows.append(TsnRow(
                        number=r.position.number, name=r.position.name,
                        unit=r.position.unit, qty=r.position.qty,
                        tsn_base_price=base, coefficient=coeff,
                        kac_max_price=r.max_offer.price_with_vat if r.max_offer else None))
                job.say("Сравнение с ТСН-2001 (ручные расценки)…", 0.85)
            else:
                job.say("Сравнение с ТСН-2001 (локальный справочник)…", 0.85)
                tsn_rows = build_tsn_rows(results, inputs.tsn_work_kind)
            tsn_path, excluded = build_tsn(tsn_rows, inputs.object_name,
                                           OUTPUT_DIR / f"{job.job_id}_TSN.xlsx")
            job.outputs["tsn"] = str(tsn_path)
            if excluded:
                job.say(f"Исключены из КАЦ (ТСН>КАЦ): {sorted(excluded)}", 0.92)
                for r in results:
                    if r.position.number in excluded:
                        r.excluded_by_tsn = True
                build_kac(by_disc, inputs.object_name, kac_path)  # пересборка

        job.step = ""
        job.say("Готово.", 1.0)
        job.status = "done"
    except Exception as e:  # pragma: no cover
        job.status = "error"
        job.error = f"{e}\n{traceback.format_exc()}"
        job.say(f"Ошибка: {e}")


def run_pipeline_async(job: JobState, inputs: JobInputs, browser_factory) -> threading.Thread:
    t = threading.Thread(target=run_pipeline, args=(job, inputs, browser_factory), daemon=True)
    t.start()
    return t
