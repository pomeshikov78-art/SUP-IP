@echo off
rem СУП-ИП — запуск сервера (Windows). Требуется PostgreSQL и psycopg2-binary (см. README.md).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python не найден. Установите Python 3.10+ с python.org & pause & exit /b 1)
python -c "import psycopg2" 2>nul || python -m pip install psycopg2-binary
python server.py --port 8000 %*
pause
