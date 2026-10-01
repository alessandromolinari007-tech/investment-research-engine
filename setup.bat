@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo === Investment Research Engine: installazione ===
set ERRORI=0
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY goto :nopython
%PY% --version >nul 2>nul || goto :nopython
%PY% --version
if not exist .venv\Scripts\activate.bat (
  %PY% -m venv .venv || (echo Creazione dell'ambiente .venv fallita & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip || set "ERRORI=1"
python -m pip install -r requirements.txt || (echo Installazione pacchetti fallita & pause & exit /b 1)
python -m ire configure || set "ERRORI=1"
python -m ire check || set "ERRORI=1"
echo.
if %ERRORI%==0 (
  echo Installazione completata. Primo test veloce: run_quick_test.bat   Analisi completa: run_pipeline.bat   Interfaccia: run_app.bat
) else (
  echo Installazione completata CON PROBLEMI: leggi i messaggi qui sopra prima di proseguire.
)
pause
exit /b %ERRORI%
:nopython
echo Python non trovato. Installa Python 3.12 (winget install Python.Python.3.12) e riapri questa finestra.
pause
exit /b 1
