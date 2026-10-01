# CLAUDE.md — Investment Research Engine

## 1. Obiettivo del progetto
Motore di ricerca azionaria personale per un investitore retail italiano alle prime armi in finanza (base EUR).
Deve scandire il più ampio universo di azioni acquistabili (USA, Europa, UK, Giappone, Canada, Australia, altri) usando SOLO dati pubblici gratuiti, analizzare bilanci, qualità, valutazione (multi-metodo), red flag, confronti tra pari, e proporre un portafoglio motivato posizione per posizione.
Deve essere riusabile nel tempo (ri-scansione, "cosa è cambiato", deterioramenti), trasparente (dato→fonte→data→metodo→assunzione) e comprensibile. Regola più importante: niente dati finti, niente numeri hardcoded, niente mockup.
Specifica originale completa: file `attachment.txt` caricato dall'utente nella prima sessione (19 sezioni). Non è nel repo: da definire se aggiungerlo (vedi §9).

## 2. Stato attuale
Repository: https://github.com/alessandromolinari007-tech/investment-research-engine (ramo `main`). Visibilità del repo: da definire / da verificare.
Test offline: `python -m pytest -q` → 11 passed (Python 3.11, sandbox cloud). `pytest.ini` limita la raccolta a `tests/`.
UI: `scripts/dev_offline_run.py` + `scripts/dev_ui_test.py` → 7 pagine su 7 renderizzate senza eccezioni (DB sintetico, dopo le ultime modifiche).
Ultimo run offline sintetico: soglia percentile abbassata 70→60, portafoglio con 12 posizioni; 3 vincoli riportati come NON rispettati (peso max per titolo 12% vs 8%, settore 41,5% vs 25%, Nord America 88% vs 65%). È atteso: il mondo sintetico è piccolo e concentrato.

### Funziona (testato SOLO offline, con dati sintetici)
- Pipeline completa in 9 stadi: macro (FX BCE + tassi FRED) → universo → prezzi → bilanci → FX extra → analisi → scoring → analisi testuale filing ("deep") → portafoglio → diff con l'analisi precedente.
- Normalizzazione SEC XBRL companyfacts (tier A) e Yahoo statements (tier B), TTM, split, restatement, provenienza di ogni fatto nel DB.
- Circa 80 metriche, scoring relativo al settore, classificazione, verdetto di valutazione, tesi in italiano, red flag, portafoglio, UI Streamlit con 7 pagine.
- Red team (6 subagent: Quant, Financial Analyst, Data Engineer, Portfolio Specialist, Skeptic, UX/Software) eseguito UNA volta. Correzioni in parte applicate (vedi sotto).

