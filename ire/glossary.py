"""Human-readable labels, formats and beginner-friendly explanations for every metric."""
from __future__ import annotations

import math

# key: (label, format, short explanation for beginners)
#   formats: pct (0.12 → 12%), x (multiple 12.3×), num, money, ratio01 (0.8 → 80%), score01 (fraction → x/9)
GLOSSARY: dict[str, tuple[str, str, str]] = {
    "market_cap": ("Capitalizzazione", "money", "Quanto vale in Borsa l'intera azienda (prezzo × numero di azioni)."),
    "enterprise_value": ("Enterprise value (EV)", "money", "Prezzo per comprare l'intera azienda: capitalizzazione + debiti − cassa."),
    "net_debt": ("Debito netto", "money", "Debiti finanziari meno la cassa. Negativo = l'azienda ha più cassa che debiti."),
    "pe": ("P/E", "x", "Quanti anni di utili attuali stai pagando. Più basso = più economica (a parità di qualità)."),
    "forward_pe": ("P/E prospettico (stima analisti)", "x", "P/E calcolato sugli utili attesi dagli analisti: è una stima di terzi."),
    "ps": ("P/S", "x", "Prezzo rispetto ai ricavi. Utile per aziende senza utili, ma ignora la redditività."),
    "pb": ("P/B", "x", "Prezzo rispetto al patrimonio netto contabile. Molto usato per banche e assicurazioni."),
    "ev_ebitda": ("EV/EBITDA", "x", "Prezzo dell'intera azienda rispetto al margine operativo lordo."),
    "ev_ebit": ("EV/EBIT", "x", "Prezzo dell'intera azienda rispetto all'utile operativo. Considera anche il debito."),
    "ev_fcf": ("EV/FCF", "x", "Prezzo dell'intera azienda rispetto alla cassa libera generata."),
    "ev_sales": ("EV/Ricavi", "x", "Prezzo dell'intera azienda rispetto ai ricavi."),
    "earnings_yield": ("Rendimento operativo (EBIT/EV)", "pct", "Utile operativo che 'rende' ogni euro pagato per l'azienda. Più alto = più economica."),
    "earnings_yield_equity": ("Rendimento degli utili (E/P)", "pct", "Utile netto diviso capitalizzazione: l'inverso del P/E."),
    "fcf_yield": ("FCF yield", "pct", "Cassa libera generata per ogni euro di capitalizzazione. Più alto = più economica."),
    "fcf_sbc_yield": ("FCF yield al netto delle stock option", "pct", "Come il FCF yield, ma sottrae i compensi pagati in azioni (un costo reale)."),
    "dividend_yield": ("Rendimento da dividendo", "pct", "Dividendi pagati negli ultimi 12 mesi / capitalizzazione."),
    "shareholder_yield": ("Rendimento per l'azionista", "pct", "Dividendi + riacquisti di azioni − nuove emissioni, rispetto alla capitalizzazione."),
    "fwd_eps_growth": ("Crescita utili attesa (analisti)", "pct", "Crescita dell'utile per azione attesa dagli analisti nei prossimi 12 mesi (stima di terzi)."),
    "p_ffo": ("P/FFO (approssimato)", "x", "Per i REIT: prezzo rispetto al flusso da operazioni (utile + ammortamenti)."),
    "ffo_payout": ("Payout su FFO (approssimato)", "pct", "Quota del FFO distribuita come dividendo (REIT)."),
    "gross_margin": ("Margine lordo", "pct", "Quanto resta di ogni euro di ricavi dopo il costo diretto del prodotto. Alto = potere di prezzo."),
    "operating_margin": ("Margine operativo", "pct", "Quanto resta dopo tutti i costi operativi."),
    "net_margin": ("Margine netto", "pct", "Quanto resta dopo tutto, tasse e interessi inclusi."),
    "fcf_margin": ("Margine di free cash flow", "pct", "Cassa libera generata per ogni euro di ricavi."),
    "roe": ("ROE", "pct", "Utile rispetto al capitale degli azionisti."),
    "roa": ("ROA", "pct", "Utile rispetto a tutto l'attivo."),
    "roic": ("ROIC (ultimo anno)", "pct", "Rendimento del capitale investito nel business. >15% stabilmente = segnale di vantaggio competitivo."),
    "roic_5y_median": ("ROIC mediano 5 anni", "pct", "Rendimento tipico sul capitale investito negli ultimi 5 anni."),
    "roe_5y_median": ("ROE mediano 5 anni", "pct", "Rendimento tipico sul capitale degli azionisti."),
    "roa_5y_median": ("ROA mediano 5 anni", "pct", "Rendimento tipico sull'attivo (importante per le banche)."),
    "gross_margin_5y_median": ("Margine lordo mediano 5 anni", "pct", ""),
    "op_margin_5y_median": ("Margine operativo mediano 5 anni", "pct", ""),
    "op_margin_volatility": ("Instabilità del margine operativo", "pct", "Quanto oscilla il margine anno su anno. Basso = business prevedibile."),
    "op_margin_trend": ("Margine operativo vs sua mediana", "pctpt", "Ultimo anno rispetto alla mediana a 5 anni, in punti percentuali."),
    "pct_years_profitable": ("Anni in utile", "ratio01", "Quota di anni con utile positivo."),
    "pct_years_fcf_positive": ("Anni con FCF positivo", "ratio01", "Quota di anni in cui l'azienda ha generato cassa libera."),
    "fcf_conversion": ("Conversione utili in cassa", "x", "FCF / utile netto. Vicino o sopra 1 = utili 'veri', sostenuti da cassa."),
    "accruals_ratio": ("Accruals", "pct", "Parte degli utili non incassata. Alto = qualità degli utili bassa."),
    "equity_to_assets": ("Patrimonio / attivo", "pct", "Cuscinetto di capitale di banche e assicurazioni."),
    "revenue_cagr_3y": ("Crescita ricavi (3 anni, annua)", "pct", ""),
    "revenue_cagr_5y": ("Crescita ricavi (5 anni, annua)", "pct", "Crescita media annua dei ricavi."),
    "revenue_cagr_10y": ("Crescita ricavi (10 anni, annua)", "pct", ""),
    "gross_profit_cagr_5y": ("Crescita utile lordo (5 anni)", "pct", ""),
    "revenue_growth_last_fy": ("Crescita ricavi ultimo anno", "pct", ""),
    "revenue_growth_consistency": ("Costanza della crescita", "ratio01", "Quota di anni in cui i ricavi sono cresciuti."),
    "eps_cagr_5y": ("Crescita utile per azione (5 anni)", "pct", ""),
    "ni_cagr_5y": ("Crescita utile netto (5 anni)", "pct", ""),
    "fcf_ps_cagr_5y": ("Crescita FCF per azione (5 anni)", "pct", ""),
    "fcf_cagr_5y": ("Crescita FCF (5 anni)", "pct", ""),
    "bvps_cagr_5y": ("Crescita patrimonio per azione (5 anni)", "pct", ""),
    "net_debt_ebitda": ("Debito netto / EBITDA", "x", "Anni di margine operativo lordo (EBITDA) necessari a ripagare il debito netto. >3-4 = leva alta (dipende dal settore)."),
    "debt_equity": ("Debito / patrimonio", "x", ""),
    "interest_coverage": ("Copertura interessi", "x", "Quante volte l'utile operativo copre gli interessi. <3 = attenzione."),
    "current_ratio": ("Liquidità corrente", "x", "Attività a breve / passività a breve. <1 = possibili tensioni di liquidità."),
    "quick_ratio": ("Liquidità immediata", "x", "Come sopra ma senza magazzino."),
    "goodwill_to_assets": ("Avviamento+intangibili / attivo", "pct", ""),
    "altman_z": ("Altman Z-score", "num", "Indicatore di rischio fallimento per aziende industriali: <1.8 stress, >3 solido."),
    "beneish_m": ("Beneish M-score", "num", "Indicatore di possibile manipolazione contabile: > −1.78 da approfondire (molti falsi positivi)."),
    "piotroski_f": ("Piotroski F-score", "score01", "9 test sulla salute del bilancio. Più alto = fondamentali in miglioramento."),
    "share_change_cagr_3y": ("Variazione azioni (3 anni, annua)", "pct", ""),
    "share_change_cagr_5y": ("Variazione azioni (5 anni, annua)", "pct", "Negativo = riacquisti (buono per te). Positivo = diluizione."),
    "sbc_to_revenue": ("Compensi in azioni / ricavi", "pct", "Stipendi pagati in azioni: diluiscono gli azionisti."),
    "sbc_to_fcf": ("Compensi in azioni / FCF", "pct", ""),
    "payout_ratio": ("Payout (dividendi/utile)", "pct", ""),
    "fcf_payout": ("Dividendi / FCF", "pct", ""),
    "buyback_to_fcf_5y": ("Riacquisti / FCF (5 anni)", "pct", ""),
    "acquisitions_to_fcf_5y": ("Acquisizioni / FCF (5 anni)", "pct", "Quanta cassa va in acquisizioni: alto = crescita 'comprata'."),
    "capex_to_revenue": ("Capex / ricavi (intensità di capitale)", "pct", "Quanto deve reinvestire per mantenersi e crescere."),
    "capex_to_da": ("Capex / ammortamenti", "x", ""),
    "implied_fcf_growth": ("Crescita implicita nel prezzo", "pct", "Crescita annua del FCF, per gli anni di crescita esplicita del modello (predefinito 10), coerente con il prezzo attuale (reverse DCF, stima)."),
    "growth_gap": ("Implicita − storica", "pctpt", "Negativo = il prezzo sembra scontare meno crescita di quella già realizzata (stima di modello)."),
    "pe_hist_median": ("P/E mediano storico", "x", ""),
    "pe_vs_history_pct": ("P/E vs propria storia", "ratio01", "0% = mai stato così basso, 100% = mai così alto."),
    "ev_ebit_vs_history_pct": ("EV/EBIT vs propria storia", "ratio01", ""),
    "p_fcf_vs_history_pct": ("P/FCF vs propria storia", "ratio01", ""),
    "ps_vs_history_pct": ("P/S vs propria storia", "ratio01", ""),
    "vol_1y": ("Volatilità (1 anno, in EUR)", "pct", "Quanto oscilla il prezzo. Tipico: 15-25% grandi aziende, >40% molto volatile."),
    "vol_3y": ("Volatilità (3 anni, in EUR)", "pct", ""),
    "max_drawdown_5y": ("Perdita massima (5 anni)", "pct", "La peggiore discesa dal massimo negli ultimi 5 anni: preparati a viverla di nuovo."),
    "max_drawdown_10y": ("Perdita massima (10 anni)", "pct", ""),
    "beta_world": ("Beta vs MSCI World", "num", "Sensibilità ai movimenti del mercato mondiale: 1 = come il mercato, <1 = più difensiva."),
    "drawdown_from_52w_high": ("Distanza dal massimo 52 settimane", "pct", ""),
    "drawdown_from_3y_high": ("Distanza dal massimo 3 anni", "pct", ""),
    "momentum_12_1": ("Momentum 12-1 mesi", "pct", ""),
    "return_1y": ("Rendimento 1 anno (EUR)", "pct", ""),
    "return_5y_ann": ("Rendimento annuo 5 anni (EUR)", "pct", ""),
    "years_of_data": ("Anni di bilanci disponibili", "num", ""),
    "data_age_days": ("Età dell'ultimo bilancio (giorni)", "num", ""),
}

