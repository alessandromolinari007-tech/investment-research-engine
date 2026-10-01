"""Valuation: reverse DCF, valuation vs own history, and a multi-signal verdict.

Philosophy: we never output a single "fair value" presented as fact. We answer
"what growth is the current price implying?" (reverse DCF) and compare it with what
the company has actually delivered, alongside cheap/expensive signals versus sector
peers and versus the company's own history. The verdict reports how many signals
agree (confidence).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_RF = 0.035


def dcf_value(fcf0: float, g: float, r: float, g_term: float, years: int = 10) -> float:
    """PV of FCF growing at g for `years`, then at g_term forever."""
    if r <= g_term:
        return float("inf")
    pv = 0.0
    f = fcf0
    for t in range(1, years + 1):
        f *= 1 + g
        pv += f / (1 + r) ** t
    tv = f * (1 + g_term) / (r - g_term)
    pv += tv / (1 + r) ** years
    return pv


def implied_growth(price_value: float, fcf0: float, r: float, g_term: float, years: int = 10,
                   lo: float = -0.5, hi: float = 1.0) -> tuple[float | None, str]:
    """Solve dcf_value(g) == price_value. Returns (g, status)."""
    if fcf0 is None or fcf0 <= 0 or price_value is None or price_value <= 0:
        return None, "FCF di partenza non positivo: reverse DCF non applicabile"
    if r <= g_term:
        return None, "tasso di sconto ≤ crescita perpetua"
    f_lo = dcf_value(fcf0, lo, r, g_term, years) - price_value
    f_hi = dcf_value(fcf0, hi, r, g_term, years) - price_value
    if f_lo > 0:
        return None, f"prezzo inferiore al valore anche con crescita {lo:.0%}/anno (fuori dall'intervallo risolvibile)"
    if f_hi < 0:
        return None, f"il prezzo richiede crescita > {hi:.0%}/anno (fuori dall'intervallo risolvibile)"
    a, b = lo, hi
    for _ in range(100):
        mid = (a + b) / 2
        fm = dcf_value(fcf0, mid, r, g_term, years) - price_value
        if abs(fm) < price_value * 1e-7:
            break
        if fm > 0:
            b = mid
        else:
            a = mid
    return (a + b) / 2, "ok"


@dataclass
class ReverseDCF:
    implied_g: float | None
    status: str
    fcf_base: float | None
    fcf_base_method: str
    discount_rate: float | None
    risk_free: float | None
    risk_free_source: str
    erp: float
    terminal_growth: float
    years: int
    sensitivity: dict[str, float | None] = field(default_factory=dict)
    implied_g_ex_sbc: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def normalized_fcf(fcf_fy: pd.Series, fcf_ttm: float | None, ttm_end: pd.Timestamp | None) -> tuple[float | None, str]:
    s = fcf_fy.dropna()
    if s.empty:
        return None, "FCF non disponibile"
    vals = list(s.iloc[-3:].values)
    dates = list(s.index[-3:])
    if fcf_ttm is not None and ttm_end is not None and dates and ttm_end > dates[-1]:
        vals[-1] = fcf_ttm
        method = "media FCF: ultimi 12 mesi + 2 anni fiscali precedenti"
    else:
        method = f"media FCF degli ultimi {len(vals)} anni fiscali"
    if any(v <= 0 for v in vals):
        return None, "FCF negativo o nullo in almeno uno degli ultimi anni: base non affidabile, reverse DCF non calcolato"
    if len(vals) < 2:
        return None, "un solo anno di FCF: base non affidabile"
    base = float(np.mean(vals))
    spread = (max(vals) - min(vals)) / base
    if spread > 1.0:
        method += f" (attenzione: FCF molto variabile, min {min(vals):.3g} / max {max(vals):.3g})"
    return base, method


def reverse_dcf(market_cap: float | None, fcf_fy: pd.Series, fcf_ttm: float | None, ttm_end,
                risk_free: float | None, rf_source: str, erp: float, g_term: float, years: int,
                sbc_ttm: float | None = None) -> ReverseDCF:
    base, method = normalized_fcf(fcf_fy, fcf_ttm, ttm_end)
    if risk_free is None:
        risk_free = DEFAULT_RF
        rf_source = f"ASSUNZIONE: tasso risk-free non disponibile per questa valuta, uso {DEFAULT_RF * 100:.1f}%".replace(".", ",")
    r = risk_free + erp
    # long-run nominal growth cannot exceed the currency's long-run nominal risk-free rate (JPY ≠ USD)
    if g_term > risk_free:
        g_term = max(round(risk_free, 4), 0.0)
    g, status = implied_growth(market_cap, base, r, g_term, years) if base else (None, method)
    sens = {}
    if base:
        for dr in (-0.01, 0.01):
            gg, _ = implied_growth(market_cap, base, r + dr, g_term, years)
            sens[f"r={r + dr:.1%}"] = gg
    g_ex = None
    if base and sbc_ttm:
        g_ex, _ = implied_growth(market_cap, base - sbc_ttm, r, g_term, years) if base - sbc_ttm > 0 else (None, "")
    return ReverseDCF(g, status, base, method, r, risk_free, rf_source, erp, g_term, years, sens, g_ex)


def historical_multiples(annual: pd.DataFrame, mcap_at: dict[pd.Timestamp, float]) -> pd.DataFrame:
    """Per fiscal year: market cap at FY end and P/E, P/S, P/FCF, EV/EBIT."""
    rows = []
    for dt, r in annual.iterrows():
        mc = mcap_at.get(dt)
        if mc is None or not math.isfinite(mc) or mc <= 0:
            continue
        ni, rev, fcf, ebit = r.get("net_income"), r.get("revenue"), r.get("fcf"), r.get("ebit")
        debt, cash = r.get("total_debt"), r.get("cash")
        sti = r.get("short_term_investments") if pd.notna(r.get("short_term_investments")) else 0.0
        mi = r.get("minority_interest") if pd.notna(r.get("minority_interest")) else 0.0
        pref = r.get("preferred_equity") if pd.notna(r.get("preferred_equity")) else 0.0
        # same EV definition as the current EV (metrics.py): + minorities + preferred − short-term investments
        ev = mc + debt + mi + pref - cash - sti if pd.notna(debt) and pd.notna(cash) else np.nan
        rows.append({
            "date": dt, "market_cap": mc,
            "pe": mc / ni if pd.notna(ni) and ni > 0 else np.nan,
            "ps": mc / rev if pd.notna(rev) and rev > 0 else np.nan,
            "p_fcf": mc / fcf if pd.notna(fcf) and fcf > 0 else np.nan,
            "ev_ebit": ev / ebit if pd.notna(ev) and pd.notna(ebit) and ebit > 0 else np.nan,
        })
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def percentile_vs_history(current: float | None, hist: pd.Series, min_points: int = 4) -> float | None:
    h = hist.dropna()
    h = h[(h > 0) & (h < 1000)]
    if current is None or current <= 0 or len(h) < min_points:
        return None
    return float((h < current).mean())


@dataclass
class Signal:
    name: str
    verdict: str          # 'economica' | 'ragionevole' | 'costosa'
    detail: str
    weight: float = 1.0
    # signals of the same family rest on largely the same numbers (peer multiples and earnings yield vs bonds are
    # both "price vs current earnings"): they count once when judging how much independent evidence agrees
    family: str = ""


def valuation_verdict(signals: list[Signal]) -> tuple[str, str, list[Signal]]:
    """Weighted vote. Confidence = how much of the evidence supports the verdict itself, and whether
    any signal points the opposite way (weight-0 signals are shown but not counted)."""
    counted = [s for s in signals if s.weight > 0]
    if not counted:
        return "non determinabile", "nessuna", signals
    score = {"economica": 0.0, "ragionevole": 0.0, "costosa": 0.0}
    for s in counted:
        score[s.verdict] += s.weight
    total = sum(score.values())
    x = (score["economica"] - score["costosa"]) / total     # cheap=+1, fair=0, expensive=−1
    if x >= 1 / 3 - 1e-9:
        verdict, cat, opp = "relativamente economica", "economica", "costosa"
    elif x <= -1 / 3 + 1e-9:
        verdict, cat, opp = "costosa", "costosa", "economica"
    else:
        verdict, cat, opp = "ragionevolmente valutata", "ragionevole", None
    support = score[cat] / total
    opposed = opp is not None and score[opp] > 0
    if verdict == "ragionevolmente valutata" and score["economica"] > 0 and score["costosa"] > 0:
        opposed = True       # "fair" only because cheap and expensive signals cancel out
    n = len({s.family or s.name for s in counted})        # independent pieces of evidence
    if n >= 3 and support >= 0.75 and not opposed:
        conf = "alta"
    elif n >= 2 and support >= 0.5 and not opposed:
        conf = "media"
    else:
        conf = "bassa"
    return verdict, conf, signals
