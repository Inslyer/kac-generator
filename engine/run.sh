#!/usr/bin/env bash
# Запуск локального движка (macOS/Linux). Создаёт venv, ставит зависимости, поднимает сервер.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python -m playwright install chromium

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан .env — заполните ANTHROPIC_API_KEY и DADATA_TOKEN, затем перезапустите."
fi

echo "Движок: http://127.0.0.1:8765 (Ctrl+C для остановки)"
# запуск из корня репозитория как пакет engine.app (движок использует относительные импорты)
cd ..
exec uvicorn engine.app:app --host 127.0.0.1 --port 8765
