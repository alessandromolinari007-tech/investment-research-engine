@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
rem Installazione + test completo. Tutto l'output va in logs\first_run.log
if not exist logs mkdir logs
set LOG=logs\first_run.log
set ERRORI=0
echo === first_run_test %date% %time% === > %LOG%
echo Installazione e test in corso: puo' richiedere 10-30 minuti. Non chiudere questa finestra.
echo Avanzamento dettagliato in %LOG%
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY goto :nopython
%PY% --version >nul 2>nul || goto :nopython
echo [python] >> %LOG%
%PY% --version >> %LOG% 2>&1
if not exist .venv\Scripts\activate.bat (
  %PY% -m venv .venv >> %LOG% 2>&1 || (echo VENV_FALLITO >> %LOG% & echo Creazione di .venv fallita & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo [pip] >> %LOG%
python -m pip install --upgrade pip >> %LOG% 2>&1
python -m pip install -r requirements.txt >> %LOG% 2>&1 || (echo PIP_FALLITO >> %LOG% & echo Installazione pacchetti fallita: vedi %LOG% & pause & exit /b 1)
echo [configure] >> %LOG%
python -m ire configure --check >> %LOG% 2>&1
if errorlevel 1 (
  echo Manca il contatto richiesto dalla SEC: inseriscilo qui sotto.
  python -m ire configure
  python -m ire configure --check >> %LOG% 2>&1 || echo SENZA CONTATTO SEC: i titoli USA verranno saltati.
)
echo 1/5 test automatici...
echo [pytest] >> %LOG%
python -m pytest -q >> %LOG% 2>&1 || (set "ERRORI=1" & echo PYTEST_FALLITO >> %LOG%)
echo 2/5 verifica fonti dati...
echo [check] >> %LOG%
python -m ire check >> %LOG% 2>&1 || (set "ERRORI=1" & echo CHECK_FALLITO >> %LOG%)
echo 3/5 analisi rapida (quick): la parte piu' lunga...
echo [run quick] >> %LOG%
python -m ire run --mode quick >> %LOG% 2>&1 || (set "ERRORI=1" & echo RUN_FALLITO >> %LOG%)
echo 4/5 stato...
echo [status] >> %LOG%
python -m ire status >> %LOG% 2>&1
echo 5/5 fine.
echo === FINE %date% %time% (errori=%ERRORI%) === >> %LOG%
if %ERRORI%==0 (echo Completato senza errori. Log in %LOG%) else (echo Completato CON ERRORI: invia il file %LOG%)
pause
exit /b %ERRORI%
:nopython
echo PYTHON_NON_TROVATO >> %LOG%
echo Python non trovato. Installa Python 3.12 (winget install Python.Python.3.12) e riapri questa finestra.
pause
exit /b 1
