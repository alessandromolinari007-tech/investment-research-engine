# Investment Research Engine

Motore di ricerca azionaria personale, gratuito e trasparente: analizza le azioni quotate
acquistabili da un investitore retail (USA, Europa, Regno Unito, Giappone, Canada, Australia…)
usando solo dati pubblici e gratuiti (SEC EDGAR XBRL, Yahoo Finance via `yfinance`, BCE, FRED).

> **Stato:** in sviluppo. Testato offline su dati sintetici; il test end-to-end con dati reali si esegue
> sul PC con `first_run_test.bat`. Non è una consulenza finanziaria.
>
> Come funziona, in dettaglio: **[METHODOLOGY.md](METHODOLOGY.md)** (visibile anche nell'app, pagina *Dati e metodologia*).

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
1. Installa Python 3.12: `winget install Python.Python.3.12` (oppure da python.org, spunta "Add to PATH").
2. Doppio clic su `setup.bat`: crea l'ambiente `.venv`, installa le librerie, chiede nome + email per la SEC
   (salvati in `config.local.toml`, che non viene mai pubblicato) e verifica le fonti dati.
   In alternativa `first_run_test.bat` fa installazione + test + prima analisi rapida e scrive tutto in `logs\first_run.log`.
3. `run_quick_test.bat` per una prima analisi veloce (società ≥ 20 mld $), `run_pipeline.bat` per quella completa.
4. `run_app.bat` apre l'interfaccia nel browser (http://localhost:8501). Chiudi la finestra nera per fermarla.

Riga di comando (con `.venv` attivo):

| Comando | Cosa fa |
|---|---|
| `python -m ire check` | verifica configurazione e raggiungibilità di ogni fonte |
| `python -m ire configure [--check]` | imposta (o verifica) il contatto richiesto dalla SEC |
| `python -m ire run [--mode quick\|standard\|full] [--tickers AAPL,ENI.MI] [--skip-deep] [--limit N]` | esegue l'analisi |
| `python -m ire status` | ultime analisi ed eventuali errori |
| `python -m ire app` | interfaccia web |
| `python -m ire watch add\|remove\|list TICKER` | watchlist (sempre analizzata) |

Ripeti l'analisi ogni settimana o mese: la pagina *Cambiamenti e watchlist* mostra cosa è peggiorato.

## Test
`python -m pytest -q` — test offline su dati **sintetici** (nessun dato reale è inventato o incluso).
Per sviluppatori: `python scripts/dev_offline_run.py` esegue l'intera pipeline sul mondo sintetico e stampa la
cartella dati; `IRE_CONFIG=<cartella>/config.toml python scripts/dev_ui_test.py` renderizza tutte le pagine.

## Configurazione
Tutto in `config.toml` (soglie dell'universo, pesi, vincoli di portafoglio, premio per il rischio…), con commenti in
italiano. I valori sono controllati all'avvio: un valore non valido produce un messaggio chiaro invece di
un'analisi sbagliata. Per i percorsi Windows usa le barre normali (`C:/Users/nome/dati`).

Le impostazioni personali vanno in `config.local.toml` (stessa sintassi, sovrascrive `config.toml`, escluso da git):
in particolare `[sec] user_agent = "Nome Cognome email"`, richiesto dalla SEC e inviato solo a sec.gov.
In alternativa si può usare la variabile d'ambiente `IRE_SEC_USER_AGENT`.
