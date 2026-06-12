"""Локальный движок (FastAPI). UI на GitHub Pages обращается сюда на http://127.0.0.1:8765.

Поток из двух шагов:
  POST /parse   — загрузка спецификаций + писем → список позиций (для правки в UI)
  POST /build   — отредактированные позиции (JSON) → запуск пайплайна → 3 файла

Прочее:
  GET  /health
  GET  /jobs/{id}               — статус/прогресс/лог
  GET  /jobs/{id}/download/{k}  — скачать результат (k = kac|tkp|tsn)
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from datetime import date

from pydantic import BaseModel

from .browser import Browser
from .config import CACHE_DIR, UPLOAD_DIR, settings
from .llm import LLMUnavailable
from .models import PositionResult, SpecPosition
from .parsers.letters import letter_to_offer, parse_letter
from .parsers.spec_pdf import parse_spec
from .pipeline import JobInputs, JobState, run_pipeline_async
from .schemas import BuildRequest, OfferIn, ParseResponse, PositionIn

app = FastAPI(title="КАЦ-генератор (локальный движок)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # локально; UI с GitHub Pages
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: dict[str, JobState] = {}


def _browser_factory():
    return Browser(headed=settings.browser_headed)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "anthropic_key": bool(settings.anthropic_api_key),
        "dadata_token": bool(settings.dadata_token),
        "min_sources": settings.min_sources,
        "top_prices": settings.top_prices,
        "vat_rate": settings.vat_rate,
    }


def _similar(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return bool(a) and bool(b) and (a in b or b in a or len(set(a.split()) & set(b.split())) >= 2)


@app.post("/parse", response_model=ParseResponse)
async def parse(
    object_name: str = Form(""),
    discipline: str = Form("ЭМ"),
    unique_names: str = Form(""),
    specs: list[UploadFile] = (),
    letters: list[UploadFile] = (),
) -> ParseResponse:
    """Парсит спецификации и письма → список позиций для правки в UI."""
    job_dir = UPLOAD_DIR / f"parse_{uuid.uuid4().hex[:8]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    uniq = {s.strip() for s in unique_names.split(";") if s.strip()}

    positions: list[PositionIn] = []
    try:
        for f in specs or []:
            path = job_dir / f.filename
            path.write_bytes(await f.read())
            for p in parse_spec(path, discipline=discipline, unique_names=uniq):
                positions.append(PositionIn(
                    number=p.number, name=p.name, full_name=p.full_name,
                    type_mark=p.type_mark, unit=p.unit, qty=p.qty,
                    is_unique=p.is_unique, discipline=discipline))

        # письма → офферы уникального оборудования (привязка по наименованию)
        for f in letters or []:
            path = job_dir / f.filename
            path.write_bytes(await f.read())
            for item in parse_letter(path):
                name = str(item.get("name", ""))
                offer = OfferIn(
                    price_with_vat=float(item.get("price_with_vat") or 0),
                    org_name=str(item.get("supplier_name", "")),
                    inn=str(item.get("supplier_inn", "")),
                    kpp=str(item.get("supplier_kpp", "")),
                    from_letter=True)
                target = next((p for p in positions if p.is_unique and _similar(p.name, name)), None)
                if target is None:
                    target = PositionIn(number=len(positions) + 1, name=name or "Уникальное",
                                        is_unique=True, discipline=discipline)
                    positions.append(target)
                target.offers = [offer]
    except LLMUnavailable as e:
        raise HTTPException(400, f"Автоматический разбор недоступен: {e}. "
                                 "Заполните позиции вручную в таблице.")
    return ParseResponse(positions=positions)


@app.post("/build")
def build(req: BuildRequest) -> dict:
    """Запускает сборку по (отредактированным) позициям из UI."""
    job_id = uuid.uuid4().hex[:12]
    job = JobState(job_id=job_id, object_name=req.object_name)
    JOBS[job_id] = job

    results: list[PositionResult] = [p.to_result() for p in req.positions]
    manual_tsn: dict[int, tuple[float | None, float | None]] = {
        p.number: (p.tsn_base_price, p.tsn_coefficient)
        for p in req.positions if p.tsn_base_price is not None or p.tsn_coefficient is not None
    }
    inputs = JobInputs(
        object_name=req.object_name, spec_results=results, use_tsn=req.use_tsn,
        fix_date=req.fix_date, skip_search=req.skip_search, manual_tsn=manual_tsn)
    run_pipeline_async(job, inputs, _browser_factory)
    return {"job_id": job_id}


@app.get("/cache/{name}")
def cache_file(name: str) -> FileResponse:
    """Отдаёт изображение скриншота из кэша (для предпросмотра в UI)."""
    path = CACHE_DIR / Path(name).name      # только basename — без обхода каталогов
    if not path.exists():
        raise HTTPException(404, "Нет файла")
    return FileResponse(path, media_type="image/png")


def _offer_to_dict(off) -> dict:
    """PriceOffer → форма OfferIn для UI (скриншоты — basename из кэша)."""
    req = off.requisites
    return {
        "price_with_vat": off.price_with_vat, "url": off.url,
        "org_name": req.name, "inn": req.inn, "kpp": req.kpp,
        "city": req.city, "status": int(req.status), "from_letter": off.from_letter,
        "product_title": off.product_title,
        "screenshot_product": Path(off.screenshot_product).name if off.screenshot_product else "",
        "screenshot_requisites": Path(off.screenshot_requisites).name if off.screenshot_requisites else "",
    }


class CaptureRequest(BaseModel):
    url: str
    inn: str = ""
    extract: bool = True


@app.post("/capture")
def capture(req: CaptureRequest) -> dict:
    """Снимает скриншот карточки товара (+ реквизитов по ИНН) и извлекает цену/ИНН со страницы."""
    from .requisites.dadata import screenshot_requisites
    from .search.agent import _extract_from_page
    result = {"screenshot_product": "", "screenshot_requisites": "", "extracted": {}}
    with _browser_factory() as browser:
        with browser.page(req.url) as page:
            page.wait_for_timeout(1500)
            shot = CACHE_DIR / f"prod_{abs(hash(req.url)) % 10**10}.png"
            page.screenshot(path=str(shot), clip={"x": 0, "y": 0, "width": 1366, "height": 900})
            result["screenshot_product"] = shot.name
            if req.extract:
                try:
                    data = _extract_from_page(page.inner_text("body")) or {}
                    result["extracted"] = data
                except Exception:
                    pass
        inn = req.inn or str(result["extracted"].get("seller_inn") or "")
        if inn:
            rshot = screenshot_requisites(browser, inn)
            if rshot:
                result["screenshot_requisites"] = Path(rshot).name
    return result


class SearchRequest(BaseModel):
    name: str
    type_mark: str = ""
    discipline: str = "ЭМ"


@app.post("/search_position")
def search_position(req: SearchRequest) -> dict:
    """Ищет цены по позиции и возвращает офферы (со скриншотами) для проверки в UI."""
    from .search.agent import research_position
    pos = SpecPosition(number=1, name=req.name, type_mark=req.type_mark,
                       discipline=req.discipline)
    year = str(date.today().year)
    quarter = str((date.today().month - 1) // 3 + 1)
    with _browser_factory() as browser:
        res = research_position(browser, pos, year, quarter)
    return {"offers": [_offer_to_dict(o) for o in res.offers],
            "year": res.year, "quarter": res.quarter,
            "found": res.sources_found, "checked": res.sources_checked,
            "target": res.sources_target or settings.min_sources}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return {
        "job_id": job.job_id, "status": job.status, "step": job.step,
        "progress": job.progress, "log": job.log[-50:],
        "outputs": list(job.outputs.keys()), "error": job.error,
    }


@app.get("/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str) -> FileResponse:
    job = JOBS.get(job_id)
    if not job or kind not in job.outputs:
        raise HTTPException(404, "Файл не готов")
    path = Path(job.outputs[kind])
    return FileResponse(path, filename=path.name)
