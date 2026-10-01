"""Sector-relative scoring, robust ranking, classification and valuation verdict.

* Every metric is converted to a percentile WITHIN the peer group (sector; bank-like
  financials form their own group) — so a utility's leverage is compared with other
  utilities, not with software companies. Percentiles are robust to outliers.
* Pillars (quality, growth, financial strength, valuation, capital allocation) are the
  weighted average of their available metric percentiles; a pillar needs ≥50% of its
  metric weight to be available, otherwise it is "insufficient" (never imputed).
* The composite is computed under 5 different weighting schemes; the ROBUST score is
  their median and the rank spread shows how much the ranking depends on the weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .valuation import Signal, valuation_verdict

# (metric, direction +1 higher-better / -1 lower-better, weight)
PILLARS: dict[str, dict[str, list[tuple[str, int, float]]]] = {
    "quality": {
        "normal": [("roic_5y_median", 1, 2.0), ("roic", 1, 1.0), ("gross_margin", 1, 1.0),
                   ("op_margin_volatility", -1, 1.0), ("fcf_conversion", 1, 1.0), ("accruals_ratio", -1, 0.5),
                   ("pct_years_fcf_positive", 1, 0.5), ("pct_years_profitable", 1, 0.5)],
        "bank": [("roe_5y_median", 1, 2.0), ("roa_5y_median", 1, 1.0), ("roe", 1, 1.0), ("pct_years_profitable", 1, 1.0)],
    },
    "growth": {
        "normal": [("revenue_cagr_5y", 1, 1.5), ("eps_cagr_5y", 1, 1.0), ("fcf_ps_cagr_5y", 1, 1.0),
                   ("revenue_growth_consistency", 1, 1.0), ("revenue_growth_last_fy", 1, 0.5), ("gross_profit_cagr_5y", 1, 0.5)],
        "bank": [("ni_cagr_5y", 1, 1.0), ("eps_cagr_5y", 1, 1.0), ("bvps_cagr_5y", 1, 1.0)],
    },
    "financial_strength": {
        "normal": [("net_debt_ebitda", -1, 1.5), ("interest_coverage", 1, 1.0), ("current_ratio", 1, 0.5),
                   ("piotroski_f", 1, 1.0), ("altman_z", 1, 0.5), ("debt_equity", -1, 0.5)],
        "bank": [("equity_to_assets", 1, 1.5), ("piotroski_f", 1, 1.0)],
    },
    "valuation": {
        "normal": [("earnings_yield", 1, 1.5), ("fcf_sbc_yield", 1, 1.5), ("ev_sales", -1, 0.5),
                   ("pe_vs_history_pct", -1, 1.0), ("growth_gap", -1, 1.0)],
        "reit": [("p_ffo", -1, 1.5), ("dividend_yield", 1, 1.0), ("ev_ebitda", -1, 1.0), ("pe_vs_history_pct", -1, 0.5)],
        "bank": [("earnings_yield_equity", 1, 1.5), ("ptbv", -1, 1.0), ("pe_vs_history_pct", -1, 1.0), ("dividend_yield", 1, 0.5)],
    },
    "capital_allocation": {
        "normal": [("share_change_cagr_5y", -1, 1.5), ("sbc_to_revenue", -1, 1.0), ("shareholder_yield", 1, 1.0),
                   ("acquisitions_to_fcf_5y", -1, 0.5)],
        "bank": [("share_change_cagr_5y", -1, 1.5), ("shareholder_yield", 1, 1.0)],
    },
}
# Valuation vs PEERS ONLY (no own-history, no reverse-DCF inputs): used for the "vs concorrenti"
# signal of the verdict so that the verdict's signals do not re-count the same evidence.
PEER_VALUATION = {
    "normal": [("earnings_yield", 1, 1.5), ("fcf_sbc_yield", 1, 1.5), ("ev_sales", -1, 0.5)],
    "reit": [("p_ffo", -1, 1.5), ("dividend_yield", 1, 1.0), ("ev_ebitda", -1, 1.0)],
    "bank": [("earnings_yield_equity", 1, 1.5), ("ptbv", -1, 1.0), ("dividend_yield", 1, 0.5)],
}
# When the primary metric is unavailable (short history) its slot uses the fallback. If the
# fallback is already a metric of the same pillar, its weight is increased instead (never counted twice).
FALLBACKS = {"revenue_cagr_5y": "revenue_cagr_3y", "roic_5y_median": "roic", "roe_5y_median": "roe", "ptbv": "pb"}
# markers: when the marker is 1 the metric is set to the WORST possible value (e.g. net debt with EBITDA ≤ 0)
WORST_IF = {"net_debt_ebitda": "neg_ebitda_with_debt", "debt_equity": "neg_equity_with_debt"}
CLIP = {"net_debt_ebitda": (-5.0, None)}      # huge net cash vs tiny EBITDA is not "infinitely" better
MIN_OBS = 5          # minimum companies with the metric in the comparison group
FULL_CONF_OBS = 10   # below this, percentiles are shrunk towards the middle (small-sample noise)

WEIGHT_SCHEMES = {
    "base": None,  # from config
    "uguali": {"quality": 0.2, "valuation": 0.2, "financial_strength": 0.2, "growth": 0.2, "capital_allocation": 0.2},
    "qualità": {"quality": 0.45, "valuation": 0.20, "financial_strength": 0.20, "growth": 0.10, "capital_allocation": 0.05},
    "valore": {"quality": 0.25, "valuation": 0.45, "financial_strength": 0.15, "growth": 0.10, "capital_allocation": 0.05},
    "crescita": {"quality": 0.25, "valuation": 0.20, "financial_strength": 0.15, "growth": 0.35, "capital_allocation": 0.05},
}

PILLAR_IT = {"quality": "Qualità", "growth": "Crescita", "financial_strength": "Solidità finanziaria",
             "valuation": "Valutazione (economicità)", "capital_allocation": "Allocazione del capitale"}

FIN_POOL = "Finanza (banche, assicurazioni e intermediari insieme: gruppo di settore troppo piccolo)"
ALL_POOL = "Universo intero non finanziario (settore con meno di {n} società)"


def profile_of(row: pd.Series) -> str:
    if row.get("is_banklike"):
        return "bank"
    if row.get("is_reit"):
        return "reit"
    return "normal"


def peer_group_of(row: pd.Series) -> str:
    if row.get("is_banklike"):
        ind = str(row.get("industry") or "")
        if ind.startswith("Insurance"):
            return "Finanza — assicurazioni"
        if ind.startswith("Banks"):
            return "Finanza — banche"
        return "Finanza — altri intermediari"
    sec = row.get("sector")
    return sec if isinstance(sec, str) and sec else "Unknown"


def _percentiles(values: pd.Series, groups: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Mid-rank percentile within each group: p = (rank − 0.5) / n, so 'higher is better' and
    'lower is better' are exact mirrors (1 − p) and no company ever shows 0 or 100 by construction."""
    r = values.groupby(groups).rank(method="average")
    n = values.groupby(groups).transform("count")
    return (r - 0.5) / n, n


