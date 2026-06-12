"""Диагностический CLI калибровки: прогоняет этапы пайплайна на реальных файлах
и печатает понятный отчёт (что сработало, где сломалось).

Запуск (из корня репозитория, с заполненным engine/.env):
    python -m engine.calibrate --spec "путь/Спецификация.pdf" \
        [--letter "путь/Счет.pdf"] [--discipline ЭМ] [--search 2] [--tsn]

--search N  — попробовать онлайн-поиск по первым N неуникальным позициям (нужен ключ+браузер)
--tsn       — проверить доступ к mos.ru (коэффициент пересчёта + расценка по 1 позиции)

Каждый этап изолирован: ошибка одного не валит остальные. Без ключей/браузера —
этапы помечаются как недоступные с подсказкой, что включить.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import settings

OK, WARN, BAD = "✓", "⚠", "✗"


def hr(title: str) -> None:
    print(f"\n{'═' * 70}\n  {title}\n{'═' * 70}")


def line(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


def stage(title: str):
    """Декоратор-обёртка: печатает заголовок и ловит исключения этапа."""
    def wrap(fn):
        def inner(*a, **k):
            hr(title)
            try:
                return fn(*a, **k)
            except Exception as e:
                line(BAD, f"ОШИБКА: {e}")
                print("    " + "\n    ".join(traceback.format_exc().splitlines()[-4:]))
                return None
        return inner
    return wrap


# ─────────────────────────────── окружение ───────────────────────────────
@stage("1. ОКРУЖЕНИЕ")
def check_env() -> dict:
    env = {}
    env["anthropic"] = bool(settings.anthropic_api_key)
    env["dadata"] = bool(settings.dadata_token)
    line(OK if env["anthropic"] else BAD,
         f"ANTHROPIC_API_KEY — {'задан' if env['anthropic'] else 'НЕ задан (разбор/поиск/ТСН не работают)'}")
    line(OK if env["dadata"] else WARN,
         f"DADATA_TOKEN — {'задан' if env['dadata'] else 'не задан (реквизиты по ИНН не подтянутся)'}")
    try:
        import playwright  # noqa
        env["playwright"] = True
        line(OK, "playwright — установлен")
    except ImportError:
        env["playwright"] = False
        line(BAD, "playwright — НЕ установлен (pip install -r requirements.txt; playwright install chromium)")
    try:
        import shutil
        env["tesseract"] = bool(shutil.which("tesseract"))
        line(OK if env["tesseract"] else WARN,
             f"tesseract — {'есть' if env['tesseract'] else 'нет (не обязателен: сканы идут через Claude vision)'}")
    except Exception:
        env["tesseract"] = False
    # Yandex Search API — ключевой источник поиска (без капчи)
    if settings.yandex_search_api_key and settings.yandex_search_folder_id:
        from engine.search.search_engine import yandex_api_search
        try:
            urls = yandex_api_search("ноутбук купить москва", n=5)
            line(OK if urls else WARN,
                 f"Yandex Search API — ключ задан, тестовый запрос вернул {len(urls)} ссылок"
                 + ("" if urls else " (проверьте права/folderId/баланс — детали в логе выше)"))
        except Exception as e:
            line(BAD, f"Yandex Search API — ошибка: {e}")
    else:
        line(WARN, "Yandex Search API — ключ не задан (поиск пойдёт через браузер → возможна капча)")
    line("•", f"параметры: НДС {settings.vat_rate}% · источников ≥{settings.min_sources} · "
              f"в КАЦ {settings.top_prices} макс. · регион «{settings.region}»")
    return env


# ──────────────────────────── разбор спецификации ────────────────────────────
@stage("2. РАЗБОР СПЕЦИФИКАЦИИ")
def check_spec(spec_paths: list[str], discipline: str, unique_names: set[str]):
    from engine.parsers.spec_pdf import extract_raw_text, parse_spec
    all_positions = []
    for sp in spec_paths:
        text, scanned = extract_raw_text(sp)
        line("•", f"{Path(sp).name}: {'СКАН (vision)' if scanned else 'текстовый слой'}, "
                  f"символов текста: {len(text.strip())}")
        positions = parse_spec(sp, discipline=discipline, unique_names=unique_names)
        line(OK, f"распознано позиций: {len(positions)}")
        for p in positions[:20]:
            flag = " [УНИК.]" if p.is_unique else ""
            line("  ", f"№{p.number:>2} {p.name[:54]:<54} {p.unit:<6} {p.qty:g}{flag}")
        if len(positions) > 20:
            line("  ", f"… ещё {len(positions) - 20} позиц.")
        all_positions += positions
    uniq = sum(1 for p in all_positions if p.is_unique)
    line("•", f"итого {len(all_positions)} позиц., из них уникальных (из писем): {uniq}")
    return all_positions


# ──────────────────────────────── письма ────────────────────────────────
@stage("3. РАЗБОР ПИСЕМ / СЧЕТОВ")
def check_letters(letter_paths: list[str]):
    from engine.parsers.letters import parse_letter
    for lp in letter_paths:
        items = parse_letter(lp)
        line(OK, f"{Path(lp).name}: позиций с ценой {len(items)}")
        for it in items[:15]:
            line("  ", f"{str(it.get('name',''))[:46]:<46} "
                       f"{it.get('price_with_vat','?')!s:>12} ₽  "
                       f"ИНН {it.get('supplier_inn','—')}")


# ──────────────────────────── онлайн-поиск цен ────────────────────────────
@stage("4. ОНЛАЙН-ПОИСК ЦЕН")
def check_search(positions, n: int, discipline: str):
    if not positions:
        line(WARN, "нет позиций (этап 2 не дал результата) — пропуск")
        return
    from datetime import date
    from engine.browser import Browser
    from engine.search.agent import research_position
    targets = [p for p in positions if not p.is_unique][:n]
    line("•", f"проверяю поиск по {len(targets)} позициям…")
    year = str(date.today().year)
    quarter = str((date.today().month - 1) // 3 + 1)
    with Browser() as browser:
        for p in targets:
            res = research_position(browser, p, year, quarter)
            mark = OK if res.sources_found >= res.sources_target else WARN
            line(mark, f"«{p.name[:40]}»: найдено {res.sources_found}/{res.sources_target} "
                       f"источ., в КАЦ {len(res.offers)} цен")
            for o in res.offers:
                shot = "📷" if o.screenshot_product else "—"
                line("  ", f"{o.price_with_vat:>12,.2f} ₽  {o.requisites.city:<14} "
                           f"ИНН {o.requisites.inn or '—':<12} {shot} {o.url[:50]}")


# ──────────────────────────────── ТСН mos.ru ────────────────────────────────
@stage("5. ТСН-2001 (mos.ru)")
def check_tsn(positions):
    from engine.browser import Browser
    from engine.tsn.mos_ru import fetch_current_coefficient, lookup_tsn_price
    with Browser() as browser:
        coeff = fetch_current_coefficient(browser)
        line(OK if coeff else WARN,
             f"коэффициент пересчёта: {coeff if coeff else 'не получен (проверьте доступ к mos.ru/структуру страницы)'}")
        if positions:
            p = next((x for x in positions if not x.is_unique), positions[0])
            tsn = lookup_tsn_price(browser, p.name)
            line(OK if tsn else WARN,
                 f"расценка ТСН по «{p.name[:40]}»: "
                 f"{tsn.get('base_price') if tsn else 'не найдена (доработать матчинг/селекторы)'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Диагностика пайплайна КАЦ на реальных файлах")
    ap.add_argument("--spec", action="append", default=[], help="PDF спецификации (можно несколько)")
    ap.add_argument("--letter", action="append", default=[], help="PDF письма/счёта")
    ap.add_argument("--discipline", default="ЭМ")
    ap.add_argument("--unique", default="", help="подстроки уникального оборуд. через ;")
    ap.add_argument("--search", type=int, default=0, help="проверить поиск по N позициям")
    ap.add_argument("--tsn", action="store_true", help="проверить mos.ru")
    args = ap.parse_args()

    print("\n  ДИАГНОСТИКА КАЦ-ГЕНЕРАТОРА")
    env = check_env()
    uniq = {s.strip() for s in args.unique.split(";") if s.strip()}

    positions = []
    if args.spec:
        if not env or not env.get("anthropic"):
            hr("2. РАЗБОР СПЕЦИФИКАЦИИ")
            line(BAD, "пропуск: нужен ANTHROPIC_API_KEY")
        else:
            positions = check_spec(args.spec, args.discipline, uniq) or []
    if args.letter and env and env.get("anthropic"):
        check_letters(args.letter)

    if args.search > 0:
        if env and env.get("anthropic") and env.get("playwright"):
            check_search(positions, args.search, args.discipline)
        else:
            hr("4. ОНЛАЙН-ПОИСК ЦЕН")
            line(BAD, "пропуск: нужны ANTHROPIC_API_KEY и установленный playwright")
    if args.tsn:
        if env and env.get("anthropic") and env.get("playwright"):
            check_tsn(positions)
        else:
            hr("5. ТСН-2001 (mos.ru)")
            line(BAD, "пропуск: нужны ANTHROPIC_API_KEY и установленный playwright")

    hr("ГОТОВО")
    print("  Скопируйте вывод выше и пришлите — по нему откалибруем промпты и селекторы.\n")


if __name__ == "__main__":
    main()
