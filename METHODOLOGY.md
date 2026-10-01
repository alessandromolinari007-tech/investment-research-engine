# Metodologia

Questo documento descrive **cosa fa il motore, con quali dati e con quali ipotesi**. Ogni numero mostrato
nell'interfaccia ha fonte, data, metodo e tipo (dato osservato, calcolato, stima, stima di terzi, assunzione):
lo trovi nella scheda azienda, sezioni *Tutte le metriche* e *Fonti e dati*.

> È uno strumento di **ricerca**, non di consulenza. I punteggi servono a scegliere *cosa studiare*, non
> *cosa comprare*. Nessun modello qui dentro prevede i prezzi futuri.

I valori indicati come "predefiniti" sono le impostazioni di `config.toml` e si possono cambiare.

---

## 1. Fonti dei dati (tutte gratuite e pubbliche)

| Fonte | Cosa forniamo da lì | Note |
|---|---|---|
| **SEC EDGAR** (`companyfacts` XBRL, `submissions`, `frames`, documenti 10-K/20-F) | Bilanci delle società registrate alla SEC (USA e straniere che depositano 20-F/40-F), elenco dei filing, eventi 8-K, testo dei report annuali | Fonte primaria. Richiede un contatto nel User-Agent (in `config.local.toml`, mai pubblicato). Massimo 10 richieste al secondo (predefinito 8). |
| **Yahoo Finance** (libreria `yfinance`) | Prezzi giornalieri, split, settore/industria, valuta, azioni in circolazione, stime degli analisti; bilanci delle società NON registrate alla SEC | Fonte non ufficiale: può cambiare o limitare le richieste senza preavviso. |
| **BCE** (`eurofxref-hist.zip`) | Cambi ufficiali di riferimento contro euro | Le valute non pubblicate dalla BCE usano le coppie Yahoo (`EURxxx=X`), con la fonte indicata. |
| **FRED** (Federal Reserve di St. Louis) | Rendimento dei titoli di Stato a 10 anni per valuta (USD giornaliero `DGS10`, altre valute mensili OCSE) | Usato come tasso privo di rischio. Le serie mensili sono in ritardo di qualche settimana. |
| **Wikipedia** | Composizione attuale dei principali indici non USA | Più un elenco di partenza nel repository (`ire/data/international_seed.csv`). |

Due **livelli di qualità** dei bilanci:

- **A — filing SEC (XBRL)**: storico lungo, provenienza per singolo filing, riesposizioni tracciate.
- **B — Yahoo**: circa 4-5 anni, voci standardizzate dal fornitore di Yahoo (le definizioni possono differire
  dal bilancio ufficiale). Il livello B **abbassa di un livello l'affidabilità** del punteggio (una sola volta, anche
  se coincide con storico breve e minore copertura delle metriche: sono la stessa debolezza).

Quando le due fonti sono disponibili per la stessa società (livello A), le voci principali dell'ultimo anno
vengono confrontate: le differenze sopra il 2% sono registrate tra le *discrepanze tra fonti* con una spiegazione
probabile. Si usa sempre il dato SEC.

## 2. Universo investibile

Un titolo entra nell'analisi se è:

1. un'**azione ordinaria** di una società operativa (esclusi ETF, fondi, SPAC — SIC 6770 —, privilegiate, warrant, unit);
2. quotato su **NYSE/Nasdaq** oppure membro di un indice principale non USA (FTSE MIB, DAX, CAC 40, AEX, IBEX 35,
   SMI, FTSE 100, OMXS30, OMXC25, OMXH25, Nikkei 225, S&P/TSX 60, S&P/ASX 200, Hang Seng) o dell'elenco di partenza;
3. **liquido**: mediana del controvalore giornaliero degli ultimi 3 mesi ≥ soglia (predefinito 2 milioni di USD);
4. sopra la **capitalizzazione minima** della modalità: `quick` ≥ 20 mld $, `standard` ≥ 2 mld $, `full` ≥ 300 mln $;
5. **una sola quotazione per società** (ADR e quotazione domestica deduplicate; si preferisce quella con filing SEC);
6. con un prezzo recente (un ultimo prezzo vecchio indica sospensione o delisting).

Ogni titolo escluso ha il **motivo** registrato (pagina *Dati e metodologia → Esclusioni*). I titoli che aggiungi
a mano (watchlist, il tuo portafoglio) vengono sempre analizzati, ma entrano nella *proposta* di portafoglio
solo se rispettano la soglia di liquidità.

