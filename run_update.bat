@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv\Scripts\activate.bat exit /b 1
call .venv\Scripts\activate.bat
echo === aggiornamento %date% %time% === >> logs\update.log
python -m ire update >> logs\update.log 2>&1
echo === fine (codice %errorlevel%) === >> logs\update.log