### Correzioni del red team GIÀ applicate
- Debito/capex/SBC mai riportati NON più assunti = 0. Il debito è assunto 0 solo se non ci sono interessi passivi e le passività non correnti sono < 15% dell'attivo (flag `ASSUMED_ZERO_DEBT`, severity "data"). Altrimenti `DEBT_UNKNOWN` / `CAPEX_UNKNOWN` / `DEBT_PARTIAL`. Metriche basate su voci assunte hanno `kind="assumption"`.
- Più concetti XBRL per debito/capex. Commercial paper non più persa (`LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` spostato in `debt_lt_total`). Leasing IFRS 16 aggiunti al debito e sottratti dal FCF (solo filer IFRS). Azioni privilegiate escluse da P/B e ROE; nuovo P/TBV per banche. Rimossi i concetti `...BeforeIncomeTaxesDomestic`, D&A IFRS con impairment e `InterestExpenseNet`.
- Yahoo: capex/SBC mancanti restano NaN; un valore positivo di "Net Business Purchase And Sale" (dismissione) dà acquisizioni = 0.
- Scoring: percentile mid-rank `(rank−0.5)/n`; minimo 5 osservazioni; shrinkage verso 50 sotto 10. I settori piccoli si confrontano con tutto l'universo non finanziario, le finanziarie con tutta la finanza. Gruppi separati per banche, assicurazioni e altri intermediari. Fallback senza doppio conteggio (pesi fusi). Valore "peggiore" per debito netto con EBITDA ≤ 0 e per debito con patrimonio ≤ 0; clip di ND/EBITDA a −5. Nuovo punteggio `valuation_peers` (solo multipli vs pari).
- Classificazione: etichette come costanti in `ire/scoring.py` (C_TEMP, C_QDISC, C_QFAIR, C_QFULL, C_QDET, C_TRAP, C_REDFLAG, C_GROWTH, C_CHEAP, C_AVG, C_NODATA). Un singolo flag in `BLOCKING` → "Red flag". Deterioramento anche pluriennale (CAGR ricavi, trend margini, FCF/azione, pilastro crescita < 25). Nuova classe "Qualità in deterioramento". Testi meno assertivi.
- Verdetto: 4 segnali su evidenze diverse (pari / storia / reverse DCF vs crescita / rendimento operativo dopo tasse vs titoli di Stato, soglia rf+3%). Confidenza basata su quanta evidenza sostiene il verdetto e sull'assenza di segnali opposti. Segnali "economici" soppressi se c'è il flag `CYCLICAL_PEAK`.
- Reverse DCF: soluzione fuori intervallo → None. Base FCF solo se tutti gli ultimi anni sono > 0. Crescita perpetua ≤ risk-free della valuta. EV storico calcolato come quello attuale. `growth_gap` usa la crescita del FCF totale (`fcf_cagr_5y`).
- Nuovi flag: `CYCLICAL_PEAK` (margine attuale > 1,5× mediana di ciclo), `ONE_OFF_ITEMS` (info).
- Analisi testuale filing riscritta per frase: scarta frasi ipotetiche e negate, normalizza gli apostrofi curvi, cerca nuovi flag `AUDITOR_CHANGE_TEXT` e `IMPAIRMENT_TEXT`. L'analisi "deep" copre i primi `deep_analysis_top_n` (150) + tutte le società candidabili al portafoglio, fino a `deep_analysis_max` (400). Nel payload: `performed`; nel detail degli score: `deep_analysis`.
- Portafoglio (`ire/portfolio.py` riscritto):
  - idoneità con ≥ `min_weeks_3y` (130) settimane recenti; correlazione ignota = scarto;
  - soglia di percentile abbassata 70→60→50 se servono più titoli; sotto `min_positions` (12) lo stato è "non proposto";
  - SLSQP con limiti per titolo, settore e AREA; ogni vincolo è riportato in `constraints` (configurato vs effettivo, `rispettato`);
  - `holding_bonus`, `no_trade_band`, `turnover`;
  - ruoli con soglie assolute; covarianza senza `fillna(0)`; benchmark sulla stessa finestra; `weighted_geo_mean_mcap_eur`.
- Pipeline/dati:
  - migrazioni colonne; run interrotti marcati `interrupted`;
  - `companies` aggiornata con `upsert_merge` + reset `in_universe=0` a ogni run;
  - companyfacts 404 → fallback Yahoo; circuit breaker dopo 15 errori SEC consecutivi;
  - senza User-Agent SEC gli USA vengono saltati (non bloccano il run).
- HTTP: limiter unico per `*.sec.gov` (con lock); scritture cache atomiche; cache corrotta cancellata e riscaricata; 403 SEC "rate threshold" → attesa e retry; risposta non-JSON su URL `.json` mai in cache.
- Nuove colonne `companies.liquidity_usd`, `market_cap_usd`, `forced`; i ticker aggiunti a mano sotto soglia di liquidità sono esclusi dalla PROPOSTA di portafoglio.
- UI: la pagina Classifica andava in errore se una metrica mancava per tutte le società (ora `app/data.py` crea sempre le colonne di `KEY_METRICS`).

### A metà
- `price_meta.full_fetched_at` è nello schema e nella migrazione, ma `ire/prices.py` NON la usa ancora (§7, bug 1).
- FATTO (blocco 1): `app/app.py` e `ire/changes.py` importano le costanti di classe da `ire/scoring.py`; la sezione "💎" della Panoramica si popola. Test di regressione in `tests/test_ui_labels.py`; fixture `world` spostata in `tests/conftest.py`. La UI non mostra ancora `status`, `constraints`, `turnover` e `exited` del portafoglio.
- README minimale presente. `METHODOLOGY.md` NON esiste: è citato in `ire/normalize/sec_facts.py` e la tab Metodologia è vuota.

