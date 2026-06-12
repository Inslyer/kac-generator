"""Тесты эндпоинтов без сети: импорт приложения, отдача кэша, проброс скриншотов в ТКП.

Запуск:  python -m engine.test_endpoints
"""
from __future__ import annotations

from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from engine.app import app
from engine.config import CACHE_DIR
from engine.schemas import BuildRequest

client = TestClient(app)


def _make_png(path: Path, label: str = "test") -> Path:
    """Генерирует небольшой PNG-образец (без внешних файлов)."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 100), label, fontsize=20)
    pix = page.get_pixmap()
    pix.save(path)
    doc.close()
    return path


def test_health_and_cache():
    assert client.get("/health").json()["ok"] is True

    # положим тестовый PNG в кэш и проверим отдачу + защиту от обхода каталогов
    _make_png(CACHE_DIR / "prod_test.png", "card")
    r = client.get("/cache/prod_test.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/cache/nope.png").status_code == 404
    # обход каталога обезвреживается (берётся только basename)
    assert client.get("/cache/..%2f..%2fconfig.py").status_code in (404, 400)
    print("✓ /health и /cache (отдача + защита) работают")


def test_screenshot_threading_into_tkp():
    """Скриншоты, переданные в офферах, должны попасть в Том ТКП."""
    _make_png(CACHE_DIR / "prod_thread.png", "card")
    _make_png(CACHE_DIR / "req_thread.png", "requisites")

    build = BuildRequest(
        object_name="ТЕСТ скриншоты", skip_search=True, use_tsn=False,
        positions=[{
            "number": 1, "name": "Кабель", "unit": "м",
            "offers": [{
                "price_with_vat": 2361, "org_name": "ООО Тест", "inn": "7700000000",
                "screenshot_product": "prod_thread.png",
                "screenshot_requisites": "req_thread.png",
            }],
        }],
    )
    res = build.positions[0].to_result()
    off = res.offers[0]
    assert off.screenshot_product and Path(off.screenshot_product).exists()
    assert off.screenshot_requisites and Path(off.screenshot_requisites).exists()

    from engine.builders.tkp_pdf import build_tkp
    out, _ = build_tkp([res], "ТЕСТ скриншоты", CACHE_DIR / "tkp_thread.pdf")
    import fitz
    d = fitz.open(out)
    # на странице позиции должны быть встроены изображения (карточка + реквизиты)
    imgs = d[1].get_images()
    assert len(imgs) >= 2, f"ожидались встроенные скриншоты, найдено {len(imgs)}"
    print(f"✓ скриншоты офферов встроены в Том ТКП (изображений на странице: {len(imgs)})")


if __name__ == "__main__":
    test_health_and_cache()
    test_screenshot_threading_into_tkp()
    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✓")