## 3. Bilanci: normalizzazione

- **Periodi identificati dalla data di fine periodo**, non dal campo `fy` di companyfacts (che indica l'anno del
  filing). Anno fiscale = fatto di durata 330-400 giorni da un modulo annuale (10-K, 20-F, 40-F e rettifiche).
- **Riesposizioni**: se più filing riportano lo stesso periodo vince l'ultimo depositato; il primo resta come
  `original_value` e le differenze sopra lo 0,5% sono marcate `restated`.
- **TTM** (ultimi 12 mesi) per i flussi = ultimo anno fiscale + progressivo dell'anno in corso − progressivo dello
  stesso periodo dell'anno prima (dai 10-Q). Senza trimestrali (molti 20-F) TTM = ultimo anno fiscale, e l'età del
  dato è segnalata.
- **Split**: azioni e utile per azione depositati prima di uno split sono rettificati (solo filer domestici).
- **Capex** = totale degli investimenti in impianti; se la società riporta voci separate (es. pozzi petroliferi e
  "altri impianti", sviluppo immobiliare, macchinari) vengono **sommate** (un totale maggiore o uguale alle voci è
  considerato già comprensivo). L'acquisto di immobili è un'acquisizione, non capex.
- **Debito** = debito a lungo termine (quote correnti incluse) + debiti a breve, evitando di contare due volte le
  quote correnti. Per i debiti a breve (finanziamenti a breve e carta commerciale) non si sommano voci che potrebbero
  includersi a vicenda: in caso di dubbio si prende la voce maggiore (scelta prudente, può sottostimare).
- **Ultimo bilancio**: è quello con la data più recente del totale attivo. Una voce non più riportata a quella data
  non viene usata con il valore di anni prima: le voci minori valgono 0 (assunzione); il debito sparito vale 0 solo
  se non ci sono interessi passivi (segnalazione `ASSUMED_DEBT_REPAID`), altrimenti è sconosciuto.
- **Senza utile operativo** (molte società energetiche e industriali): EBIT = utile ante imposte + interessi passivi,
  EBITDA = EBIT + ammortamenti, sempre sullo stesso periodo.
- **Valuta di bilancio** = quella dell'ultimo bilancio annuale (gestisce chi è passato, ad esempio, da USD a EUR).
- **Passività totali**: se la voce non è riportata, totale passivo e patrimonio − patrimonio netto.
- **Voci mai riportate**: solo voci minori (dividendi, riacquisti, acquisizioni, avviamento, interessi di
  minoranza…) valgono 0, con nota di *assunzione*. **Debito, capex e compensi in azioni non sono mai assunti a 0**:
  restano mancanti e le metriche che li usano non vengono calcolate. Il debito è assunto 0 solo se non ci sono
  interessi passivi e le passività non correnti sono sotto il 15% dell'attivo (segnalazione `ASSUMED_ZERO_DEBT`).
