# Investment Research Engine

Motore di ricerca azionaria personale, gratuito e trasparente: analizza le azioni quotate
acquistabili da un investitore retail (USA, Europa, Regno Unito, Giappone, Canada, Australia…)
usando solo dati pubblici e gratuiti (SEC EDGAR XBRL, Yahoo Finance via `yfinance`, BCE, FRED).

> **Stato:** in sviluppo. La revisione "red team" è in corso e il test end-to-end con dati
> reali non è ancora stato eseguito. Non è una consulenza finanziaria.

## Cosa fa
- costruisce l'universo investibile (filtri di liquidità e dimensione);
- scarica bilanci (SEC XBRL per le società registrate alla SEC, Yahoo per le altre) con provenienza di ogni numero;
- calcola ~80 metriche (valutazione, qualità, crescita, solidità, allocazione del capitale) adattate al settore;
- punteggi relativi al settore su 5 pilastri e 5 schemi di pesi (punteggio "robusto" = mediana);
- valutazione multi-metodo: confronto con i pari, con la propria storia, reverse DCF, rendimento vs tassi;
- ricerca di red flag nei filing (going concern, debolezze nei controlli, restatement, eventi 8-K…);
- portafoglio proposto con vincoli di peso, settore, area e correlazione, ruolo e motivazione per ogni titolo;
- confronto tra un'analisi e la precedente (cosa è cambiato, cosa è peggiorato);
- interfaccia web in italiano (Streamlit).

## Uso rapido (Windows)
1. Installa Python 3.10+ da python.org (spunta "Add to PATH").
2. Doppio clic su `setup.bat` (crea l'ambiente e chiede nome + email per la SEC).
3. `run_quick_test.bat` per una prima analisi veloce, `run_pipeline.bat` per quella completa.
4. `run_app.bat` per aprire l'interfaccia.

Riga di comando: `python -m ire check | run [--mode quick|standard|full] | status | app | watch add TICKER`.

## Test
`python -m pytest -q` — test offline su dati **sintetici** (nessun dato reale è inventato o incluso).

## Configurazione
Tutto in `config.toml` (soglie dell'universo, pesi, vincoli di portafoglio, premio per il rischio…).
Il campo `[sec] user_agent` deve contenere nome ed email: è richiesto dalla SEC ed è inviato solo a sec.gov.
