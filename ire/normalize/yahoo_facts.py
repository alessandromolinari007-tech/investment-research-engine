"""Yahoo statements → standardized Financials (tier B, used only for non-SEC filers).

Limitations recorded on every company built this way:
* ~4-5 fiscal years only → long-term growth/stability metrics have lower confidence;
* no filing-level provenance (source_ref = Yahoo endpoint), no restatement tracking;
* Yahoo's 'Total Debt' includes lease liabilities (consistent with IFRS 16 tier-A treatment,
  not with US GAAP tier A); Yahoo FCF does not subtract lease repayments;
* capex / stock-based compensation missing on Yahoo stay missing (never assumed 0).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .concepts import DURATION, INSTANT, ITEMS, YAHOO_NEGATIVE_OUTFLOWS
from .model import Financials
from .sec_facts import _check_share_jumps

SOURCE = "Yahoo Finance (yfinance)"


def _table(block: dict[str, dict[str, float]], item_type: str) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    rows: dict[str, dict[str, float]] = {}
    used: dict[tuple[str, str], str] = {}
    for date, vals in (block or {}).items():
        r = {}
        for item, d in ITEMS.items():
            if d["type"] != item_type or "yahoo" not in d:
                continue
            for label in d["yahoo"]:
                if label in vals and vals[label] is not None and not np.isnan(vals[label]):
                    v = float(vals[label])
                    if item in YAHOO_NEGATIVE_OUTFLOWS:
                        # outflows are negative in Yahoo; a positive "net business purchase and sale" is a
                        # net DIVESTITURE, not an acquisition → 0 acquisitions that year
                        v = 0.0 if (item == "acquisitions" and v > 0) else abs(v)
                    r[item] = v
                    used[(item, date)] = label
                    break
        if r:
            rows[date] = r
    df = pd.DataFrame.from_dict(rows, orient="index")
    if not df.empty:
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
    return df, used


def normalize_yahoo(company_id: str, ticker: str, st: dict[str, Any], fin_currency: str | None,
                    fetched_at: str | None = None) -> Financials:
    fin = Financials(company_id=company_id, currency=fin_currency, source=SOURCE, tier="B", annual=pd.DataFrame())
    inc, u1 = _table(st.get("income_annual"), DURATION)
    cfs, u2 = _table(st.get("cashflow_annual"), DURATION)
    bal, u3 = _table(st.get("balance_annual"), INSTANT)
    used = {**u1, **u2, **u3}
    annual = pd.concat([inc, cfs, bal], axis=1)
    annual = annual.loc[:, ~annual.columns.duplicated()]
    if annual.empty:
        fin.flags.append({"code": "NO_ANNUAL_DATA", "severity": "data", "message": "Nessun bilancio annuale su Yahoo"})
        return fin
    # drop fiscal years where core items are all missing (Yahoo often returns an empty 5th column)
    core = [c for c in ("revenue", "net_income", "ocf", "total_assets") if c in annual.columns]
    annual = annual[annual[core].notna().sum(axis=1) >= 2] if core else annual
    annual = annual.groupby(annual.index).last().sort_index()

    # debt: Yahoo 'Total Debt' preferred; else components
    if "debt_total_reported" in annual.columns:
        annual["total_debt"] = annual["debt_total_reported"]
    else:
        parts = [c for c in ("debt_noncurrent", "debt_current") if c in annual.columns]
        annual["total_debt"] = annual[parts].sum(axis=1, min_count=1) if parts else np.nan
    for c in ("ocf", "capex", "operating_income", "da", "gross_profit", "revenue", "cost_of_revenue"):
        if c not in annual.columns:
            annual[c] = np.nan
    for c in ("dividends_paid", "buybacks", "acquisitions", "share_issuance", "goodwill",
              "intangibles", "minority_interest", "short_term_investments", "inventory", "preferred_equity"):
        if c not in annual.columns or annual[c].isna().all():
            annual[c] = 0.0
            fin.assumed_zero.add(c)
    for c in ("capex", "sbc"):
        if c not in annual.columns or annual[c].isna().all():
            annual[c] = np.nan
            fin.flags.append({"code": f"{c.upper()}_UNKNOWN", "severity": "data",
                              "message": f"{'Capex' if c == 'capex' else 'Compensi in azioni'} non disponibili su Yahoo: "
                                         "metriche collegate non calcolate (non assunti zero)."})
    if annual["total_debt"].isna().all():
        fin.flags.append({"code": "DEBT_UNKNOWN", "severity": "data",
                          "message": "Debito non disponibile su Yahoo: EV e metriche di leva non calcolati."})
    gp_missing = annual["gross_profit"].isna()
    annual.loc[gp_missing, "gross_profit"] = annual["revenue"] - annual["cost_of_revenue"]
    annual["fcf"] = annual["ocf"] - annual["capex"]
    annual["ebitda"] = annual["operating_income"] + annual["da"]
    annual["ebit"] = annual["operating_income"]
    fin.annual = annual
    fin.fiscal_year_end = annual.index.max().strftime("%m-%d")

    # ---------------------------------------------------------------- TTM from 4 discrete quarters
    qi, _ = _table(st.get("income_quarterly"), DURATION)
    qc, _ = _table(st.get("cashflow_quarterly"), DURATION)
    q = pd.concat([qi, qc], axis=1)
    q = q.loc[:, ~q.columns.duplicated()].sort_index() if not q.empty else q
    last_fy = annual.index.max()
    fin.ttm = {k: float(v) for k, v in annual.iloc[-1].items() if pd.notna(v)}
    fin.ttm_end = last_fy
    fin.ttm_derivation = "TTM = ultimo anno fiscale (Yahoo)"
    if not q.empty and len(q) >= 4:
        last4 = q.iloc[-4:]
        gaps = np.diff(last4.index.values).astype("timedelta64[D]").astype(int)
        if last4.index[-1] > last_fy and all(80 <= g <= 100 for g in gaps):
            ttm = {}
            for c in ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "ocf",
                      "capex", "da", "sbc", "dividends_paid", "buybacks", "interest_expense", "pretax_income",
                      "income_tax", "share_issuance", "acquisitions"):
                if c in last4.columns and last4[c].notna().all():
                    ttm[c] = float(last4[c].sum())
            if all(k in ttm for k in ("revenue", "net_income")) and "ocf" in ttm:
                for z in fin.assumed_zero:
                    ttm.setdefault(z, 0.0)
                if "capex" in ttm:
                    ttm["fcf"] = ttm["ocf"] - ttm["capex"]
                if "operating_income" in ttm:
                    ttm["ebit"] = ttm["operating_income"]
                    if "da" in ttm:
                        ttm["ebitda"] = ttm["operating_income"] + ttm["da"]
                if "gross_profit" not in ttm and "cost_of_revenue" in ttm:
                    ttm["gross_profit"] = ttm["revenue"] - ttm["cost_of_revenue"]
                fin.ttm = ttm
                fin.ttm_end = last4.index[-1]
                fin.ttm_derivation = "TTM = somma degli ultimi 4 trimestri (Yahoo)"

    # latest balance sheet from quarterly if newer
    qb, _ = _table(st.get("balance_quarterly"), INSTANT)
    src = qb if (not qb.empty and qb.index.max() > last_fy) else bal
    if not src.empty:
        last_row = src.iloc[-1]
        for k, v in last_row.items():
            if pd.notna(v):
                fin.latest[k] = (float(v), src.index[-1])
        if "debt_total_reported" in fin.latest:
            fin.latest["total_debt"] = fin.latest["debt_total_reported"]

    _check_share_jumps(fin, annual)
    fin.notes.append("Dati di bilancio da Yahoo Finance (standardizzati dal fornitore di Yahoo): "
                     "storico breve (≈4-5 anni), senza tracciamento dei singoli filing.")
    if fin.assumed_zero:
        fin.notes.append("Voci non presenti su Yahoo trattate come 0 (assunzione): " + ", ".join(sorted(fin.assumed_zero)))

    for end, row in annual.iterrows():
        e = end.strftime("%Y-%m-%d")
        for item, v in row.items():
            if pd.isna(v) or item.startswith("debt_") or item == "st_borrowings":
                continue
            label = used.get((item, e))
            fin.fact_rows.append({
                "company_id": company_id, "item": item, "period_type": "FY", "period_start": None,
                "period_end": e, "value": float(v), "unit": fin_currency, "source": SOURCE,
                "source_ref": f"yfinance Ticker('{ticker}') statements", "concept": label, "form": None,
                "filed": None,
                "derivation": None if label else ("mai riportato → 0 (ASSUNZIONE)" if item in fin.assumed_zero else "calcolato"),
                "restated": 0, "original_value": None, "split_adjusted": 0, "fetched_at": fetched_at,
            })
    return fin