def score_universe(df: pd.DataFrame, weights: dict[str, float], min_peer: int = 8) -> pd.DataFrame:
    """df: one row per company with columns = metric values + company attributes.
    Returns df with pillar scores, composites, ranks and per-metric percentiles (in 'detail')."""
    df = df.copy()
    df["profile"] = df.apply(profile_of, axis=1)
    df["peer_group"] = df.apply(peer_group_of, axis=1)
    sizes = df["peer_group"].value_counts()
    small = df["peer_group"].map(sizes) < min_peer
    fin_rows = df["profile"] == "bank"
    # comparison population for companies in small sectors: all financials (for bank-like), or the whole
    # non-financial universe — NOT a pool of left-overs from unrelated sectors.
    pool = pd.Series(np.where(fin_rows, FIN_POOL, ALL_POOL.format(n=min_peer)), index=df.index)
    df["peer_used"] = df["peer_group"].where(~small, pool)

    all_metrics = sorted({m for p in PILLARS.values() for prof in p.values() for m, _, _ in prof}
                         | set(FALLBACKS.values()) | {m for prof in PEER_VALUATION.values() for m, _, _ in prof})
    pct: dict[str, pd.Series] = {}
    nobs: dict[str, pd.Series] = {}
    for mname in all_metrics:
        if mname not in df.columns:
            continue
        col = pd.to_numeric(df[mname], errors="coerce")
        if mname in WORST_IF and WORST_IF[mname] in df.columns:
            worst = pd.to_numeric(df[WORST_IF[mname]], errors="coerce") == 1
            col = col.where(~worst, np.inf)
        if mname in CLIP:
            lo, hi = CLIP[mname]
            col = col.clip(lower=lo, upper=hi)
        p_grp, n_grp = _percentiles(col, df["peer_group"])
        p_pool, n_pool = _percentiles(col, pool)
        p = p_grp.where(~small, p_pool)
        n = n_grp.where(~small, n_pool)
        p = p.where(n >= MIN_OBS)
        shrink = (n / FULL_CONF_OBS).clip(upper=1.0)
        pct[mname] = 0.5 + (p - 0.5) * shrink
        nobs[mname] = n

    def pillar_score(idx, spec, det=None, pillar=""):
        # resolve fallbacks first so that no metric is counted twice
        entries: dict[str, list] = {}
        wall = 0.0
        for mname, direction, w in spec:
            wall += w
            p = pct.get(mname, pd.Series(dtype=float)).get(idx)
            used = mname
            if (p is None or pd.isna(p)) and mname in FALLBACKS:
                alt = FALLBACKS[mname]
                pa = pct.get(alt, pd.Series(dtype=float)).get(idx)
                if pa is not None and pd.notna(pa):
                    p, used = pa, alt
                    if det is not None:
                        det["fallbacks"].append(f"{mname}→{alt}")
            if p is None or pd.isna(p):
                continue
            if used in entries:
                entries[used][2] += w          # fallback onto a metric already in the pillar: merge weights
            else:
                entries[used] = [p, direction, w]
        num = wsum = 0.0
        for used, (p, direction, w) in entries.items():
            sc = p if direction > 0 else 1 - p
            num += sc * w
            wsum += w
            if det is not None:
                det["metrics"][used] = {"percentile": round(float(sc) * 100, 1), "pillar": pillar,
                                        "direction": direction, "weight": w,
                                        "n_confronto": int(nobs[used].get(idx)) if used in nobs and pd.notna(nobs[used].get(idx)) else None}
        score = num / wsum * 100 if wsum >= 0.5 * wall and wsum > 0 else np.nan
        return score, wsum, wall

    details: list[dict[str, Any]] = []
    pillar_scores = {p: [] for p in PILLARS}
    peer_val, coverages = [], []
    for idx, row in df.iterrows():
        prof = row["profile"]
        det = {"metrics": {}, "fallbacks": []}
        tot_w, got_w = 0.0, 0.0
        for pillar, profs in PILLARS.items():
            spec = profs.get(prof) or profs["normal"]
            sc, wsum, wall = pillar_score(idx, spec, det, pillar)
            tot_w += wall
            got_w += wsum
            pillar_scores[pillar].append(sc)
        pv, _, _ = pillar_score(idx, PEER_VALUATION.get(prof) or PEER_VALUATION["normal"])
        peer_val.append(pv)
        coverages.append(got_w / tot_w if tot_w else 0)
        details.append(det)
    for p in PILLARS:
        df[p] = pillar_scores[p]
    df["valuation_peers"] = peer_val
    df["coverage"] = coverages
    df["detail_obj"] = details

    # composites under several weightings
    schemes = dict(WEIGHT_SCHEMES)
    schemes["base"] = weights
    comp = {}
    for name, w in schemes.items():
        comp[name] = df.apply(lambda r: _composite(r, w), axis=1)
    comp_df = pd.DataFrame(comp)
    df["composite"] = comp_df["base"]
    df["robust_score"] = comp_df.median(axis=1, skipna=False)
    ranks = comp_df.rank(ascending=False, method="min")
    n = comp_df["base"].notna().sum()
    df["rank_spread"] = (ranks.max(axis=1) - ranks.min(axis=1)) / max(n, 1)
    df["robust_rank"] = df["robust_score"].rank(ascending=False, method="min")
    df["robust_percentile"] = df["robust_score"].rank(pct=True) * 100
    for name in schemes:
        df[f"score_{name}"] = comp_df[name]
    return df


