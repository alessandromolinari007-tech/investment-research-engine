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
    "net_debt_ebitda": ("Debito netto / EBITDA", "x", "Anni di margine lordo necessari a ripagare il debito netto. >3-4 = leva alta (dipende dal settore)."),
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
    "implied_fcf_growth": ("Crescita implicita nel prezzo", "pct", "Crescita annua del FCF per 10 anni che giustificherebbe il prezzo attuale (reverse DCF)."),
    "growth_gap": ("Implicita − storica", "pctpt", "Negativo = il mercato si aspetta meno di quanto l'azienda ha già fatto."),
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

KIND_IT = {"observed": "dato osservato", "calculated": "calcolato", "estimate": "stima (modello)",
           "third_party_estimate": "stima di terzi", "assumption": "assunzione"}


def label(key: str) -> str:
    return GLOSSARY.get(key, (key, "num", ""))[0]


def explain(key: str) -> str:
    return GLOSSARY.get(key, (key, "num", ""))[2]


def fmt(key: str, v, currency: str | None = None) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/d"
    kind = GLOSSARY.get(key, (key, "num", ""))[1]
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "pctpt":
        return f"{v * 100:+.1f} pt"
    if kind == "ratio01":
        return f"{v * 100:.0f}%"
    if kind == "x":
        return f"{v:.1f}×"
    if kind == "score01":
        return f"{v * 9:.1f}/9"
    if kind == "money":
        return money(v, currency)
    return f"{v:,.2f}"


def money(v: float | None, currency: str | None = None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/d"
    cur = f" {currency}" if currency else ""
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:.2f} mila mld{cur}"
    if a >= 1e9:
        return f"{v / 1e9:.1f} mld{cur}"
    if a >= 1e6:
        return f"{v / 1e6:.0f} mln{cur}"
    return f"{v:,.0f}{cur}"
