@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Ambiente Python non installato: esegui prima setup.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m ire app
if errorlevel 1 (
  echo.
  echo L'interfaccia si e' chiusa con un errore: leggi i messaggi qui sopra.
)
pause