# explanations for entries whose description above is empty (shown in the UI next to each metric)
_MORE = {
    "gross_margin_5y_median": "Valore tipico del margine lordo negli ultimi 5 anni: meno sensibile a un singolo anno anomalo.",
    "op_margin_5y_median": "Valore tipico del margine operativo negli ultimi 5 anni: riferimento per capire se oggi è alto o basso.",
    "revenue_cagr_3y": "Di quanto sono cresciuti i ricavi in media ogni anno negli ultimi 3 anni.",
    "revenue_cagr_10y": "Di quanto sono cresciuti i ricavi in media ogni anno negli ultimi 10 anni.",
    "gross_profit_cagr_5y": "Crescita annua media dell'utile lordo: crescita dei ricavi che porta con sé anche margine.",
    "revenue_growth_last_fy": "Variazione dei ricavi nell'ultimo anno fiscale rispetto all'anno prima.",
    "eps_cagr_5y": "Crescita annua media dell'utile per azione: tiene conto anche di riacquisti e diluizioni.",
    "ni_cagr_5y": "Crescita annua media dell'utile netto (usata per banche e assicurazioni).",
    "fcf_ps_cagr_5y": "Crescita annua media della cassa libera per azione: è ciò che spetta davvero a ogni azione.",
    "fcf_cagr_5y": "Crescita annua media del free cash flow totale.",
    "bvps_cagr_5y": "Crescita annua media del patrimonio netto per azione (misura di crescita per le banche).",
    "debt_equity": "Debito finanziario diviso patrimonio netto. Più alto = l'azienda si finanzia più a debito.",
    "goodwill_to_assets": "Quota dell'attivo fatta di avviamento e intangibili acquisiti: alta = crescita per acquisizioni, rischio di svalutazioni.",
    "share_change_cagr_3y": "Variazione annua media del numero di azioni in 3 anni. Positivo = diluizione, negativo = riacquisti.",
    "sbc_to_fcf": "Compensi in azioni rispetto alla cassa libera: quanta parte della cassa 'va' ai dipendenti in azioni.",
    "payout_ratio": "Quota dell'utile distribuita come dividendo. Sopra 100% il dividendo supera gli utili.",
    "fcf_payout": "Quota della cassa libera distribuita come dividendo. Sopra 100% il dividendo non è coperto dalla cassa.",
    "buyback_to_fcf_5y": "Quota della cassa libera usata per riacquistare azioni negli ultimi 5 anni.",
    "capex_to_da": "Investimenti diviso ammortamenti. Sopra 1 l'azienda investe più di quanto si consuma (crescita); molto sotto 1 per anni può indicare sotto-investimento.",
    "pe_hist_median": "P/E tipico della società negli anni passati (a fine anno fiscale): termine di confronto per il P/E di oggi.",
    "ev_ebit_vs_history_pct": "Dove si colloca l'EV/EBIT di oggi rispetto agli anni passati della società (0% = mai così basso, 100% = mai così alto).",
    "p_fcf_vs_history_pct": "Dove si colloca il prezzo/FCF di oggi rispetto agli anni passati della società (0% = mai così basso).",
    "ps_vs_history_pct": "Dove si colloca il prezzo/ricavi di oggi rispetto agli anni passati della società (0% = mai così basso).",
    "vol_3y": "Quanto oscilla il titolo (deviazione standard annualizzata dei rendimenti giornalieri in euro, 3 anni).",
    "max_drawdown_10y": "La perdita più grande da un massimo al minimo successivo negli ultimi 10 anni (in euro).",
    "drawdown_from_52w_high": "Quanto il prezzo attuale è sotto il massimo delle ultime 52 settimane.",
    "drawdown_from_3y_high": "Quanto il prezzo attuale è sotto il massimo degli ultimi 3 anni.",
    "momentum_12_1": "Rendimento degli ultimi 12 mesi escluso l'ultimo mese: misura la tendenza recente del prezzo.",
    "return_1y": "Rendimento totale (dividendi inclusi) degli ultimi 12 mesi, convertito in euro.",
    "return_5y_ann": "Rendimento totale annuo medio degli ultimi 5 anni, dividendi inclusi, in euro.",
    "years_of_data": "Quanti anni di bilanci annuali sono disponibili: con pochi anni le metriche di crescita e stabilità sono poco affidabili.",
    "data_age_days": "Giorni trascorsi dalla fine del periodo dell'ultimo bilancio disponibile.",
}
for _k, _e in _MORE.items():
    if _k in GLOSSARY and not GLOSSARY[_k][2]:
        GLOSSARY[_k] = (GLOSSARY[_k][0], GLOSSARY[_k][1], _e)

