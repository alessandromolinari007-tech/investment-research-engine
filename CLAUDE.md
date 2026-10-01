# CLAUDE.md — Investment Research Engine

## 1. Obiettivo del progetto
Motore di ricerca azionaria personale per un investitore retail italiano alle prime armi in finanza (base EUR).
Deve scandire il più ampio universo di azioni acquistabili (USA, Europa, UK, Giappone, Canada, Australia, altri) usando SOLO dati pubblici gratuiti, analizzare bilanci, qualità, valutazione (multi-metodo), red flag, confronti tra pari, e proporre un portafoglio motivato posizione per posizione.
Deve essere riusabile nel tempo (ri-scansione, "cosa è cambiato", deterioramenti), trasparente (dato→fonte→data→metodo→assunzione) e comprensibile. Regola più importante: niente dati finti, niente numeri hardcoded, niente mockup.
Specifica originale completa: file `attachment.txt` caricato dall'utente nella prima sessione (19 sezioni). Non è nel repo: da definire se aggiungerlo (vedi §9).

## 2. Stato attuale (aggiornato al 2026-10-01)
Repository: https://github.com/alessandromolinari007-tech/investment-research-engine (ramo `main`). Visibilità del repo: da verificare.
Test offline: `python -m pytest -q` → 120 passed (Python 3.11, sandbox cloud). `pytest.ini` limita la raccolta a `tests/`.
UI: `scripts/dev_offline_run.py` + `scripts/dev_ui_test.py` → 7 pagine su 7 senza eccezioni (DB sintetico).
Test end-to-end con dati REALI: MAI eseguito. Python 3.12 autorizzato dall'utente (lo installa lui con winget); l'utente lancia `first_run_test.bat` e riporta `logs\first_run.log`.

### Fatto in questa sessione (commit e3111be → 328f4b6 + successivi)
- Blocco 1: `app/app.py` e `ire/changes.py` usano le costanti di classe di `ire/scoring.py` (sezione 💎 non più vuota).
- Blocco 2: chiusi i punti 1-9 della vecchia lista bug (prezzi/refresh/split, rate limit Yahoo, memoria download, cambi, changes, app, .bat, config, tesi). Contatto SEC in `config.local.toml` (in .gitignore).
- Blocco 3: test di metodologia (`tests/test_methodology.py`), `METHODOLOGY.md`, README, glossario completo.
- Blocco 4: secondo red team con 5 subagenti (quant, analista finanziario, data engineer, portafoglio, scettico/UX): circa 55 rilievi, corretti in 3 commit (dettagli nei messaggi di commit e in §7). Riverifica con subagenti indipendenti: vedi §7.

### Funziona (testato SOLO offline, con dati sintetici)
Pipeline in 9 stadi, normalizzazione SEC/Yahoo, ~80 metriche, punteggi, classificazione, verdetto, tesi, red flag, portafoglio, diff, UI a 7 pagine, controllo di salute delle esecuzioni (stato `degraded`), lock contro esecuzioni parallele.

