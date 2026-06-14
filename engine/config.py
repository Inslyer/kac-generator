"""Конфигурация движка из переменных окружения (.env)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = ENGINE_DIR.parent
DATA_DIR = ENGINE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
CACHE_DIR = DATA_DIR / "cache"

# Локальные справочники ТСН-2001 (лежат в корне репо). Используются вместо mos.ru,
# который недоступен с зарубежного IP/VPN.
TSN_REFERENCE_FILE = REPO_ROOT / "ТСН-2001_электро_автоматика.md"
TSN_INDICES_FILE = REPO_ROOT / "индексы.md"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENGINE_DIR / ".env", extra="ignore")

    # Провайдер LLM: "anthropic" (Claude) или "deepseek". DeepSeek дешевле и совместим
    # с OpenAI API, но БЕЗ vision — сканы идут через локальный OCR (Tesseract), см. spec_pdf.
    llm_provider: str = "anthropic"

    anthropic_api_key: str = ""
    model_smart: str = "claude-opus-4-8"
    model_fast: str = "claude-sonnet-4-6"

    # DeepSeek (OpenAI-совместимый). deepseek-chat — для всех задач (текст), без vision.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_smart: str = "deepseek-chat"
    deepseek_model_fast: str = "deepseek-chat"

    dadata_token: str = ""
    yandex_search_api_key: str = ""
    yandex_search_folder_id: str = ""

    # SERP-API: XMLRiver/XMLStock — выдача Яндекса в XML без капчи. Если заданы user+key,
    # поиск идёт через API (без браузера); иначе фолбэк на браузерную выдачу.
    # URL: XMLRiver — https://xmlriver.com/search_yandex/xml ; XMLStock — https://xmlstock.com/yandex/xml/
    xmlriver_user: str = ""
    xmlriver_key: str = ""
    xmlriver_url: str = "https://xmlriver.com/search_yandex/xml"
    xmlriver_lr: int = 213  # код региона Яндекса (213 = Москва)

    min_sources: int = 12
    top_prices: int = 3
    vat_rate: int = 22
    region: str = "Москва и Московская область"
    # производители уникального оборудования (цена из писем, онлайн-поиск не нужен);
    # через «;», напр. CUSTOM_MAKERS=ЗаводА;ЗаводБ
    custom_makers: str = ""

    browser_headed: bool = True

    # ресурсы для скриншота карточки реквизитов по ИНН (пробуются по порядку, до первого
    # отрисовавшего карточку). checko.ru исключён — блокирует зарубежный IP/VPN. {inn} —
    # подстановка ИНН. Переопределяется через .env: REQUISITES_SOURCES="url1;url2"
    requisites_sources: str = (
        "https://www.audit-it.ru/contragent/{inn};"
        "https://sbis.ru/contragents/{inn};"
        "https://e-ecolog.ru/org/{inn};"
        "https://zachestnyibiznes.ru/search?query={inn}"
    )

    @property
    def requisites_sources_list(self) -> list[str]:
        return [u.strip() for u in self.requisites_sources.split(";") if u.strip()]

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