def _composite(row: pd.Series, w: dict[str, float]) -> float:
    if pd.isna(row.get("quality")) or pd.isna(row.get("valuation")):
        return np.nan
    num, den, tot = 0.0, 0.0, sum(w.values())
    for p, wt in w.items():
        v = row.get(p)
        if pd.notna(v):
            num += v * wt
            den += wt
    if den < 0.6 * tot:
        return np.nan
    return num / den


# ------------------------------------------------------------------ classification
C_TEMP = "Possibile sottovalutazione temporanea"
C_QDISC = "Qualità a sconto vs pari"
C_QFAIR = "Qualità a prezzo ragionevole"
C_QFULL = "Qualità a prezzo pieno"
C_QDET = "Qualità in deterioramento"
C_TRAP = "Possibile value trap"
C_REDFLAG = "Red flag: approfondire"
C_GROWTH = "Crescita costosa"
C_CHEAP = "Economica"
C_AVG = "Nella media"
C_NODATA = "Dati insufficienti"
QUALITY_DISCOUNT = {C_TEMP, C_QDISC}
NEGATIVE_CLASSES = {C_REDFLAG, C_TRAP}

SEVERE = {"severe"}
HIGH = {"high"}
# a single one of these is enough for "Red flag" (each is serious on its own)
BLOCKING = {"GOING_CONCERN_TEXT", "MATERIAL_WEAKNESS", "NON_RELIANCE_8K", "BANKRUPTCY_8K", "DELISTING_NOTICE_8K",
            "DEBT_ACCELERATION_8K", "HEAVY_DILUTION"}
