@echo off
rem СУП-ИП — сборка установщика Windows x64 (ТЗ §7.1): SUP-IP.exe + SUP-IP-Setup-VERSION.exe
rem Требуется: Python 3.10+, NSIS (makensis в PATH или "C:\Program Files (x86)\NSIS\makensis.exe").
rem Опционально для полностью офлайн-пакета: положите postgresql-16.x-windows-x64.exe
rem            в installer\postgresql-setup.exe — он будет включён в установщик.
cd /d "%~dp0.."
python -m pip install pyinstaller psycopg2-binary pywin32 cryptography
pyinstaller --onefile --name SUP-IP --add-data "static;static" --add-data "assets;assets" --hidden-import docgen --clean server.py
if errorlevel 1 (echo Ошибка сборки EXE & pause & exit /b 1)
set VER=
for /f "tokens=3 delims= " %%a in ('findstr /b "VERSION = " server.py') do set VER=%%~a
where makensis >nul 2>nul
if %errorlevel%==0 (set NSIS=makensis) else (set NSIS="C:\Program Files (x86)\NSIS\makensis.exe")
%NSIS% /DVERSION=%VER% installer\setup.nsi
echo.
echo Готово: dist\SUP-IP.exe и dist\SUP-IP-Setup-%VER%.exe
pause
