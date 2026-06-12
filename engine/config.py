"""Конфигурация движка из переменных окружения (.env)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_DIR = Path(__file__).resolve().parent
DATA_DIR = ENGINE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
CACHE_DIR = DATA_DIR / "cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENGINE_DIR / ".env", extra="ignore")

    anthropic_api_key: str = ""
    model_smart: str = "claude-opus-4-8"
    model_fast: str = "claude-sonnet-4-6"

    dadata_token: str = ""
    yandex_search_api_key: str = ""
    yandex_search_folder_id: str = ""

    min_sources: int = 12
    top_prices: int = 3
    vat_rate: int = 22
    region: str = "Москва и Московская область"
    # производители уникального оборудования (цена из писем, онлайн-поиск не нужен);
    # через «;», напр. CUSTOM_MAKERS=ЗаводА;ЗаводБ
    custom_makers: str = ""

    browser_headed: bool = True

    @property
    def custom_makers_list(self) -> list[str]:
        return [m.strip().lower() for m in self.custom_makers.split(";") if m.strip()]

    @property
    def vat_divisor(self) -> float:
        """Коэффициент для перевода цены С НДС в цену БЕЗ НДС (графа H = G / divisor)."""
        return 1 + self.vat_rate / 100.0


settings = Settings()

for _d in (UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