BLOCKING_IF_HIGH = {"LOW_INTEREST_COVERAGE", "HIGH_LEVERAGE"}


def _num(row, k):
    v = row.get(k)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def is_deteriorating(row: pd.Series, flags: list[dict]) -> list[str]:
    """Deterioration signals: one-year triggers (flags) AND slow multi-year declines."""
    reasons = []
    codes = {f["code"] for f in flags}
    for c, txt in (("REVENUE_DECLINE", "ricavi in calo nell'ultimo anno"), ("MARGIN_DETERIORATION", "margini in peggioramento"),
                   ("DEBT_SURGE", "debito in forte aumento"), ("NEGATIVE_FCF", "free cash flow negativo"),
                   ("PERSISTENT_LOSSES", "perdite persistenti"), ("CYCLICAL_PEAK", "margini ai massimi del ciclo")):
        if c in codes:
            reasons.append(txt)
    r5, r3 = _num(row, "revenue_cagr_5y"), _num(row, "revenue_cagr_3y")
    if r5 is not None and r5 < 0:
        reasons.append(f"ricavi in calo da 5 anni ({r5:.1%}/anno)")
    elif r3 is not None and r3 < -0.03:
        reasons.append(f"ricavi in calo da 3 anni ({r3:.1%}/anno)")
    omt = _num(row, "op_margin_trend")
    if omt is not None and -0.05 <= omt < -0.03:
        reasons.append(f"margine operativo {omt * 100:.1f} punti sotto la media 5 anni")
    fps = _num(row, "fcf_ps_cagr_5y")
    if fps is not None and fps < -0.05:
        reasons.append(f"free cash flow per azione in calo ({fps:.1%}/anno in 5 anni)")
    g = _num(row, "growth")
    if g is not None and g < 25:
        reasons.append("crescita tra le più deboli del settore")
    return reasons


