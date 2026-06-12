@echo off
REM Запуск локального движка (Windows). Создаёт venv, ставит зависимости, поднимает сервер.
cd /d "%~dp0"

if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python -m playwright install chromium

if not exist .env (
  copy .env.example .env
  echo Создан .env - заполните ANTHROPIC_API_KEY и DADATA_TOKEN, затем перезапустите.
)

echo Движок: http://127.0.0.1:8765 (Ctrl+C для остановки)
uvicorn app:app --host 127.0.0.1 --port 8765