- **Leasing**: per i filer IFRS (IFRS 16) i debiti per leasing sono aggiunti al debito finanziario e i rimborsi di
  leasing sottratti dal free cash flow; per i filer US GAAP lo stesso vale per i **leasing finanziari** (il cui costo,
  come per IFRS 16, è sotto l'EBITDA), salvo che il concetto di debito li includa già. I leasing operativi US GAAP
  restano esclusi (il loro costo è già nell'EBITDA).
- **Split**: rilevati anche i rapporti 3:2, 5:4, 4:3, 5:2; se il numero di azioni salta in modo compatibile con uno
  split non rettificato, le metriche per azione e la diluizione non vengono calcolate.
- **Azioni privilegiate** escluse dal patrimonio degli azionisti ordinari (P/B, ROE).

## 4. Prezzi, valute e capitalizzazione

- Prezzi giornalieri Yahoo (chiusura e chiusura rettificata per dividendi), salvati nel database. Pence, centesimi
  di rand e agorot sono convertiti nell'unità principale.
- **Aggiornamento dei prezzi**: ogni giorno si scaricano solo le ultime settimane e si confrontano con quelle già
  salvate. Se Yahoo ha ricalcolato lo storico (dopo uno split o un dividendo, scarto > 0,5%) o compare uno split
  nuovo, l'intero storico viene riscaricato; quello vecchio resta finché il nuovo non è arrivato. Lo storico
  completo viene comunque riscaricato ogni 7 giorni (predefinito). La barra del giorno corrente non viene salvata
  (può essere un prezzo intraday). Un download vuoto (spesso un limite di richieste di Yahoo) non viene mai
  considerato "fresco".
- **Cambi**: si usa il cambio dello stesso giorno o, se manca, l'ultimo disponibile **al massimo 10 giorni prima**.
  Una valuta che la BCE non pubblica più non viene usata con un cambio vecchio (la freschezza si misura separatamente
  per i cambi BCE e per quelli Yahoo). Se i cambi BCE sono fermi da più di 7 giorni l'analisi lo segnala.
- **Capitalizzazione** = prezzo × azioni, nella valuta di bilancio:
  - filer SEC domestici: azioni in circolazione dalla copertina del 10-K/10-Q; se differiscono di oltre il 15% da
    quelle Yahoo (più classi di azioni, dati non aggiornati) si usa Yahoo e la discrepanza è registrata;
  - ADR: il rapporto ADR è stimato confrontando le azioni ordinarie del filing con quelle Yahoo e arrotondato al
    rapporto standard più vicino; se non corrisponde a nessuno, viene segnalato;
  - se la valuta di quotazione è diversa da quella di bilancio, conversione al cambio più recente.
- Per confronti tra società di paesi diversi (classifica, filtri, panoramica) la capitalizzazione è convertita in EUR.

## 5. Metriche (circa 80)

Ogni metrica ha valore, periodo, formula in parole e input. Esempi delle principali:

| Area | Metrica | Formula |
|---|---|---|
| Valutazione | Rendimento operativo (EBIT/EV) | EBIT TTM / enterprise value (EV = capitalizzazione + debito + minoranze + azioni privilegiate − cassa − investimenti a breve) |
| | Rendimento operativo dopo le tasse | EBIT × (1 − aliquota effettiva) / EV, confrontabile con i titoli di Stato |
| | FCF yield netto stock option | (free cash flow − compensi in azioni) / capitalizzazione |
| | P/E, EV/EBIT, EV/EBITDA, EV/ricavi, P/B, P/TBV (banche), P/FFO (REIT) | calcolati solo con denominatore positivo |
| | Multipli vs propria storia | percentile del multiplo attuale tra i multipli di fine anno fiscale (almeno 4 anni), calcolati con la stessa base di azioni della capitalizzazione attuale |
| Qualità | ROIC | NOPAT / capitale investito medio; NOPAT = EBIT × (1 − aliquota effettiva limitata 0-35%; 21% se non calcolabile, dichiarato come assunzione); capitale investito = attivo − cassa − investimenti a breve − passività correnti non finanziarie |
| | Conversione in cassa | mediana di FCF / utile netto negli anni con utile positivo |
| | Accruals (Sloan) | (utile netto − flusso di cassa operativo) / attivo medio |
| Crescita | CAGR ricavi, utile per azione, FCF per azione | crescita annua composta su 3, 5, 10 anni (estremi positivi) |
| Solidità | Debito netto / EBITDA | non calcolabile se EBITDA ≤ 0: in quel caso, con debito netto positivo, il punteggio è il peggiore possibile |
| | Copertura interessi, liquidità corrente, Piotroski F-score, Altman Z, Beneish M-score | definizioni standard |
| Allocazione del capitale | Variazione del numero di azioni, compensi in azioni / ricavi, shareholder yield, acquisizioni / FCF | |
| Rischio (in EUR) | Volatilità 1 e 3 anni, perdita massima 5 e 10 anni, beta verso MSCI World, momentum 12-1 mesi | rendimenti totali convertiti in euro (anche il momentum) |

Banche e assicurazioni hanno metriche dedicate (ROE, ROA, patrimonio/attivo, P/TBV); i margini e il ROIC non
vengono calcolati per loro.

## 6. Punteggi relativi al settore

1. **Gruppo di confronto**: il settore (Yahoo o, in mancanza, dal codice SIC). Banche, assicurazioni e altri
   intermediari finanziari sono gruppi separati. Se un gruppo ha meno di 8 società (predefinito), le finanziarie
   si confrontano con tutta la finanza e le altre con **tutto l'universo non finanziario**. Le metriche di
   valutazione tipiche dei REIT (P/FFO, rendimento da dividendo, EV/EBITDA) si confrontano solo tra REIT (se sono
   almeno 5). Le "mediane dei pari" mostrate usano la stessa popolazione dei percentili.
2. **Percentile a rango medio**: `p = (rango − 0,5) / n`. Così "più alto = meglio" e "più basso = meglio" sono
   esattamente speculari e nessuna società risulta 0 o 100 per costruzione.
3. **Campioni piccoli**: servono almeno 5 società con il dato; sotto 10 il percentile viene avvicinato a 50 in
   proporzione (`50 + (p − 50) × n/10`). Un dato mancante non viene mai stimato.
4. **Casi limite**: debito netto con EBITDA ≤ 0 e debito con patrimonio ≤ 0 ricevono il valore peggiore; una cassa
   netta molto grande rispetto a un EBITDA piccolo è limitata (debito netto/EBITDA ≥ −5).
5. **Cinque pilastri** (media pesata dei percentili delle loro metriche): Qualità, Crescita, Solidità finanziaria,
   Valutazione (100 = molto economica), Allocazione del capitale. Un pilastro richiede almeno metà del peso delle
   sue metriche, altrimenti è "insufficiente". Se una metrica a 5 anni manca si usa la versione breve (es. ROIC
   ultimo anno) senza contarla due volte.
6. **Punteggio robusto** = mediana del punteggio composito sotto **5 schemi di pesi** (configurato, uguali,
   orientato a qualità, a valore, a crescita). Servono Qualità, Valutazione e almeno 3 pilastri su 5: la stessa
   regola vale per tutti gli schemi. L'**instabilità del rango** misura quanto la posizione dipende dai
   pesi. Attenzione: questa robustezza riguarda solo i pesi, non gli errori nei dati.
7. Senza capitalizzazione o con bilanci più vecchi di 550 giorni la società non riceve punteggio e non entra nei
   gruppi di confronto delle altre.

## 7. Classificazione

Le etichette descrivono **cosa mostrano i numeri rispetto ai pari**, non cosa farà il titolo.

| Etichetta | Regola (Q = qualità, V = valutazione, G = crescita) |
|---|---|
| **Red flag: approfondire** | una segnalazione grave, oppure una bloccante (going concern, debolezza materiale nei controlli, bilanci non affidabili 8-K 4.02, bancarotta, delisting, accelerazione del debito, forte diluizione, leva o copertura interessi critiche), oppure due segnalazioni di gravità alta |
| **Possibile sottovalutazione temporanea** | come "Qualità a sconto vs pari", e in più multipli bassi rispetto alla propria storia oppure prezzo ≥ 25% sotto il massimo a 3 anni |
| **Qualità a sconto vs pari** | Q ≥ 70, V ≥ 65, economicità **sui soli multipli vs pari** ≥ 60, nessun segnale di deterioramento né di cautela |
| **Possibile value trap** | V ≥ 70 e (Q < 40, oppure segnali di deterioramento, oppure margini ai massimi del ciclo) |
| **Qualità in deterioramento** | Q ≥ 70 con segnali di deterioramento |
| **Qualità a prezzo pieno** | Q ≥ 70, V < 40 |
| **Qualità a prezzo ragionevole** | Q ≥ 70 (altri casi) |
| **Crescita costosa** | G ≥ 70, V < 35 |
| **Economica** | V ≥ 70 con qualità nella media |
| **Nella media** / **Dati insufficienti** | negli altri casi / senza punteggio |

**Segnali di deterioramento** (nel tempo): ricavi in calo nell'ultimo anno, margini in peggioramento, debito in forte
aumento, FCF negativo, perdite persistenti; e su più anni: ricavi in calo da 5 anni (o oltre −3% l'anno da 3 anni),
margine operativo 3-5 punti sotto la mediana a 5 anni, FCF per azione in calo oltre il 5% l'anno. Una singola
segnalazione di gravità alta impedisce le etichette positive.

**Segnali di cautela** (non sono un peggioramento, ma rendono dubbio che un prezzo basso sia un vero sconto): crescita
tra le più deboli del settore (pilastro crescita < 25), margini ai massimi del ciclo, segnalazioni finanziarie medie
(leva, copertura interessi, Altman, dividendo non coperto, diluizione, accruals, Beneish, depositi tardivi, cambio del
revisore, riesposizioni). Impediscono le etichette "a sconto" ma non escludono dal portafoglio.

## 8. Valutazione: verdetto e reverse DCF

Non viene mai presentato un "valore giusto" unico come fatto. Il verdetto combina **4 segnali**:

1. **Rispetto ai concorrenti**: solo i multipli rispetto ai pari (punteggio `valuation_peers`): ≥ 65 economica, ≤ 35 costosa.
2. **Rispetto alla propria storia**: percentile medio dei multipli attuali (P/E, EV/EBIT, P/FCF) nella storia della
   società: ≤ 30% economica, ≥ 70% costosa.
3. **Crescita implicita nel prezzo** (reverse DCF) contro la crescita storica del FCF: scarto di ±3 punti.
4. **Rendimento contro i tassi** (peso 0,5): rendimento operativo dopo le tasse ≥ titolo di Stato + 3 punti →
   economica; ≤ titolo di Stato + 0,5 punti → costosa.

Se il margine attuale è oltre 1,5 volte la mediana del ciclo (`CYCLICAL_PEAK`) i segnali 2 e 3 vengono mostrati ma
non contati: multipli bassi su utili di picco non indicano economicità.

I segnali 1 e 4 guardano entrambi il prezzo rispetto agli utili attuali: per la confidenza contano come **una sola
evidenza**. **Confidenza del verdetto**: *alta* con almeno 3 evidenze indipendenti, almeno il 75% del peso a favore
e nessun segnale opposto; *media* con almeno 2 evidenze e almeno il 50% a favore senza opposti; altrimenti *bassa*.
Nell'interfaccia la "confidenza del verdetto" è distinta dall'"affidabilità dei dati" del punteggio.

**Reverse DCF** — risponde a: *quale crescita del free cash flow giustifica il prezzo attuale?*

- FCF di partenza = media degli ultimi 3 anni (l'ultimo sostituito dal TTM se più recente); non calcolato se
  anche uno solo di questi anni è ≤ 0.
- Tasso di sconto = tasso privo di rischio della valuta di bilancio (titolo di Stato a 10 anni, FRED) + premio per
  il rischio azionario del **5% (assunzione)**. Se il tasso manca si usa 3,5%, dichiarato come assunzione.
- Crescita esplicita per **10 anni**, poi crescita perpetua del **2,5% (assunzione)**, mai superiore al tasso privo di
  rischio della valuta.
- La crescita implicita è cercata tra −50% e +100% l'anno; fuori da questo intervallo non viene riportata.
- Nella scheda azienda puoi cambiare le ipotesi e vedere quanto cambia il risultato.

## 9. Segnalazioni (red flag)

- **Dai numeri**: leva elevata, copertura interessi bassa, FCF negativo, margini in calo, Altman Z in zona di
  stress, Beneish M-score, accruals alti, compensi in azioni elevati, avviamento elevato, debito in forte aumento,
  ricavi in calo, diluizione, dividendo non coperto, perdite persistenti, bilanci vecchi o storico breve,
  valori riesposti.
- **Dagli eventi SEC** (ultimi 24 mesi): 8-K item 1.03 (bancarotta), 4.02 (bilanci non affidabili), 3.01 (delisting),
  2.04 (accelerazione del debito), 4.01 (cambio revisore), 2.06 (svalutazioni), 1.05 (cyber), depositi tardivi
  (NT 10-K/10-Q/20-F), molti cambi di dirigenti.
- **Dal testo dei report annuali** (solo filer SEC, sulle prime 150 società per punteggio più tutte quelle
  candidabili al portafoglio, fino a 400): analisi **frase per frase** che scarta le frasi ipotetiche ("may",
  "could", "if"…) e quelle negate. Cerca dubbi sulla continuità aziendale, debolezze materiali nei controlli,
  riesposizioni, cambio del revisore, svalutazioni di avviamento; registra concentrazione dei clienti, fornitori
  unici, esposizione geografica e quanto sono cambiati i fattori di rischio rispetto all'anno prima. Ogni
  segnalazione riporta la frase e il link al documento. **Va sempre verificata leggendo il documento.**
- Per le società non registrate alla SEC l'analisi del testo **non è disponibile** (non esiste un'API gratuita
  standard per i report annuali europei e asiatici) e l'interfaccia lo dice esplicitamente.

## 10. Portafoglio proposto

Non è una selezione dei primi N e non è un'ottimizzazione media-varianza (troppo sensibile agli errori di stima).

1. **Candidati**: punteggio robusto ≥ 70° percentile (predefinito), affidabilità dei dati non bassa, nessuna classe
   tra Red flag, Possibile value trap, Qualità in deterioramento, Dati insufficienti, nessun verdetto "costosa" con
   confidenza media o alta; almeno 130 settimane di prezzi negli ultimi 3 anni (predefinito). Se i candidati non bastano la soglia scende a 60 e poi a 50, e il log lo dice.
2. **Selezione greedy**: si aggiunge il miglior punteggio corretto per la correlazione media con i titoli già scelti;
   correlazione massima tra due titoli 0,80; limiti al numero di titoli per settore e area; un piccolo bonus ai
   titoli già presenti nella proposta precedente (meno rotazione).
3. **Pesi**: 50% uguali + 50% inversamente proporzionali alla volatilità, inclinati del ±30% in base al punteggio, poi
   i pesi più vicini che rispettano i limiti (minimi quadrati vincolati): peso per titolo tra 2,5% e 8%, settore
   ≤ 25%, aree (Nord America ≤ 65%, Europa ≤ 45%, Regno Unito, Giappone, Asia-Pacifico ≤ 20%, Altro ≤ 10%).
4. **Vincoli dichiarati**: ogni vincolo è mostrato come configurato vs effettivo. Se non è rispettabile (troppo pochi
   candidati diversificati) un programma lineare trova l'**allentamento minimo** dei soli vincoli che lo richiedono
   (alzare il limite di un singolo titolo "costa" il triplo di un limite di settore o area), e il vincolo è
   **riportato come NON rispettato**, mai violato in silenzio.
5. **Sotto 12 titoli** (predefinito) il portafoglio è **"non proposto"**; se i limiti sono superati di molto (oltre 4
   punti per titolo o 10 punti per settore/area) è **"concentrato"**: in entrambi i casi è solo un elenco di candidati.
6. **Banda di non intervento**: i titoli il cui nuovo peso differisce meno di 1,5 punti dalla proposta precedente
   mantengono **esattamente** il peso precedente (nessun micro-ordine); la rotazione e i titoli usciti sono mostrati.
7. **Rischio** misurato in EUR su rendimenti settimanali a 3 anni, covarianza con shrinkage di Ledoit-Wolf verso una
   **correlazione costante** (lo shrinkage verso l'identità sottostima il rischio di un portafoglio azionario); un
   titolo con meno di 52 settimane di prezzi recenti è escluso dal calcolo del rischio e segnalato:
   volatilità attesa, numero effettivo di titoli, rapporto di diversificazione, contributo al rischio, beta e
   perdita nel peggior trimestre del benchmark (iShares Core MSCI World in EUR, SWDA.MI).

Il grafico "comportamento storico dei pesi attuali" **non è un backtest**: i titoli sono scelti oggi con i dati di
oggi (distorsione da senno di poi e da sopravvivenza).

## 11. Esecuzioni incomplete

Se un'analisi termina con troppi dati mancanti o vecchi — bilanci ottenuti per meno di metà delle società, nessun
prezzo per oltre il 20% dei titoli, dati SEC serviti dalla cache scaduta, cambi BCE fermi, o molte meno società con
punteggio rispetto all'analisi precedente della stessa modalità — viene registrata come **incompleta**: i suoi dati
restano nel database, ma l'interfaccia, il portafoglio e i confronti continuano a usare l'ultima analisi completa.
Due analisi non possono girare contemporaneamente.

## 12. Cosa è cambiato

Ogni nuova analisi viene confrontata con l'ultima completata **nella stessa modalità**: cambi di classificazione,
di verdetto, di punteggio (≥ 10 punti), peggioramenti di margini, crescita, ROIC, leva, diluizione e copertura
interessi, nuove segnalazioni. Una segnalazione dal testo dei report conta come "nuova" solo se il report era stato
analizzato anche la volta prima. Le esclusioni dovute a problemi di download sono distinte dalle uscite vere
dall'universo.

## 13. Limiti principali

- **Sopravvivenza**: l'universo internazionale usa la composizione *attuale* degli indici; le società fallite o uscite
  non ci sono.
- **Dati Yahoo** non ufficiali e con storico breve; definizioni del fornitore non sempre uguali al bilancio.
- **Tasso di sconto** uguale per tutte le società della stessa valuta (nessun beta o premio specifico).
- **Capitale investito** semplificato; capitalizzazione con azioni in circolazione (non diluite); dividendi dalla
  voce di cassa `PaymentsOfDividends`.
- Serie dei tassi FRED mensili e in ritardo per le valute diverse dal dollaro.
- I moduli 6-K degli emittenti esteri non sono analizzati nel testo.
- L'analisi del testo usa regole lessicali: può sbagliare in entrambe le direzioni; ogni segnalazione va letta
  nel documento originale.
- Nessuna previsione: i punteggi riflettono il passato e il prezzo attuale.