def classify_row(row: pd.Series, flags: list[dict]) -> tuple[str, str]:
    """Returns (classification, explanation). Labels describe what the NUMBERS show relative to peers;
    none of them is a statement that the stock will go up."""
    if pd.isna(row.get("robust_score")):
        return C_NODATA, "Mancano dati sufficienti per un giudizio comparabile (vedi qualità dati)."
    sev = [f for f in flags if f.get("severity") in SEVERE]
    block = [f for f in flags if f.get("code") in BLOCKING
             or (f.get("code") in BLOCKING_IF_HIGH and f.get("severity") == "high")]
    high = [f for f in flags if f.get("severity") in HIGH]
    if sev or block or len(high) >= 2:
        which = "; ".join(f["message"] for f in (sev + block + high)[:2])
        return C_REDFLAG, f"Segnali di rischio gravi: {which}"
    q, v, g = row.get("quality"), row.get("valuation"), row.get("growth")
    det = is_deteriorating(row, flags)
    det += [f"segnalazione: {f['message']}" for f in high]      # a single other high flag blocks the positive labels
    cheap_hist = _num(row, "pe_vs_history_pct") is not None and _num(row, "pe_vs_history_pct") <= 0.3
    dd = _num(row, "drawdown_from_3y_high")
    depressed = dd is not None and dd <= -0.25
    qn, vn = (q if pd.notna(q) else 0), (v if pd.notna(v) else 0)
    checks = "nessuno dei controlli automatici di deterioramento (ricavi, margini, cassa, debito, crescita pluriennale) è scattato"
    if qn >= 70 and vn >= 65 and not det:
        extra = []
        if cheap_hist:
            extra.append("multipli bassi rispetto alla propria storia")
        if depressed:
            extra.append(f"prezzo {dd:.0%} dal massimo a 3 anni")
        if extra:
            return (C_TEMP, "Alta qualità, multipli bassi rispetto ai pari e " + " e ".join(extra) + f"; {checks}. "
                    "È un'ipotesi da verificare: capire PERCHÉ il prezzo è sceso (notizie, guidance, settore).")
        return C_QDISC, f"Alta qualità e multipli più bassi dei pari del settore; {checks}."
    if vn >= 70 and (qn < 40 or det):
        why = ", ".join(det) if det else "qualità bassa rispetto ai pari"
        return C_TRAP, f"Sembra economica, ma: {why}. Un prezzo basso può essere giustificato."
    if qn >= 70 and det:
        return C_QDET, "Qualità storicamente alta, ma: " + ", ".join(det) + ". Da monitorare prima di considerarla."
    if qn >= 70 and vn < 40:
        return C_QFULL, "Azienda di qualità, ma il prezzo riflette già aspettative elevate (multipli alti vs pari)."
    if qn >= 70:
        return C_QFAIR, "Alta qualità a una valutazione nella media del settore."
    note = f" Attenzione: {', '.join(det)}." if det else ""
    if pd.notna(g) and g >= 70 and vn < 35:
        return C_GROWTH, "Cresce molto, ma il prezzo richiede che continui a lungo." + note
    if vn >= 70:
        return C_CHEAP, "Prezzo basso rispetto ai pari con qualità nella media: serve capire il perché." + note
    return C_AVG, "Nessun tratto distintivo rispetto al settore." + note


