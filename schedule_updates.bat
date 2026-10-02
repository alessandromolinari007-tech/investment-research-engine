@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Aggiornamenti automatici dell analisi.
echo Verra creata nell Utilita di pianificazione di Windows (solo per il tuo utente) una attivita che controlla ogni giorno alle 12:30
echo e a ogni accesso a Windows se l analisi ha piu di 7 giorni; in quel caso la rifa da sola (15-70 minuti, in una finestra che si chiude da sola).
echo Funziona solo con il PC acceso. Per toglierla: unschedule_updates.bat
echo.
pause
schtasks /Create /TN "IRE aggiornamento giornaliero" /TR "\"%~dp0run_update.bat\"" /SC DAILY /ST 12:30 /F
if errorlevel 1 (echo. & echo NON RIUSCITO: l attivita giornaliera non e stata creata. & pause & exit /b 1)
schtasks /Create /TN "IRE aggiornamento all accesso" /TR "\"%~dp0run_update.bat\"" /SC ONLOGON /DELAY 0005:00 /F
if errorlevel 1 (echo. & echo L attivita all accesso richiede i permessi di amministratore: non e stata creata. Resta quella giornaliera, che basta.)
echo.
echo Fatto. Il registro degli aggiornamenti e in logs\update.log
pause
