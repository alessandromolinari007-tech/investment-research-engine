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
echo Verifica storica del metodo: richiede 20-40 minuti. Non chiudere questa finestra.
python -m ire backtest
if errorlevel 1 (echo. & echo VERIFICA NON COMPLETATA: leggi i messaggi qui sopra.) else (echo. & echo Verifica completata. Apri run_app.bat e la pagina "Verifica del metodo".)
pause