def verdict_for(row: pd.Series, rf: float | None, flags: list[dict] | None = None) -> tuple[str, str, list[Signal]]:
    """Four signals built on DIFFERENT evidence: peer multiples only / own-history multiples /
    reverse DCF vs delivered growth / after-tax operating yield vs government bonds."""
    sig: list[Signal] = []
    codes = {f.get("code") for f in (flags or [])}
    cyclical_peak = "CYCLICAL_PEAK" in codes
    v = row.get("valuation_peers")
    if v is not None and pd.notna(v):
        if v >= 65:
            sig.append(Signal("Rispetto ai concorrenti", "economica", f"multipli più bassi di circa il {v:.0f}% dei pari"))
        elif v <= 35:
            sig.append(Signal("Rispetto ai concorrenti", "costosa", f"più costosa di circa il {100 - v:.0f}% dei pari"))
        else:
            sig.append(Signal("Rispetto ai concorrenti", "ragionevole", "multipli in linea con il settore"))
    hist_keys = ("pe_vs_history_pct", "ev_ebit_vs_history_pct", "p_fcf_vs_history_pct")
    hist = [_num(row, k) for k in hist_keys]
    hist = [h for h in hist if h is not None]
    if hist:
        h = float(np.mean(hist))
        if h <= 0.3 and cyclical_peak:
            sig.append(Signal("Rispetto alla propria storia", "ragionevole",
                              "multipli bassi vs storia, ma con margini ai massimi del ciclo: non indicativo", 0.0))
        elif h <= 0.3:
            sig.append(Signal("Rispetto alla propria storia", "economica", f"multipli attuali tra i più bassi degli ultimi anni (percentile {h:.0%})"))
        elif h >= 0.7:
            sig.append(Signal("Rispetto alla propria storia", "costosa", f"multipli attuali tra i più alti degli ultimi anni (percentile {h:.0%})"))
        else:
            sig.append(Signal("Rispetto alla propria storia", "ragionevole", "multipli nella media storica"))
    ig, gap = _num(row, "implied_fcf_growth"), _num(row, "growth_gap")
    if ig is not None:
        if ig > 0.15:
            sig.append(Signal("Crescita implicita nel prezzo", "costosa", f"il prezzo richiede circa {ig:.0%} di crescita annua del FCF per 10 anni"))
        elif gap is not None:
            if gap <= -0.03 and cyclical_peak:
                sig.append(Signal("Crescita implicita nel prezzo", "ragionevole",
                                  "crescita storica gonfiata dal ciclo: confronto non indicativo", 0.0))
            elif gap <= -0.03:
                sig.append(Signal("Crescita implicita nel prezzo", "economica",
                                  f"il prezzo richiede ~{ig:.1%}/anno, meno di quanto fatto storicamente (~{ig - gap:.1%})"))
            elif gap >= 0.03:
                sig.append(Signal("Crescita implicita nel prezzo", "costosa",
                                  f"il prezzo richiede ~{ig:.1%}/anno, più della crescita storica (~{ig - gap:.1%})"))
            else:
                sig.append(Signal("Crescita implicita nel prezzo", "ragionevole", f"crescita implicita ~{ig:.1%}, simile alla storica"))
    ey = _num(row, "earnings_yield_after_tax") if not row.get("is_banklike") else _num(row, "earnings_yield_equity")
    lbl = "rendimento operativo dopo le tasse" if not row.get("is_banklike") else "rendimento degli utili"
    if ey is not None and rf is not None:
        if ey >= rf + 0.03:
            sig.append(Signal("Rendimento vs tassi", "economica", f"{lbl} {ey:.1%} vs titoli di Stato {rf:.1%}", 0.5))
        elif ey <= rf + 0.005:
            sig.append(Signal("Rendimento vs tassi", "costosa", f"{lbl} {ey:.1%}, inferiore o simile ai titoli di Stato {rf:.1%}", 0.5))
        else:
            sig.append(Signal("Rendimento vs tassi", "ragionevole", f"{lbl} {ey:.1%} vs titoli di Stato {rf:.1%}", 0.5))
    return valuation_verdict(sig)


def confidence_of(row: pd.Series, flags: list[dict]) -> str:
    cov = row.get("coverage") or 0
    lvl = 2 if cov >= 0.8 else 1 if cov >= 0.6 else 0
    if row.get("data_tier") == "B":
        lvl -= 1
    yrs = row.get("years_of_data")
    if yrs is not None and pd.notna(yrs) and yrs < 5:
        lvl -= 1
    if sum(1 for f in flags if f.get("severity") == "data") >= 2:
        lvl -= 1
    if pd.notna(row.get("rank_spread")) and row.get("rank_spread") > 0.25:
        lvl -= 1
    return ["bassa", "media", "alta"][max(0, min(2, lvl))]
