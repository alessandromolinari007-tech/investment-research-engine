@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo === Investment Research Engine: installazione ===
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version || (echo Python non trovato. Installa Python 3.10+ da https://www.python.org/downloads/ ^(spunta "Add to PATH"^) & pause & exit /b 1)
if not exist .venv (%PY% -m venv .venv)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo Installazione pacchetti fallita & pause & exit /b 1)
python -m ire configure
python -m ire check
echo.
echo Fatto. Primo test veloce: run_quick_test.bat   Analisi completa: run_pipeline.bat   Interfaccia: run_app.bat
pause