KIND_IT = {"observed": "dato osservato", "calculated": "calcolato", "estimate": "stima (modello)",
           "third_party_estimate": "stima di terzi", "assumption": "assunzione"}


def label(key: str) -> str:
    return GLOSSARY.get(key, (key, "num", ""))[0]


def explain(key: str) -> str:
    return GLOSSARY.get(key, (key, "num", ""))[2]


def it(text: str) -> str:
    """'1,234.5' → '1.234,5' (Italian separators) for an already formatted number."""
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num_it(v: float, decimals: int = 1) -> str:
    return it(f"{v:,.{decimals}f}")


def pct(v: float, decimals: int = 1, sign: bool = False) -> str:
    """0.123 → '12,3%' (Italian decimal comma)."""
    return it(f"{v * 100:{'+' if sign else ''}.{decimals}f}%")


def fmt(key: str, v, currency: str | None = None) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/d"
    kind = GLOSSARY.get(key, (key, "num", ""))[1]
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if kind == "pct":
        return it(f"{v * 100:.1f}%")
    if kind == "pctpt":
        return it(f"{v * 100:+.1f} pt")
    if kind == "ratio01":
        return f"{v * 100:.0f}%"
    if kind == "x":
        return it(f"{v:.1f}×")
    if kind == "score01":
        return it(f"{v * 9:.1f}/9")
    if kind == "money":
        return money(v, currency)
    return it(f"{v:,.2f}")


def money(v: float | None, currency: str | None = None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/d"
    cur = f" {currency}" if currency else ""
    a = abs(v)
    if a >= 1e12:
        return it(f"{v / 1e12:.2f}") + f" mila mld{cur}"
    if a >= 1e9:
        return it(f"{v / 1e9:.1f}") + f" mld{cur}"
    if a >= 1e6:
        return it(f"{v / 1e6:,.0f}") + f" mln{cur}"
    return it(f"{v:,.0f}") + cur
