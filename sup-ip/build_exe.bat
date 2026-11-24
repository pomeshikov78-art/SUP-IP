@echo off
rem СУП-ИП — сборка автономного EXE (Windows x64). Результат: dist\SUP-IP.exe
cd /d "%~dp0"
python -m pip install pyinstaller psycopg2-binary
pyinstaller --onefile --name SUP-IP --add-data "static;static" --add-data "assets;assets" --hidden-import docgen --clean server.py
echo.
echo Готово: dist\SUP-IP.exe  (PostgreSQL устанавливается отдельно, см. README.md)
pause