## 3. Stack e ambiente
- Python. Sandbox cloud: 3.11.15. PC utente: Python 3.12 (installazione autorizzata, fatta dall'utente). Target: Windows 11.
- Versioni nel sandbox: pandas 3.0.2, numpy 2.4.4, yfinance 1.7.0, streamlit 1.64.0, plotly 7.1.0, scipy 1.17.1, requests 2.33.1, lxml 6.1.0, beautifulsoup4 4.14.3, html5lib 1.1, pytest 9.1.1. Vincoli in `requirements.txt`.
- SQLite in `data/` (config `[general] data_dir`); cache HTTP gzip in `data/cache/`.
- Fonti (gratuite):
  - SEC EDGAR: `company_tickers_exchange.json`, `submissions`, `api/xbrl/companyfacts`, `api/xbrl/frames`, documenti in `Archives/edgar/data`. User-Agent nome+email obbligatorio, inviato solo a sec.gov; massimo 10 richieste/s (config 8).
  - Yahoo via yfinance: prezzi, split, metadati, bilanci non-SEC, stime analisti.
  - BCE: `eurofxref-hist.zip`.
  - FRED: titoli di Stato a 10 anni (USD `DGS10`, EUR `IRLTLT01DEM156N`, altre valute serie `IRLTLT01xxM156N`).
  - Wikipedia: indici FTSE MIB, DAX, CAC 40, AEX, IBEX 35, SMI, FTSE 100, OMXS30, OMXC25, OMXH25, Nikkei 225, S&P/TSX 60, S&P/ASX 200, Hang Seng, più il seed `ire/data/international_seed.csv` (652 ticker).
- Benchmark: SWDA.MI → IWDA.AS → URTH (convertito in EUR).
- Comandi:
  - Test: `python -m pytest -q`
  - Run offline sintetico: `python scripts/dev_offline_run.py` (stampa `DATA <cartella>`)
  - Test UI: `IRE_CONFIG=<cartella>/config.toml python scripts/dev_ui_test.py`
  - CLI: `python -m ire check | configure | run [--mode quick|standard|full] [--tickers AAPL,ENI.MI] [--skip-deep] [--limit N] | status | app | watch add|remove|list TICKER`
  - Windows: `setup.bat`, `run_quick_test.bat`, `run_pipeline.bat`, `run_app.bat`, `first_run_test.bat` (non interattivo, log in `logs\first_run.log`).
- Variabili d'ambiente: `IRE_CONFIG` (percorso config), `IRE_SEC_USER_AGENT` (sovrascrive `[sec] user_agent`), `IRE_TEST_PAGE` (usata solo dai test UI).
- `config.local.toml` (accanto a config.toml, in .gitignore): impostazioni personali, sovrascrive config.toml. `python -m ire configure [--check]`.
- Exit code di `python -m ire run`: 0 ok, 1 errore, 2 config non valida, 3 esecuzione incompleta (`degraded`).

## 4. Struttura dei file
- `config.toml` — tutte le impostazioni, commentate in italiano:
  - `[general]` base_currency EUR, data_dir;
  - `[sec]` user_agent (vuoto), max_requests_per_second 8;
  - `[universe]` mode standard, liquidità minima 2M$/giorno, cap minima quick 20 mld / standard 2 mld / full 300 mln;
  - `[cache]`, `[valuation]` (erp 0.05, terminal_growth 0.025, dcf_years 10);
  - `[scoring]` min_peer_group 8, deep_analysis_top_n 150, deep_analysis_max 400, pesi .30/.25/.20/.15/.10;
  - `[portfolio]` target 20, max .08, min .025, settore .25, correlazione .80, penalità .5, percentile 70, min_positions 12, holding_bonus 3, no_trade_band .015, min_weeks_3y 130, `max_region_weight` (NA .65, Europa .45, UK/Giappone/APAC .20, Altro .10).
- `requirements.txt`, `pytest.ini`, `README.md`, `.gitignore` (esclude data/, logs/, *.db, .venv/, cache).
- `setup.bat`, `run_pipeline.bat`, `run_quick_test.bat`, `run_app.bat`, `first_run_test.bat` — launcher Windows (`chcp 65001`, UTF-8, CRLF).
- `scripts/dev_offline_run.py` — pipeline completa offline sul mondo sintetico. `scripts/dev_ui_test.py` — renderizza le 7 pagine con AppTest.
- `ire/__main__.py` — CLI. `ire/config.py` — caricamento TOML. `ire/db.py` — schema SQLite, `upsert`, `upsert_merge`, `init_db`, `log`.
- `ire/http.py` — client HTTP con cache, limiter e retry. `ire/sources/sec.py`, `yahoo.py`, `fx_macro.py` (`FxTable`, `RATE_SERIES`), `universe_intl.py`.
- `ire/universe.py` — universo (SEC NYSE/Nasdaq 1 ticker per CIK, internazionali, liquidità, pre-filtro cap con SEC frames, metadati Yahoo, deduplica).
- `ire/classify.py` — settori, `SECTOR_IT`, `is_banklike` (incluso "REIT - Mortgage"), `is_reit`, `region_for`.
- `ire/prices.py` — `PriceStore`, `main_unit`.
- `ire/normalize/concepts.py` (voci ↔ concetti XBRL/Yahoo in ordine di priorità), `model.py` (`Financials`), `sec_facts.py`, `yahoo_facts.py`.
- `ire/metrics.py` — `MetricSet` (valore, kind, periodo, metodo, input), Altman, Beneish, Piotroski.
- `ire/valuation.py` — reverse DCF, multipli storici, verdetto. `ire/risk.py` — volatilità, drawdown, beta, `BENCHMARKS`.
- `ire/analysis.py` — `analyze_company` (capitalizzazione con valuta e ADR, metriche, rischio, DCF, `fundamental_flags`).
- `ire/qualitative.py` — flag 8-K/NT e testo 10-K/20-F (`text_red_flags`, diff dei Risk Factors).
- `ire/scoring.py` — pilastri, `score_universe`, `classify_row`, `verdict_for`, `confidence_of`, costanti delle classi.
- `ire/thesis.py`, `ire/glossary.py` (86 voci, 32 con spiegazione vuota), `ire/portfolio.py`, `ire/changes.py`, `ire/pipeline.py`, `ire/selfcheck.py`.
- `app/app.py` (UI Streamlit, 7 pagine: Panoramica, Scheda azienda, Confronta, Classifica e filtri, Portafoglio, Cambiamenti e watchlist, Dati e metodologia), `app/data.py`, `app/charts.py`.
- `tests/`: `conftest.py` (fixture `world`), `test_methodology.py`, `test_financials_redteam.py`, `test_data_sources.py`, `test_config_changes.py`, `test_ui_labels.py` (AppTest).
- `tests/fixtures/builders.py` (companyfacts SINTETICI), `tests/fixtures/fake_world.py` (mondo sintetico; US05 ha 8-K 4.02 e material weakness), `tests/test_sec_normalize.py`, `tests/test_pipeline_offline.py`.

Sul PC dell'utente (`C:\Users\aless\source`) esistono:
- una copia VECCHIA del progetto in `investment-engine\`;
- file inutili `ire_p00.txt … ire_p07b.txt` e `ire_verify.bat`, residui di un vecchio trasferimento.

La fonte di verità è GitHub.

## 5. Decisioni prese e motivazioni
- Il motore gira sul PC dell'utente: il sandbox cloud riceve 403 dal proxy verso le fonti dati. Non aggirare il proxy.
- Python + SQLite + Streamlit + Plotly: gratuiti, installabili con un `.bat`, UI web locale semplice. Alternative scartate: da definire.
- Due livelli di dato: A = SEC XBRL (provenienza per filing, restatement, storico lungo); B = Yahoo (≈4-5 anni). Il tier abbassa la confidenza.
- Periodi identificati dalla data di FINE periodo, non dal campo `fy` di companyfacts. L'ultimo valore depositato vince; il primo resta come `original_value`.
- Mai inventare: valori mancanti = NaN. Solo voci minori mai riportate valgono 0, con nota di assunzione.
- Percentili relativi al settore, 5 pilastri, 5 schemi di pesi, punteggio robusto = mediana, `rank_spread` = dipendenza dai pesi.
- Valutazione: nessun "fair value" unico presentato come fatto. Il reverse DCF dice quale crescita sconta il prezzo; premio per il rischio 5% e crescita perpetua 2,5% dichiarati come ASSUNZIONI. Più segnali, con confidenza.
- Portafoglio non top-N né media-varianza (per robustezza, dichiarato in `port["method"]`): selezione greedy con penalità di correlazione, pesi 50% uguali + 50% inverso volatilità ±30% per punteggio, poi least-squares vincolato (SLSQP); rischio in EUR con Ledoit-Wolf.
- Mid-rank: con `rank(pct=True)` le metriche "più basso = meglio" erano penalizzate. Il vecchio gruppo "Universo intero" era un insieme di avanzi di settori diversi: sostituito.
- Analisi testuale solo per filer SEC: nessuna API gratuita standard per i report annuali europei e asiatici.
- Etichette prudenti ("Possibile sottovalutazione temporanea", "Opportunità da verificare"): non presentare stime come fatti.
- Trasferimento sul PC: prima copia-incolla base64 in Blocco note, poi `device_commit_files` sulla cartella collegata `C:\Users\aless\source` (molto più affidabile).

## 6. Vincoli e preferenze
- Lingua: risposte all'utente e testi UI in ITALIANO; codice e commenti in inglese, stringhe utente in italiano.
- Autonomia: decidere da soli, senza chiedere conferme. ECCEZIONE: installare o scaricare software sul PC e usare l'email dell'utente richiedono un suo sì esplicito.
- Niente dati finti, numeri hardcoded presentati come reali o mockup. Meglio un sistema più piccolo ma vero; robustezza prima delle nuove funzioni.
- Trasparenza: per ogni dato fonte, data, metodo e assunzione; campo `kind` (osservato / calcolato / stima / stima di terzi / assunzione); conflitti tra fonti nella tabella `conflicts`.
- Scetticismo attivo: survivorship bias, look-ahead, valute, split, ADR, GAAP/IFRS, metriche non confrontabili.
- Red team con subagent di ruoli diversi, correzioni, seconda verifica; test end-to-end obbligatorio.
- Budget: piano Claude Pro. Non inventare consumi di token; essere concisi; niente lavoro cosmetico.
- Report finale con SOLO queste sezioni: 1 COSA HAI COSTRUITO, 2 PERCHÉ HAI SCELTO QUESTO PROGETTO, 3 COSA PUÒ FARE, 4 COSA HAI ANALIZZATO, 5 METODOLOGIA, 6 LIMITAZIONI, 7 RISULTATI DEL RED TEAM, 8 COME POSSO USARLO, 9 COME POTREBBE EVOLVERE.
- Privacy e sicurezza:
  - l'email dell'utente non va MAI scritta in file versionati (né nel codice né qui); il contatto SEC vive solo sul PC (`config.toml` locale o `config.local.toml`); nel repo `user_agent = ""`;
  - non toccare i file personali dell'utente; mai cancellare suoi file; nessuna operazione di trading.
- Git: commit con trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; push su `main` a ogni blocco completato.

## 7. Problemi noti e bug aperti
Corretti (non riaprire senza motivo): tutta la vecchia lista §7 punti 1-9 e i rilievi del secondo red team (vedi commit "Red team 2 (parte 1/2/3)").
Aperti / limitazioni dichiarate (in METHODOLOGY.md §13):
- Debiti a breve: ShortTermBorrowings e CommercialPaper non vengono sommati (possibile inclusione reciproca): può sottostimare.
- Capex sintetico: se il totale PP&E esclude il petrolio&gas ma è maggiore delle altre voci, si prende solo il totale.
- Bias di sopravvivenza dell'universo internazionale; tasso di sconto uguale per valuta; capitale investito semplificato; FRED mensile; 6-K non analizzati; analisi testuale lessicale.
- Le tabelle `companies` e `facts` sono globali: una run fallita può modificare esclusioni e fatti mostrati accanto ai punteggi dell'ultima run completa.
- Formati numerici: le colonne `st.column_config` usano il formato di Streamlit (punto decimale).
- Progetto sotto OneDrive = rischio per SQLite (non verificato).
Esito della riverifica (subagenti): da aggiornare quando arriva.

## 8. Prossimi passi (in ordine)
1. Chiudere i rilievi della riverifica del red team 2 (se presenti) con test; pytest + dev_offline_run + dev_ui_test verdi; commit e push.
2. Quando l'utente incolla `logs\first_run.log` dal PC: correggere ciò che emerge dai dati reali (SEC, Yahoo, BCE, FRED, Wikipedia), con test che riproducono il caso senza rete.
3. Poi `run_quick_test.bat` → `run_app.bat` sul PC e correzioni.
4. Report finale all'utente con le sole 9 sezioni (§6).

## 9. Domande aperte (decide l'utente)
1. RISOLTA: Python 3.12 sul PC autorizzato (installazione fatta dall'utente con winget).
2. RISOLTA: contatto SEC già nel config.toml sul PC dell'utente; nel repository `user_agent = ""` e l'email non va MAI in file versionati. `configure` ora scrive in `config.local.toml` (escluso da git).
3. Visibilità del repository GitHub: da verificare.
4. Aggiungere al repo la specifica originale `attachment.txt`? Da definire.
5. Modalità predefinita (`quick` / `standard` / `full`): oggi `standard`, mai confermata dall'utente.
6. Cancellazione dei file `ire_p*.txt` e `ire_verify.bat` in `C:\Users\aless\source` (può farlo solo l'utente).