### Non ancora iniziato
- Test end-to-end con dati REALI (mai eseguito: SEC/Yahoo/FRED/BCE/Wikipedia non raggiungibili dal sandbox cloud, proxy 403).
- Test unitari per scoring, valutazione, qualitativa, classificazione, vincoli di portafoglio.
- Secondo ciclo di red team (obbligatorio da specifica).
- Report finale all'utente (§6).

## 3. Stack e ambiente
- Python. Sandbox cloud: 3.11.15. PC utente: Python NON installato (verificato). Target: Windows 11.
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
  - non mettere l'email dell'account (`[email rimossa]`) nel User-Agent SEC senza ok esplicito; `user_agent = ""` nel repo;
  - non toccare i file personali dell'utente; mai cancellare suoi file; nessuna operazione di trading.
- Git: commit con trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; push su `main` a ogni blocco completato.

## 7. Problemi noti e bug aperti
1. `ire/prices.py`: dopo il primo run lo storico completo non viene mai riscaricato (`get_recent` aggiorna `fetched_at`, quindi `get_full` lo crede fresco). Gli split successivi rompono le serie e `splits` non si aggiorna. Fix previsto:
   - usare `price_meta.full_fetched_at`;
   - confrontare le date sovrapposte (scarto > 0,5% → riscaricare con `actions=True`);
   - non ereditare `requested_start` da un refresh parziale.
2. Yahoo: con rate limit yfinance 1.7 restituisce frame vuoti senza eccezione, che finiscono in cache come freschi (prezzi 20 h, statements 7 giorni, info 20 h). Fix: non mettere in cache i risultati vuoti, retry con attesa se più del 50% del batch è vuoto, batch più piccoli, `threads=False`.
3. `_download_store`: tutto in memoria (`iterrows`, circa 2,7 GB stimati per 3000 ticker) e commit solo alla fine. Fix: scrittura e commit per batch, `itertuples`/numpy.
4. `fx_macro.py`: il forward-fill rende "fresca" una valuta morta (es. RUB) e il controllo di 10 giorni non vale con `on=None`. Fix: età misurata sull'ultima osservazione reale, `ffill(limit=…)`.
5. `changes.py`: i flag testuali appaiono come "Nuova segnalazione" quando una società entra nel top-N; il diff confronta run di modalità diverse; le esclusioni temporanee sembrano uscite dall'universo; etichette vecchie.
6. `app/app.py`:
   - **Crash**: "Analizza il MIO portafoglio" va sempre in errore (`if hc:` su una Series): passare il risultato a `ire.pipeline._serializable`.
   - **Avvio**: il prompt "Email:" di Streamlit al primo avvio blocca `run_app.bat` (aggiungere `--server.showEmailPrompt false`).
   - **Etichette e campi**: vecchie etichette di classificazione; mostrare `status`, `constraints`, `turnover`, `exited`, `missing_prices` e `valuation_peers`.
   - **Trasparenza ed errori**:
     - glossario mai mostrato (`explain` importato ma non usato);
     - run falliti o interrotti invisibili;
     - "Nessuna segnalazione" mostrato anche quando l'analisi testuale non è stata eseguita: usare `payload.performed` / `detail.deep_analysis`.
   - **Calcoli e valori mostrati**:
     - capitalizzazioni in valute diverse confrontate tra loro (usare `market_cap_eur`);
     - il DCF interattivo mostra "inf" se tasso ≤ crescita;
     - "confidenza None";
     - float grezzi in Confronta;
     - slider con valore iniziale fuori range.
   - **Testi e formato**: `$` interpretato come LaTeX; CSV non adatto a Excel italiano (servono `sep=";"`, `decimal=","`, utf-8-sig); settori non tradotti negli avvisi.
   - **Varie**: tab Metodologia vuota; `compute_changes` non in cache; connessione SQLite condivisa tra sessioni.
