"""Investment thesis generator (rule-based, Italian).

Every sentence is built from a computed metric, a peer comparison or a flag, and carries
its source metric key — no free-form claims. It is an *interpretation* layer: the UI labels
it as such.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .glossary import fmt, label
from .scoring import PILLAR_IT

STRENGTH_TEMPLATES = {
    "roic_5y_median": "Rende molto sul capitale investito: ROIC mediano {v} negli ultimi 5 anni",
    "roic": "Ritorno sul capitale investito elevato ({v} nell'ultimo anno)",
    "gross_margin": "Margine lordo alto ({v}): indizio di potere di prezzo o prodotto differenziato",
    "op_margin_volatility": "Margini molto stabili nel tempo: risultati storicamente prevedibili",
    "fcf_conversion": "Gli utili si trasformano in cassa (conversione {v})",
    "pct_years_fcf_positive": "Genera cassa libera con costanza ({v} degli anni)",
    "pct_years_profitable": "In utile con continuità ({v} degli anni)",
    "revenue_cagr_5y": "Ricavi cresciuti del {v} l'anno negli ultimi 5 anni",
    "revenue_cagr_3y": "Ricavi cresciuti del {v} l'anno negli ultimi 3 anni",
    "eps_cagr_5y": "Utile per azione cresciuto del {v} l'anno (5 anni)",
    "fcf_ps_cagr_5y": "Free cash flow per azione cresciuto del {v} l'anno (5 anni)",
    "revenue_growth_consistency": "Crescita regolare: ricavi in aumento nel {v} degli anni",
    "net_debt_ebitda": "Debito contenuto (debito netto/EBITDA {v})",
    "interest_coverage": "Interessi ampiamente coperti dagli utili ({v})",
    "piotroski_f": "Indicatori di bilancio in miglioramento (Piotroski {v})",
    "earnings_yield": "Prezzo basso rispetto agli utili operativi (rendimento {v})",
    "fcf_sbc_yield": "Prezzo basso rispetto alla cassa generata (FCF yield netto stock option {v})",
    "pe_vs_history_pct": "P/E basso rispetto alla propria storia (percentile {v})",
    "growth_gap": "Il prezzo sembra scontare meno crescita di quella storica ({v}, stima di modello)",
    "share_change_cagr_5y": "Riduce il numero di azioni ({v} l'anno): ogni azione rappresenta una quota più grande dell'azienda",
    "shareholder_yield": "Restituisce molto agli azionisti ({v} l'anno tra dividendi e riacquisti)",
    "roe_5y_median": "ROE mediano {v} (5 anni), elevato per il settore",
    "roa_5y_median": "ROA mediano {v}, elevato per una banca/assicurazione",
    "equity_to_assets": "Patrimonializzazione solida ({v} dell'attivo)",
    "earnings_yield_equity": "Prezzo basso rispetto agli utili (rendimento {v})",
    "pb": "Prezzo basso rispetto al patrimonio (P/B {v})",
}
WEAKNESS_TEMPLATES = {
    "roic_5y_median": "Ritorni sul capitale modesti (ROIC mediano {v})",
    "roic": "Ritorno sul capitale basso nell'ultimo anno ({v})",
    "gross_margin": "Margine lordo basso rispetto ai concorrenti ({v})",
    "op_margin_volatility": "Margini instabili: risultati difficili da prevedere",
    "fcf_conversion": "Gli utili si trasformano poco in cassa (conversione {v})",
    "accruals_ratio": "Utili poco supportati dalla cassa (accruals {v})",
    "revenue_cagr_5y": "Crescita dei ricavi debole ({v} l'anno, 5 anni)",
    "revenue_growth_last_fy": "Ricavi deboli nell'ultimo anno ({v})",
    "net_debt_ebitda": "Debito elevato rispetto ai pari (debito netto/EBITDA {v})",
    "interest_coverage": "Copertura degli interessi bassa ({v})",
    "current_ratio": "Liquidità a breve bassa (liquidità corrente {v})",
    "earnings_yield": "Costosa rispetto agli utili operativi (rendimento {v})",
    "fcf_sbc_yield": "Costosa rispetto alla cassa generata (FCF yield netto stock option {v})",
    "ev_sales": "Multiplo sui ricavi alto ({v})",
    "pe_vs_history_pct": "P/E alto rispetto alla propria storia (percentile {v})",
    "growth_gap": "Il prezzo sembra scontare più crescita di quella storica ({v}, stima di modello)",
    "share_change_cagr_5y": "Diluizione: azioni in aumento del {v} l'anno",
    "sbc_to_revenue": "Compensi in azioni elevati ({v} dei ricavi)",
    "acquisitions_to_fcf_5y": "Crescita molto dipendente da acquisizioni ({v} del FCF)",
    "roe_5y_median": "ROE basso rispetto al settore ({v})",
    "equity_to_assets": "Patrimonializzazione più sottile dei concorrenti ({v})",
}


def build_thesis(row: pd.Series, detail: dict[str, Any], metrics: dict[str, Any], flags: list[dict],
                 peer_medians: dict[str, float], rdcf: dict[str, Any] | None, currency: str | None) -> dict[str, Any]:
    pcts = detail.get("metrics", {})
    strengths, weaknesses = [], []
    for key, info in sorted(pcts.items(), key=lambda kv: -kv[1]["percentile"]):
        p = info["percentile"]
        v = metrics.get(key)
        if p >= 80 and key in STRENGTH_TEMPLATES and len(strengths) < 5:
            strengths.append({"text": STRENGTH_TEMPLATES[key].format(v=fmt(key, v)) + f" — percentile {p:.0f} tra i pari (50 = mediana).",
                              "metric": key, "percentile": p})
    for key, info in sorted(pcts.items(), key=lambda kv: kv[1]["percentile"]):
        p = info["percentile"]
        v = metrics.get(key)
        if p <= 20 and key in WEAKNESS_TEMPLATES and len(weaknesses) < 5:
            weaknesses.append({"text": WEAKNESS_TEMPLATES[key].format(v=fmt(key, v)) + f" — percentile {p:.0f} tra i pari (50 = mediana).",
                               "metric": key, "percentile": p})
    risk_flags = [f for f in flags if f.get("severity") in ("severe", "high", "medium")]
    for f in sorted(risk_flags, key=lambda f: {"severe": 0, "high": 1, "medium": 2}[f["severity"]])[:6]:
        weaknesses.append({"text": f["message"], "flag": f["code"], "severity": f["severity"]})

    # what you are paying
    paying = []
    for key in (["pe", "pb", "earnings_yield_equity", "dividend_yield"] if row.get("is_banklike") else
                ["pe", "ev_ebit", "fcf_sbc_yield", "ev_sales", "dividend_yield"]):
        v = metrics.get(key)
        pm = peer_medians.get(key)
        if v is None:
            continue
        s = f"{label(key)}: {fmt(key, v)}"
        if pm is not None:
            s += f" (mediana dei pari: {fmt(key, pm)})"
        paying.append(s)
    ig = metrics.get("implied_fcf_growth")
    must = []
    if rdcf and ig is not None:
        hist = None
        for k in ("fcf_ps_cagr_5y", "revenue_cagr_5y", "revenue_cagr_3y"):
            if metrics.get(k) is not None:
                hist = (k, metrics[k])
                break
        txt = (f"Con le ipotesi del modello (tasso di sconto {rdcf.get('discount_rate', 0):.1%}, crescita perpetua "
               f"{rdcf.get('terminal_growth', 0):.1%}) il prezzo attuale è coerente con una crescita del free cash flow "
               f"di circa il {ig:.1%} l'anno per {rdcf.get('years', 10)} anni. È una stima: cambia molto con le ipotesi.")
        if hist:
            txt += f" Storicamente: {label(hist[0]).lower()} {fmt(hist[0], hist[1])}."
        paying.append(txt)
        must.append(f"Con le ipotesi del modello, il prezzo attuale presuppone una crescita media del free cash flow di "
                    f"circa il {max(ig, 0):.1%} l'anno: una crescita inferiore renderebbe il prezzo meno giustificato.")
    om = metrics.get("op_margin_5y_median")
    if om is not None and not row.get("is_banklike"):
        must.append(f"La tesi presuppone margini vicini ai livelli storici (margine operativo mediano {om:.1%}).")

    monitor = []
    if om is not None and not row.get("is_banklike"):
        monitor.append(f"Margine operativo sotto {max(om - 0.05, 0):.1%} (5 punti sotto la mediana storica)")
    rg = metrics.get("revenue_cagr_5y") or metrics.get("revenue_cagr_3y")
    if ig is not None:
        monitor.append(f"Crescita dei ricavi stabilmente sotto {max(ig - 0.03, 0):.1%} (sotto quanto implicito nel prezzo)")
    elif rg is not None:
        monitor.append(f"Crescita dei ricavi sotto {max(rg / 2, 0):.1%} (metà della media storica)")
    nde = metrics.get("net_debt_ebitda")
    if nde is not None:
        monitor.append(f"Debito netto/EBITDA sopra {max(3.0, nde + 1.5):.1f}× (oggi {nde:.1f}×)")
    monitor.append("Aumento del numero di azioni oltre il 2% l'anno (diluizione)")
    monitor.append("Comparsa di nuove red flag (restatement, cambio revisore, going concern)")

    pillars = {PILLAR_IT[p]: (None if pd.isna(row.get(p)) else round(float(row.get(p)), 0)) for p in PILLAR_IT}
    return {
        "headline": f"{row.get('classification')} — {row.get('classification_reason', '')}",
        "pillars": pillars,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "paying": paying,
        "must_go_right": must,
        "monitor": monitor,
        "disclaimer": "Tesi generata automaticamente da regole trasparenti sui dati: è un'interpretazione, "
                      "non un consiglio di investimento. Verifica sempre i filing originali.",
    }
