"""What changed between two analysis runs (fundamental deterioration monitor)."""
from __future__ import annotations

import sqlite3

import pandas as pd

METRIC_RULES = [
    # metric, threshold, direction (-1 = a decrease is bad), label, is_points
    ("gross_margin", 0.02, -1, "Margine lordo in calo", True),
    ("operating_margin", 0.03, -1, "Margine operativo in calo", True),
    ("fcf_margin", 0.03, -1, "Margine di free cash flow in calo", True),
    ("revenue_growth_last_fy", 0.10, -1, "Crescita dei ricavi in rallentamento", True),
    ("roic", 0.03, -1, "ROIC in calo", True),
    ("net_debt_ebitda", 1.0, 1, "Leva finanziaria in aumento", False),
    ("share_change_cagr_3y", 0.02, 1, "Diluizione in aumento", True),
    ("interest_coverage", 3.0, -1, "Copertura interessi in calo", False),
]


def _load(con: sqlite3.Connection, run_id: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    s = pd.read_sql_query("SELECT company_id, robust_score, classification, valuation_verdict FROM scores WHERE run_id=?",
                          con, params=[run_id])
    m = pd.read_sql_query("SELECT company_id, metric, value FROM metrics WHERE run_id=?", con, params=[run_id])
    m = m.pivot_table(index="company_id", columns="metric", values="value", aggfunc="last") if not m.empty else pd.DataFrame()
    f = pd.read_sql_query("SELECT company_id, code, severity, message FROM flags WHERE run_id=?", con, params=[run_id])
    return s, m, f


def compute_changes(con: sqlite3.Connection, new_run: int, old_run: int) -> pd.DataFrame:
    s1, m1, f1 = _load(con, new_run)
    s0, m0, f0 = _load(con, old_run)
    names = pd.read_sql_query("SELECT company_id, ticker, name FROM companies", con).set_index("company_id")
    out = []

    def add(cid, kind, msg, sev):
        out.append({"company_id": cid, "ticker": names["ticker"].get(cid), "name": names["name"].get(cid),
                    "tipo": kind, "cambiamento": msg, "gravità": sev})

    s0i, s1i = s0.set_index("company_id"), s1.set_index("company_id")
    for cid, r in s1i.iterrows():
        if cid not in s0i.index:
            add(cid, "Universo", "Nuova società analizzata", "info")
            continue
        o = s0i.loc[cid]
        if r["classification"] != o["classification"]:
            bad = r["classification"] in ("Red flag: approfondire", "Possibile value trap")
            add(cid, "Classificazione", f"{o['classification']} → {r['classification']}", "alta" if bad else "info")
        if r["valuation_verdict"] != o["valuation_verdict"] and r["valuation_verdict"] and o["valuation_verdict"]:
            add(cid, "Valutazione", f"{o['valuation_verdict']} → {r['valuation_verdict']}", "info")
        if pd.notna(r["robust_score"]) and pd.notna(o["robust_score"]) and abs(r["robust_score"] - o["robust_score"]) >= 10:
            d = r["robust_score"] - o["robust_score"]
            add(cid, "Punteggio", f"Punteggio robusto {o['robust_score']:.0f} → {r['robust_score']:.0f} ({d:+.0f})",
                "media" if d < 0 else "info")
    for cid in s0i.index.difference(s1i.index):
        add(cid, "Universo", "Non più nell'universo (vedi motivo di esclusione)", "media")

    if not m1.empty and not m0.empty:
        common = m1.index.intersection(m0.index)
        for metric, thr, direction, label, pts in METRIC_RULES:
            if metric not in m1.columns or metric not in m0.columns:
                continue
            d = (m1.loc[common, metric] - m0.loc[common, metric]).dropna()
            bad = d[d * direction >= thr]
            for cid, delta in bad.items():
                old, new = m0.loc[cid, metric], m1.loc[cid, metric]
                txt = (f"{label}: {old * 100:.1f}% → {new * 100:.1f}%" if pts else f"{label}: {old:.1f} → {new:.1f}")
                add(cid, "Fondamentali", txt, "media")

    k0 = set(zip(f0["company_id"], f0["code"]))
    for _, r in f1.iterrows():
        if r["severity"] in ("severe", "high", "medium") and (r["company_id"], r["code"]) not in k0:
            add(r["company_id"], "Nuova segnalazione", r["message"], {"severe": "alta", "high": "alta", "medium": "media"}[r["severity"]])
    df = pd.DataFrame(out)
    if df.empty:
        return df
    order = {"alta": 0, "media": 1, "info": 2}
    return df.sort_values(by="gravità", key=lambda s: s.map(order)).reset_index(drop=True)