7. `.bat`: non verificano che `.venv` esista; `run_app.bat` non ha `pause`; `setup.bat` e `first_run_test.bat` scrivono "Fatto" anche quando un passo fallisce; `first_run_test.bat` non chiama `configure`.
8. Config: un percorso Windows con `\` dà un traceback TOML in inglese; un `mode` non valido passa in silenzio; `configure` scrive "Salvato" senza aver scritto nulla se `user_agent` non è vuoto.
9. `ire/thesis.py`: frasi da ammorbidire ("il mercato si aspetta…"). Verificare che con il mid-rank non compaia più "meglio del 100%".
10. Minori o non confermati:
    - nome file cache = ticker (`CON` è riservato su Windows);
    - codici Nikkei alfanumerici scartati;
    - somma delle classi di azioni su un `set`;
    - capitalizzazione con azioni non diluite;
    - dividendi da `PaymentsOfDividends`;
    - tasso di sconto uguale per tutti;
    - capitale investito semplificato;
    - progetto sotto OneDrive = rischio per SQLite;
    - serie FRED mensili e in ritardo;
    - 6-K non analizzati;
    - bias di sopravvivenza dell'universo internazionale (membri ATTUALI degli indici);
    - la "robustezza" del punteggio riguarda solo i 5 schemi di pesi.
11. PC utente: Python non installato (`first_run_test.bat` → "Python non trovato"); User-Agent SEC non configurato; nessun test reale eseguito.
12. Già risolti in passato:
    - `use_container_width` → `width="stretch"`;
    - SQLite tra thread Streamlit → `check_same_thread=False`;
    - vincolo di settore violato dopo la rinormalizzazione → SLSQP;
    - `CONFIG_PATH` deve essere `Path`, non `str`;
    - pytest raccoglieva `scripts/dev_ui_test.py` → `pytest.ini`.

## 8. Prossimi passi (in ordine)
1. **Verifica (primo passo operativo):**
   - `pip install -r requirements.txt` → `python -m pytest -q` (atteso 11 passed);
   - `python scripts/dev_offline_run.py` (annotare la cartella `DATA`);
   - `IRE_CONFIG=<DATA>/config.toml python scripts/dev_ui_test.py` (atteso 0 eccezioni sulle 7 pagine).
2. Allineare `app/app.py` e `ire/changes.py` alle costanti di `ire/scoring.py` (importarle, niente stringhe duplicate); mostrare i nuovi campi del portafoglio; correggere il crash di "mio portafoglio" e il prompt email di Streamlit (§7 punti 5-6).
3. Correggere `prices.py`, `yahoo.py` e `fx_macro.py` (§7 punti 1-4) con test unitari che simulano yfinance (monkeypatch).
4. Launcher `.bat`, validazione della config, `configure` (§7 punti 7-8); wording di `thesis.py`.
5. Test unitari: `qualitative.text_red_flags` con frasi reali e ipotetiche; `classify_row` (going concern, declino pluriennale); verdetto e confidenza; reverse DCF; mid-rank e pool; vincoli di area e stato "non proposto".
6. `METHODOLOGY.md` in italiano e README più completo; mostrarli nella tab Metodologia.
7. Secondo ciclo di red team con subagent indipendenti; verificare, correggere, ripetere finché è ragionevole.
8. Test end-to-end REALE sul PC (serve §9 punti 1-2): `setup.bat` → `python -m ire check` → `run_quick_test.bat` → correzioni → `run_app.bat`. Se la sessione è collegata al PC, la cartella collegata è `C:\Users\aless\source`; altrimenti l'utente scarica il repo e lancia i `.bat`.
9. Commit e push dopo ogni blocco.
10. Report finale con le sole 9 sezioni (§6).

## 9. Domande aperte (decide l'utente)
1. Ok a installare Python 3.12 sul PC (`winget install Python.Python.3.12`, circa 25 MB) e le librerie da PyPI (circa 150-250 MB in `.venv`)? Chiesto, nessuna risposta.
2. Contatto per lo User-Agent SEC: "Nome Cognome email", oppure ok a usare `[email rimossa]`? Chiesto, nessuna risposta. Senza contatto il motore gira solo sui mercati non USA.
3. Visibilità del repository GitHub: da definire / da verificare.
4. Aggiungere al repo la specifica originale `attachment.txt`? Da definire.
5. Modalità predefinita (`quick` / `standard` / `full`): oggi `standard`, mai confermata dall'utente.
6. Cancellazione dei file `ire_p*.txt` e `ire_verify.bat` in `C:\Users\aless\source` (può farlo solo l'utente).
