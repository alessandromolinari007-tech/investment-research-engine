@echo off
chcp 65001 >nul
echo Rimuove gli aggiornamenti automatici dell analisi.
schtasks /Delete /TN "IRE aggiornamento giornaliero" /F
schtasks /Delete /TN "IRE aggiornamento all accesso" /F
echo Fatto (eventuali errori "attivita non trovata" sono normali).
pause
