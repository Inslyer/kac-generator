"""Менеджер браузера Playwright. Один контекст на сессию; скриншоты и обход страниц.

Браузер запускается локально на машине сметчика (российский IP, headed по умолчанию),
что позволяет обходить антибот-защиту магазинов и mos.ru.

Cookie сохраняются в файл (data/cache/cookies.json, storage_state). Поэтому капчу Яндекса
достаточно решить один раз — на следующих запусках cookie подгружаются и капча не появляется,
пока живы. Файл лежит локально и в git не коммитится.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import CACHE_DIR, settings

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

COOKIE_FILE = CACHE_DIR / "cookies.json"


class Browser:
    """Лёгкая обёртка. Cookie сохраняются между запусками (storage_state)."""

    def __init__(self, headed: bool | None = None):
        self.headed = settings.browser_headed if headed is None else headed
        self._pw = None
        self._browser = None
        self.context = None

    def __enter__(self) -> "Browser":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not self.headed)
        kwargs = dict(user_agent=UA, locale="ru-RU",
                      viewport={"width": 1366, "height": 900})
        if COOKIE_FILE.exists():
            kwargs["storage_state"] = str(COOKIE_FILE)  # подгружаем cookie (решённую капчу)
        self.context = self._browser.new_context(**kwargs)
        self.context.set_default_timeout(20000)
        return self

    def _save_cookies(self) -> None:
        try:
            self.context.storage_state(path=str(COOKIE_FILE))
        except Exception:
            pass

    def __exit__(self, *exc) -> None:
        self._save_cookies()  # сохраняем cookie (в т.ч. решённую капчу) на следующий запуск
        for obj in (self.context, self._browser, self._pw):
            try:
                obj.close() if hasattr(obj, "close") else obj.stop()
            except Exception:
                pass

    @contextmanager
    def page(self, url: str) -> Iterator:
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            yield page
        finally:
            page.close()

    def screenshot(self, url: str, out_path: str | Path, *,
                   full_page: bool = False, clip_top: int | None = None) -> Path:
        """Открывает url и сохраняет скриншот. clip_top — высота видимой области (px)."""
        out_path = Path(out_path)
        with self.page(url) as page:
            page.wait_for_timeout(1500)  # дать прогрузиться цене
            if clip_top:
                page.screenshot(path=str(out_path),
                                clip={"x": 0, "y": 0, "width": 1366, "height": clip_top})
            else:
                page.screenshot(path=str(out_path), full_page=full_page)
        return out_path


def cache_path(name: str) -> Path:
    return CACHE_DIR / name
