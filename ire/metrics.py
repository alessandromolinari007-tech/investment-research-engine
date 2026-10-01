"""Metric calculations from standardized Financials + market data.

Every metric is stored with:
    value, kind (observed/calculated/estimate/third_party_estimate/assumption),
    period (e.g. 'TTM al 2026-06-30', 'FY2025', '5 anni'), method (formula in words), inputs.
A metric whose inputs are missing, or whose formula is meaningless for the company
(negative denominator, bank balance sheet...), is None — never a guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .normalize.model import Financials


@dataclass
class MV:
    value: float | None
    kind: str = "calculated"
    period: str = ""
    method: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)


class MetricSet(dict):
    def add(self, name: str, value, kind="calculated", period="", method="", **inputs) -> None:
        v = None
        if value is not None:
            try:
                fv = float(value)
                if math.isfinite(fv):
                    v = fv
            except (TypeError, ValueError):
                v = None
        self[name] = MV(v, kind, period, method, {k: _jsonable(x) for k, x in inputs.items()})

    def val(self, name: str) -> float | None:
        m = self.get(name)
        return m.value if m else None


def _jsonable(x):
    if isinstance(x, (np.floating, np.integer)):
        return float(x)
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _div(a, b, require_pos_b=True):
    if a is None or b is None:
        return None
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    if b == 0 or (require_pos_b and b < 0):
        return None
    return a / b


def cagr(series: pd.Series, years: int, tol_days: int = 75) -> float | None:
    """CAGR over `years` using the last value and the value ~years earlier. Both must be > 0."""
    s = series.dropna()
    if len(s) < 2:
        return None
    end_date, end_val = s.index[-1], s.iloc[-1]
    target = end_date - pd.DateOffset(years=years)
    diffs = abs((s.index - target).days)
    i = int(np.argmin(diffs))
    if diffs[i] > tol_days or s.index[i] >= end_date:
        return None
    start_val = s.iloc[i]
    if start_val <= 0 or end_val <= 0:
        return None
    n = (end_date - s.index[i]).days / 365.25
    return (end_val / start_val) ** (1 / n) - 1


def window(series: pd.Series, years: int) -> pd.Series:
    s = series.dropna()
    if s.empty:
        return s
    cutoff = s.index[-1] - pd.DateOffset(years=years) + pd.Timedelta(days=45)
    return s[s.index >= cutoff]


def fy_label(ts: pd.Timestamp | None) -> str:
    return f"FY{ts.year} (chiuso {ts.date()})" if ts is not None else ""


def compute_fundamental_metrics(
    fin: Financials,
    market_cap: float | None,        # in fin.currency
    price: float | None = None,       # in price currency (only used with third-party per-share data)
    banklike: bool = False,
    reit: bool = False,
    yahoo: dict[str, Any] | None = None,
) -> MetricSet:
    m = MetricSet()
    a = fin.annual.copy()
    yahoo = yahoo or {}
    if a.empty:
        return m
    last_fy = a.index[-1]
    ttm_label = f"TTM al {fin.ttm_end.date()}" if fin.ttm_end is not None else fy_label(last_fy)

    def t(item):
        v = fin.ttm.get(item)
        return float(v) if v is not None and pd.notna(v) else None

    def s(item) -> pd.Series:
        return a[item].dropna() if item in a.columns else pd.Series(dtype=float)

    def latest(item):
        if item in fin.latest:
            return float(fin.latest[item][0])
        # last fiscal year only: a value from an older year is not "the latest balance sheet"
        v = a[item].iloc[-1] if item in a.columns else None
        return float(v) if v is not None and pd.notna(v) else None

    def kind_of(*items, base="calculated"):
        """Metrics built on an item that was ASSUMED (never reported → 0) are tagged 'assumption'."""
        return "assumption" if any(i in fin.assumed_zero for i in items) else base

    # ------------------------------------------------------------ size & EV
    na_fin = "non applicabile a banche/assicurazioni (il debito è materia prima del business)"
    debt = latest("total_debt")
    cash = latest("cash")
    sti = latest("short_term_investments") or 0.0
    mi = latest("minority_interest") or 0.0
    pref_ev = latest("preferred_equity") or 0.0       # a claim ahead of common shareholders, like debt
    m.add("market_cap", market_cap, "calculated", "ultimo prezzo", "prezzo × azioni (valuta di bilancio)")
    ev = None
    if market_cap is not None and debt is not None and cash is not None and not banklike:
        ev = market_cap + debt + mi + pref_ev - cash - sti
    m.add("enterprise_value", ev, kind_of("total_debt"), "ultimo bilancio",
          "capitalizzazione + debito finanziario + minoranze + azioni privilegiate − cassa − investimenti a breve"
          if not banklike else na_fin,
          market_cap=market_cap, debt=debt, cash=cash, short_term_investments=sti, minority_interest=mi, preferred=pref_ev)
    m.add("net_debt", (debt - cash - sti) if (debt is not None and cash is not None and not banklike) else None,
          kind_of("total_debt"), "ultimo bilancio", "debito finanziario − cassa − investimenti a breve" if not banklike else na_fin,
          debt=debt, cash=cash)

    rev, ni, ebit, ebitda, fcf, ocf = t("revenue"), t("net_income"), t("ebit"), t("ebitda"), t("fcf"), t("ocf")
    sbc, div, bb, iss = t("sbc"), t("dividends_paid"), t("buybacks"), t("share_issuance")
    equity_total = latest("equity")
    pref = latest("preferred_equity") or 0.0
    equity = (equity_total - pref) if equity_total is not None else None   # common equity
    assets = latest("total_assets")
    tax_ttm = None
    if t("pretax_income") and t("income_tax") is not None and t("pretax_income") > 0:
        tax_ttm = min(max(t("income_tax") / t("pretax_income"), 0.0), 0.35)

    # ------------------------------------------------------------ valuation multiples
    m.add("pe", _div(market_cap, ni), "calculated", ttm_label, "capitalizzazione / utile netto (solo se utile > 0)",
          market_cap=market_cap, net_income=ni)
    m.add("earnings_yield_equity", _div(ni, market_cap), "calculated", ttm_label, "utile netto / capitalizzazione")
    m.add("ps", _div(market_cap, rev) if not banklike else None, "calculated", ttm_label, "capitalizzazione / ricavi",
          market_cap=market_cap, revenue=rev)
    m.add("pb", _div(market_cap, equity), "calculated", "ultimo bilancio",
          "capitalizzazione / patrimonio netto degli azionisti ordinari (escluse azioni privilegiate; se > 0)",
          market_cap=market_cap, equity=equity, preferred=pref)
    if banklike and equity is not None:
        tbv = equity - (latest("goodwill") or 0.0) - (latest("intangibles") or 0.0)
        m.add("ptbv", _div(market_cap, tbv), "calculated", "ultimo bilancio",
              "capitalizzazione / patrimonio netto tangibile (ordinario − avviamento − intangibili)",
              market_cap=market_cap, tangible_book=tbv)
    if not banklike:
        k_ev = kind_of("total_debt")
        m.add("ev_ebitda", _div(ev, ebitda), k_ev, ttm_label, "EV / EBITDA (se EBITDA > 0)", ev=ev, ebitda=ebitda)
        m.add("ev_ebit", _div(ev, ebit), k_ev, ttm_label, "EV / EBIT (se EBIT > 0)", ev=ev, ebit=ebit)
        m.add("ev_fcf", _div(ev, fcf), kind_of("total_debt", "capex"), ttm_label, "EV / free cash flow (se FCF > 0)", ev=ev, fcf=fcf)
        m.add("ev_sales", _div(ev, rev), k_ev, ttm_label, "EV / ricavi", ev=ev, revenue=rev)
        m.add("earnings_yield", _div(ebit, ev), k_ev, ttm_label,
              "EBIT / EV — rendimento operativo pagato per l'intera azienda (Greenblatt)", ebit=ebit, ev=ev)
        tr = tax_ttm if tax_ttm is not None else 0.21
        m.add("earnings_yield_after_tax", _div(ebit * (1 - tr), ev) if ebit is not None else None, k_ev, ttm_label,
              f"EBIT × (1 − aliquota {tr:.0%}{'' if tax_ttm is not None else ', ASSUNTA'}) / EV: rendimento operativo "
              "dopo le tasse, confrontabile con i titoli di Stato", ebit=ebit, ev=ev, tax_rate=tr)
        m.add("fcf_yield", _div(fcf, market_cap), kind_of("capex"), ttm_label, "free cash flow / capitalizzazione",
              fcf=fcf, market_cap=market_cap)
        fcf_sbc = (fcf - sbc) if (fcf is not None and sbc is not None) else None
        m.add("fcf_sbc_yield", _div(fcf_sbc, market_cap), kind_of("capex"), ttm_label,
              "(FCF − compensi in azioni) / capitalizzazione: tratta le stock option come un costo reale",
              fcf=fcf, sbc=sbc, market_cap=market_cap)
    m.add("dividend_yield", _div(div, market_cap), "calculated", ttm_label, "dividendi pagati (cassa) / capitalizzazione",
          dividends_paid=div, market_cap=market_cap)
    sh_yield = None
    # issuance unknown for the period (reported in annual data but not rolled to TTM) → not assumed 0,
    # otherwise a diluting company would look like it returns cash
    iss_known = iss is not None or "share_issuance" in fin.assumed_zero or "share_issuance" not in a.columns
    if div is not None and bb is not None and market_cap and iss_known:
        sh_yield = (div + bb - (iss or 0.0)) / market_cap
    m.add("shareholder_yield", sh_yield, "calculated", ttm_label,
          "(dividendi + riacquisti − emissioni di azioni) / capitalizzazione", dividends=div, buybacks=bb, issuance=iss)

    # REIT: FFO approximation
    if reit:
        da = t("da")
        ffo = (ni + da) if (ni is not None and da is not None) else None
        m.add("ffo_approx", ffo, "estimate", ttm_label,
              "FFO approssimato = utile netto + ammortamenti (non esclude plusvalenze da cessioni)", net_income=ni, da=da)
        m.add("p_ffo", _div(market_cap, ffo), "estimate", ttm_label, "capitalizzazione / FFO approssimato")
        m.add("ffo_payout", _div(div, ffo), "estimate", ttm_label, "dividendi / FFO approssimato")

    # third-party estimates (Yahoo analyst consensus) — clearly labelled
    fpe = yahoo.get("forwardPE")
    m.add("forward_pe", fpe if (fpe and fpe > 0) else None, "third_party_estimate", "prossimi 12 mesi (consenso analisti)",
          "P/E su utili attesi dagli analisti (fonte: Yahoo Finance). È una STIMA di terzi.",
          analysts=yahoo.get("numberOfAnalystOpinions"))
    f_eps, tr_eps = yahoo.get("forwardEps"), yahoo.get("trailingEps")
    feg = (f_eps / tr_eps - 1) if (f_eps and tr_eps and tr_eps > 0) else None
    m.add("fwd_eps_growth", feg, "third_party_estimate", "prossimi 12 mesi",
          "EPS atteso (consenso) / EPS ultimi 12 mesi − 1 (fonte: Yahoo Finance)", forward_eps=f_eps, trailing_eps=tr_eps)

    # ------------------------------------------------------------ profitability (TTM)
    gp = t("gross_profit")
    oi = t("operating_income") if t("operating_income") is not None else t("ebit")   # no subtotal → EBIT
    if not banklike:
        m.add("gross_margin", _div(gp, rev), "calculated", ttm_label, "utile lordo / ricavi")
        m.add("operating_margin", _div(oi, rev), "calculated", ttm_label, "utile operativo / ricavi")
        m.add("fcf_margin", _div(fcf, rev), "calculated", ttm_label, "free cash flow / ricavi")
    m.add("net_margin", _div(ni, rev) if not banklike else None, "calculated", ttm_label, "utile netto / ricavi")

    # annual series of return ratios (average of opening/closing balances)
    roe_s, roa_s, roic_s = _return_series(a, fin)
    if len(roe_s):
        m.add("roe", roe_s.iloc[-1], "calculated", fy_label(roe_s.index[-1]),
              "utile netto / patrimonio netto medio (inizio-fine anno); non calcolato se patrimonio ≤ 0")
    else:
        m.add("roe", None, "calculated", "", "patrimonio netto non positivo o dati mancanti")
    m.add("roa", roa_s.iloc[-1] if len(roa_s) else None, "calculated",
          fy_label(roa_s.index[-1]) if len(roa_s) else "", "utile netto / totale attivo medio")
    if not banklike:
        m.add("roic", roic_s.iloc[-1] if len(roic_s) else None, "calculated",
              fy_label(roic_s.index[-1]) if len(roic_s) else "",
              "NOPAT / capitale investito medio. NOPAT = EBIT × (1 − aliquota effettiva, limitata 0-35%, 21% se non "
              "calcolabile). Capitale investito = attivo − cassa − investimenti a breve − passività correnti non finanziarie")
        m.add("roic_5y_median", _median(window(roic_s, 5)), "calculated", "ultimi 5 anni", "mediana ROIC annuale",
              n=len(window(roic_s, 5)))
    m.add("roe_5y_median", _median(window(roe_s, 5)), "calculated", "ultimi 5 anni", "mediana ROE annuale",
          n=len(window(roe_s, 5)))
    m.add("roa_5y_median", _median(window(roa_s, 5)), "calculated", "ultimi 5 anni", "mediana ROA annuale")

    # stability
    gm_s = _ratio_series(a, "gross_profit", "revenue")
    a_om = a.copy()
    if "ebit" in a_om.columns:          # filers without an operating-income subtotal: EBIT (pretax + interest)
        a_om["operating_income"] = (a_om["operating_income"] if "operating_income" in a_om.columns
                                    else pd.Series(np.nan, index=a_om.index)).fillna(a_om["ebit"])
    om_s = _ratio_series(a_om, "operating_income", "revenue")
    nm_s = _ratio_series(a, "net_income", "revenue")
    if not banklike:
        m.add("gross_margin_5y_median", _median(window(gm_s, 5)), "calculated", "ultimi 5 anni", "mediana margine lordo")
        m.add("op_margin_5y_median", _median(window(om_s, 5)), "calculated", "ultimi 5 anni", "mediana margine operativo")
        w10 = window(om_s, 10)
        m.add("op_margin_10y_median", _median(w10) if len(w10) >= 6 else None, "calculated", f"ultimi {len(w10)} anni",
              "mediana del margine operativo sul ciclo (≥ 6 anni): riferimento 'metà ciclo'", n=len(w10))
        w = window(om_s, 10)
        m.add("op_margin_volatility", float(w.std(ddof=1)) if len(w) >= 4 else None, "calculated",
              f"ultimi {len(w)} anni", "deviazione standard del margine operativo annuale (più bassa = più stabile)",
              n=len(w))
        m.add("op_margin_trend", (om_s.iloc[-1] - _median(window(om_s, 5))) if len(om_s) >= 3 else None, "calculated",
              "ultimo anno vs mediana 5 anni", "margine operativo ultimo anno − mediana 5 anni (punti)")
    ni_s = s("net_income")
    w = window(ni_s, 10)
    m.add("pct_years_profitable", float((w > 0).mean()) if len(w) >= 3 else None, "calculated",
          f"ultimi {len(w)} anni", "quota di anni con utile netto positivo", n=len(w))
    fcf_s = s("fcf")
    w = window(fcf_s, 10)
    if not banklike:
        m.add("pct_years_fcf_positive", float((w > 0).mean()) if len(w) >= 3 else None, "calculated",
              f"ultimi {len(w)} anni", "quota di anni con free cash flow positivo", n=len(w))
        conv = (fcf_s / ni_s).where(ni_s > 0).dropna()
        cw = window(conv, 5).clip(-2, 3)
        m.add("fcf_conversion", _median(cw) if len(cw) >= 3 else None, "calculated", "ultimi 5 anni",
              "mediana di FCF / utile netto (anni con utile > 0). >1 = utili sostenuti da cassa", n=len(cw))
        ocf_last = s("ocf")
        if len(ocf_last) and len(ni_s) and assets:
            ta = s("total_assets")
            avg_ta = ta.rolling(2).mean().iloc[-1] if len(ta) >= 2 else (ta.iloc[-1] if len(ta) else None)
            acc = _div((ni_s.iloc[-1] - ocf_last.iloc[-1]), avg_ta)
            m.add("accruals_ratio", acc, "calculated", fy_label(last_fy),
                  "(utile netto − flusso di cassa operativo) / attivo medio. Alto = utili poco supportati da cassa (Sloan)")
    else:
        eq_s = s("equity")
        if len(eq_s) and len(s("total_assets")):
            m.add("equity_to_assets", _div(latest("equity"), latest("total_assets")), "calculated", "ultimo bilancio",
                  "patrimonio netto / totale attivo (cuscinetto di capitale di banche e assicurazioni)")

    # ------------------------------------------------------------ growth
    rev_s = s("revenue")
    if not banklike:
        for yrs in (3, 5, 10):
            m.add(f"revenue_cagr_{yrs}y", cagr(rev_s, yrs), "calculated", f"{yrs} anni fino a {fy_label(last_fy)}",
                  "crescita annua composta dei ricavi (in valuta di bilancio)")
        gp_s = s("gross_profit")
        m.add("gross_profit_cagr_5y", cagr(gp_s, 5), "calculated", "5 anni", "crescita annua composta dell'utile lordo")
        if len(rev_s) >= 2:
            m.add("revenue_growth_last_fy", rev_s.iloc[-1] / rev_s.iloc[-2] - 1 if rev_s.iloc[-2] > 0 else None,
                  "calculated", fy_label(rev_s.index[-1]), "crescita ricavi ultimo anno fiscale")
        if len(rev_s) >= 4:
            g = rev_s.pct_change().dropna()
            g = window(g, 10)
            m.add("revenue_growth_consistency", float((g > 0).mean()), "calculated", f"ultimi {len(g)} anni",
                  "quota di anni con ricavi in crescita", n=len(g))
    shares = _shares_series(a)
    share_flag = any(f["code"] == "POSSIBLE_UNADJUSTED_SPLIT" for f in fin.flags)
    eps_s = (ni_s / shares).dropna() if len(shares) else pd.Series(dtype=float)
    m.add("eps_cagr_5y", None if share_flag else cagr(eps_s, 5), "calculated", "5 anni",
          "crescita annua composta dell'utile per azione (utile netto / azioni diluite medie)")
    m.add("ni_cagr_5y", cagr(ni_s, 5), "calculated", "5 anni", "crescita annua composta dell'utile netto")
    if not banklike:
        fps = (fcf_s / shares).dropna() if len(shares) else pd.Series(dtype=float)
        m.add("fcf_ps_cagr_5y", None if share_flag else cagr(fps, 5), "calculated", "5 anni",
              "crescita annua composta del free cash flow per azione (estremi entrambi > 0)")
        m.add("fcf_cagr_5y", cagr(fcf_s, 5), "calculated", "5 anni", "crescita annua composta del free cash flow")
    else:
        bvps = (s("equity") / shares).dropna() if len(shares) else pd.Series(dtype=float)
        m.add("bvps_cagr_5y", None if share_flag else cagr(bvps, 5), "calculated", "5 anni",
              "crescita annua composta del patrimonio netto per azione")

    # ------------------------------------------------------------ financial strength
    if not banklike:
        nd = m.val("net_debt")
        m.add("net_debt_ebitda", _div(nd, ebitda) if nd is not None else None, kind_of("total_debt"), ttm_label,
              "debito netto / EBITDA (negativo = cassa netta); non calcolabile se EBITDA ≤ 0", net_debt=nd, ebitda=ebitda)
        # worst-case markers used by the scoring (a negative denominator must not look like "no debt")
        m.add("neg_ebitda_with_debt", 1.0 if (nd is not None and nd > 0 and ebitda is not None and ebitda <= 0) else 0.0,
              "calculated", ttm_label, "1 = debito netto positivo con EBITDA ≤ 0 (leva non misurabile, caso peggiore)")
        m.add("debt_equity", _div(debt, equity), kind_of("total_debt"), "ultimo bilancio",
              "debito finanziario / patrimonio netto; non calcolabile se patrimonio ≤ 0")
        m.add("neg_equity_with_debt", 1.0 if (debt is not None and debt > 0 and equity is not None and equity <= 0) else 0.0,
              "calculated", "ultimo bilancio", "1 = debito con patrimonio netto ≤ 0 (caso peggiore per la leva)")
        intexp = t("interest_expense")
        cov = None
        cov_method = "EBIT / interessi passivi (limitato a 100)"
        if ebit is not None and intexp is not None and intexp > 0:
            cov = min(ebit / intexp, 100.0)
        elif debt is not None and debt == 0 and "total_debt" not in fin.assumed_zero and ebit is not None and ebit > 0:
            cov = 100.0
            cov_method = "nessun debito finanziario: copertura posta al massimo (100)"
        m.add("interest_coverage", cov, "calculated", ttm_label, cov_method, ebit=ebit, interest_expense=intexp)
        ca, cl, inv = latest("current_assets"), latest("current_liabilities"), latest("inventory") or 0.0
        m.add("current_ratio", _div(ca, cl), "calculated", "ultimo bilancio", "attività correnti / passività correnti")
        m.add("quick_ratio", _div((ca - inv) if ca is not None else None, cl), "calculated", "ultimo bilancio",
              "(attività correnti − magazzino) / passività correnti")
        gw = (latest("goodwill") or 0.0) + (latest("intangibles") or 0.0)
        m.add("goodwill_to_assets", _div(gw, assets), "calculated", "ultimo bilancio",
              "(avviamento + intangibili) / totale attivo — alto = crescita da acquisizioni, rischio svalutazioni")
        z = _altman(a, market_cap)
        m.add("altman_z", z[0], "calculated", fy_label(last_fy),
              "Altman Z = 1.2·CCN/A + 1.4·UtiliTrattenuti/A + 3.3·EBIT/A + 0.6·Cap/Passività + 1.0·Ricavi/A "
              "(<1.8 zona di stress; pensato per aziende industriali)", **z[1])
        bm = _beneish(a)
        m.add("beneish_m", bm[0], "calculated", fy_label(last_fy),
              "Beneish M-score (8 variabili). > −1.78 = profilo contabile simile a casi di manipolazione "
              "(molti falsi positivi, specie in aziende in forte crescita)", **bm[1])
    pf = _piotroski(a, banklike)
    m.add("piotroski_f", pf[0], "calculated", fy_label(last_fy),
          "Piotroski F-score: 9 test binari su redditività, leva/liquidità ed efficienza (0-9, più alto meglio). "
          "Mostrato come frazione dei test disponibili.", **pf[1])

    # ------------------------------------------------------------ capital allocation
    if len(shares) >= 2 and not share_flag:
        for yrs in (3, 5):
            c = cagr(shares, yrs)
            m.add(f"share_change_cagr_{yrs}y", c, "calculated", f"{yrs} anni",
                  "variazione annua composta delle azioni diluite (negativo = riacquisti, positivo = diluizione)")
    else:
        m.add("share_change_cagr_5y", None, "calculated", "",
              "non calcolabile (storico azioni insufficiente o salto anomalo nel numero di azioni)")
    m.add("sbc_to_revenue", _div(sbc, rev) if not banklike else None, "calculated", ttm_label,
          "compensi in azioni / ricavi")
    m.add("sbc_to_fcf", _div(sbc, fcf) if not banklike else None, "calculated", ttm_label,
          "compensi in azioni / free cash flow (se FCF > 0)")
    m.add("payout_ratio", _div(div, ni), "calculated", ttm_label, "dividendi pagati / utile netto (se utile > 0)")
    if not banklike:
        m.add("fcf_payout", _div(div, fcf), "calculated", ttm_label, "dividendi pagati / free cash flow")
        f5 = window(fcf_s, 5).sum()
        b5 = window(s("buybacks"), 5).sum()
        q5 = window(s("acquisitions"), 5).sum()
        n5 = len(window(fcf_s, 5))
        m.add("buyback_to_fcf_5y", _div(b5, f5) if n5 >= 3 else None, "calculated", "ultimi 5 anni",
              "riacquisti cumulati / FCF cumulato", buybacks=b5, fcf=f5)
        m.add("acquisitions_to_fcf_5y", _div(q5, f5) if n5 >= 3 else None, "calculated", "ultimi 5 anni",
              "acquisizioni cumulate / FCF cumulato (alto = crescita comprata, rischio integrazione)",
              acquisitions=q5, fcf=f5)
        capex_int = _ratio_series(a, "capex", "revenue")
        m.add("capex_to_revenue", _median(window(capex_int, 5)), "calculated", "mediana 5 anni",
              "capex / ricavi: intensità di capitale")
        m.add("capex_to_da", _div(t("capex"), t("da")), "calculated", ttm_label,
              "capex / ammortamenti (>1 = investe più di quanto si consuma)")

    # ------------------------------------------------------------ data quality meta
    m.add("years_of_data", float(len(a)), "observed", "", "numero di anni fiscali disponibili")
    lpe = fin.latest_period_end
    m.add("data_age_days", float((pd.Timestamp.today().normalize() - lpe).days) if lpe is not None else None,
          "calculated", "", "giorni dalla fine dell'ultimo periodo contabile disponibile")
    return m


# ---------------------------------------------------------------- helpers
def _median(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.median()) if len(s) else None


def _ratio_series(a: pd.DataFrame, num: str, den: str) -> pd.Series:
    if num not in a.columns or den not in a.columns:
        return pd.Series(dtype=float)
    d = a[den].where(a[den] > 0)
    return (a[num] / d).dropna()


def _shares_series(a: pd.DataFrame) -> pd.Series:
    if "shares_diluted" in a.columns and a["shares_diluted"].notna().sum() >= 2:
        return a["shares_diluted"].where(a["shares_diluted"] > 0).dropna()
    if "shares_outstanding" in a.columns:
        return a["shares_outstanding"].where(a["shares_outstanding"] > 0).dropna()
    return pd.Series(dtype=float)


def _tax_rate(row: pd.Series) -> tuple[float, bool]:
    pt, tx = row.get("pretax_income"), row.get("income_tax")
    if pt is not None and tx is not None and pd.notna(pt) and pd.notna(tx) and pt > 0:
        return float(min(max(tx / pt, 0.0), 0.35)), False
    return 0.21, True


def _return_series(a: pd.DataFrame, fin: Financials) -> tuple[pd.Series, pd.Series, pd.Series]:
    roe, roa, roic = {}, {}, {}
    idx = list(a.index)
    for i in range(1, len(idx)):
        cur, prev = a.loc[idx[i]], a.loc[idx[i - 1]]
        if (idx[i] - idx[i - 1]).days > 420:
            continue
        ni = cur.get("net_income")
        eq = [x - (r.get("preferred_equity") if pd.notna(r.get("preferred_equity")) else 0.0)
              for r in (cur, prev) for x in [r.get("equity")] if pd.notna(x)]
        if pd.notna(ni) and len(eq) == 2 and min(eq) > 0:
            roe[idx[i]] = ni / np.mean(eq)
        ta = [x for x in (cur.get("total_assets"), prev.get("total_assets")) if pd.notna(x)]
        if pd.notna(ni) and len(ta) == 2 and min(ta) > 0:
            roa[idx[i]] = ni / np.mean(ta)
        ic_c, ic_p = _invested_capital(cur), _invested_capital(prev)
        ebit = cur.get("ebit")
        if pd.notna(ebit) and ic_c and ic_p and ic_c > 0 and ic_p > 0:
            tr, _ = _tax_rate(cur)
            roic[idx[i]] = ebit * (1 - tr) / ((ic_c + ic_p) / 2)
    return pd.Series(roe, dtype=float), pd.Series(roa, dtype=float), pd.Series(roic, dtype=float)


def _invested_capital(r: pd.Series) -> float | None:
    ta, cl = r.get("total_assets"), r.get("current_liabilities")
    if pd.isna(ta) or pd.isna(cl):
        return None
    cash = r.get("cash") if pd.notna(r.get("cash")) else 0.0
    sti = r.get("short_term_investments") if pd.notna(r.get("short_term_investments")) else 0.0
    dc = r.get("debt_current")
    if pd.isna(dc):
        parts = [x for x in (r.get("debt_lt_current"), r.get("st_borrowings")) if pd.notna(x)]
        dc = sum(parts) if parts else 0.0
    return float(ta - cash - sti - (cl - dc))


def _altman(a: pd.DataFrame, mcap: float | None):
    r = a.iloc[-1]
    need = ["current_assets", "current_liabilities", "total_assets", "retained_earnings", "ebit", "revenue", "total_liabilities"]
    if mcap is None or any(pd.isna(r.get(k)) for k in need) or r["total_assets"] <= 0 or r["total_liabilities"] <= 0:
        return None, {"missing": [k for k in need if pd.isna(r.get(k))]}
    ta = r["total_assets"]
    z = (1.2 * (r["current_assets"] - r["current_liabilities"]) / ta + 1.4 * r["retained_earnings"] / ta
         + 3.3 * r["ebit"] / ta + 0.6 * mcap / r["total_liabilities"] + 1.0 * r["revenue"] / ta)
    return float(z), {}


def _beneish(a: pd.DataFrame):
    if len(a) < 2:
        return None, {"note": "servono 2 anni"}
    t, p = a.iloc[-1], a.iloc[-2]
    g = lambda r, k: r.get(k) if pd.notna(r.get(k)) else None  # noqa: E731
    try:
        dsri = (g(t, "receivables") / g(t, "revenue")) / (g(p, "receivables") / g(p, "revenue"))
        gm_t = g(t, "gross_profit") / g(t, "revenue")
        gm_p = g(p, "gross_profit") / g(p, "revenue")
        gmi = gm_p / gm_t
        aqi = (1 - (g(t, "current_assets") + g(t, "ppe_net")) / g(t, "total_assets")) / (
            1 - (g(p, "current_assets") + g(p, "ppe_net")) / g(p, "total_assets"))
        sgi = g(t, "revenue") / g(p, "revenue")
        lt_t = g(t, "total_debt") or 0.0
        lt_p = g(p, "total_debt") or 0.0
        lvgi = ((g(t, "current_liabilities") + lt_t) / g(t, "total_assets")) / (
            (g(p, "current_liabilities") + lt_p) / g(p, "total_assets"))
        tata = (g(t, "net_income") - g(t, "ocf")) / g(t, "total_assets")
    except (TypeError, ZeroDivisionError):
        return None, {"note": "dati insufficienti"}
    assumptions = []
    try:
        depi = (g(p, "da") / (g(p, "da") + g(p, "ppe_net"))) / (g(t, "da") / (g(t, "da") + g(t, "ppe_net")))
    except (TypeError, ZeroDivisionError):
        depi, _ = 1.0, assumptions.append("DEPI assunto neutro (1.0)")
    try:
        sgai = (g(t, "sga") / g(t, "revenue")) / (g(p, "sga") / g(p, "revenue"))
    except (TypeError, ZeroDivisionError):
        sgai, _ = 1.0, assumptions.append("SGAI assunto neutro (1.0)")
    vals = [dsri, gmi, aqi, sgi, depi, sgai, lvgi, tata]
    if any(v is None or not np.isfinite(v) for v in vals):
        return None, {"note": "dati insufficienti"}
    mscore = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi
              - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    return float(mscore), {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi, "DEPI": depi, "SGAI": sgai,
                           "LVGI": lvgi, "TATA": tata, "assumptions": assumptions}


def _piotroski(a: pd.DataFrame, banklike: bool):
    if len(a) < 3:
        return None, {"note": "servono 3 anni di bilanci"}
    t, p, pp = a.iloc[-1], a.iloc[-2], a.iloc[-3]
    g = lambda r, k: r.get(k) if pd.notna(r.get(k)) else None  # noqa: E731
    tests: dict[str, bool | None] = {}

    def safe(fn):
        try:
            v = fn()
            return None if v is None else bool(v)
        except (TypeError, ZeroDivisionError):
            return None

    roa_t = lambda: g(t, "net_income") / g(p, "total_assets")  # noqa: E731
    roa_p = lambda: g(p, "net_income") / g(pp, "total_assets")  # noqa: E731
    tests["ROA > 0"] = safe(lambda: roa_t() > 0)
    tests["ROA in aumento"] = safe(lambda: roa_t() > roa_p())
    if not banklike:   # cash-flow and leverage tests are meaningless for banks/insurers
        tests["CFO > 0"] = safe(lambda: g(t, "ocf") > 0)
        tests["CFO > utile netto"] = safe(lambda: g(t, "ocf") > g(t, "net_income"))
        tests["Leva in calo"] = safe(lambda: (g(t, "total_debt") / g(t, "total_assets")) <= (g(p, "total_debt") / g(p, "total_assets")))
        tests["Liquidità corrente in aumento"] = safe(
            lambda: g(t, "current_assets") / g(t, "current_liabilities") > g(p, "current_assets") / g(p, "current_liabilities"))
        tests["Margine lordo in aumento"] = safe(
            lambda: g(t, "gross_profit") / g(t, "revenue") > g(p, "gross_profit") / g(p, "revenue"))
    tests["Nessuna nuova emissione di azioni"] = safe(lambda: g(t, "shares_diluted") <= g(p, "shares_diluted") * 1.005)
    tests["Rotazione attivo in aumento"] = safe(
        lambda: g(t, "revenue") / g(p, "total_assets") > g(p, "revenue") / g(pp, "total_assets"))
    avail = {k: v for k, v in tests.items() if v is not None}
    min_needed = 4 if banklike else 7
    if len(avail) < min_needed:
        return None, {"tests": tests, "note": f"solo {len(avail)} test calcolabili"}
    score = sum(avail.values())
    return float(score) / len(avail), {"tests": tests, "score": score, "available": len(avail)}
