@echo off
rem Installazione + test completo NON interattivo. Tutto l'output va in logs\first_run.log
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist logs mkdir logs
set LOG=logs\first_run.log
echo === first_run_test %date% %time% === > %LOG%
echo Installazione e test in corso: puo' richiedere 10-30 minuti. Non chiudere questa finestra.
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
echo [python] >> %LOG%
%PY% --version >> %LOG% 2>&1 || (echo PYTHON_NON_TROVATO >> %LOG% & echo Python non trovato & pause & exit /b 1)
if not exist .venv (%PY% -m venv .venv >> %LOG% 2>&1)
call .venv\Scripts\activate.bat
echo [pip] >> %LOG%
python -m pip install --upgrade pip >> %LOG% 2>&1
python -m pip install -r requirements.txt >> %LOG% 2>&1 || (echo PIP_FALLITO >> %LOG% & pause & exit /b 1)
echo [pytest] >> %LOG%
python -m pytest -q >> %LOG% 2>&1
echo [check] >> %LOG%
python -m ire check >> %LOG% 2>&1
echo [run quick] >> %LOG%
python -m ire run --mode quick >> %LOG% 2>&1
echo [status] >> %LOG%
python -m ire status >> %LOG% 2>&1
echo === FINE %date% %time% === >> %LOG%
echo Fatto. Log in logs\first_run.log
