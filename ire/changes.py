"""What changed between two analysis runs (fundamental deterioration monitor)."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from ire.qualitative import is_text_flag
from ire.scoring import NEGATIVE_CLASSES

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

# exclusion reasons that usually depend on a data download, not on the company leaving the investable universe
TEMPORARY_EXCLUSION = ("non scaricabili", "errore", "non disponibil", "vuoti", "nessun prezzo")


def _load(con: sqlite3.Connection, run_id: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    s = pd.read_sql_query("SELECT company_id, robust_score, classification, valuation_verdict, detail FROM scores "
                          "WHERE run_id=?", con, params=[run_id])
    s["deep"] = s["detail"].map(_deep_done)
    s = s.drop(columns=["detail"])
    m = pd.read_sql_query("SELECT company_id, metric, value FROM metrics WHERE run_id=?", con, params=[run_id])
    m = m.pivot_table(index="company_id", columns="metric", values="value", aggfunc="last") if not m.empty else pd.DataFrame()
    f = pd.read_sql_query("SELECT company_id, code, severity, message FROM flags WHERE run_id=?", con, params=[run_id])
    return s, m, f


def _deep_done(detail: str | None) -> bool:
    try:
        return bool(json.loads(detail or "{}").get("deep_analysis"))
    except (ValueError, AttributeError):
        return False


def run_modes(con: sqlite3.Connection, new_run: int, old_run: int) -> tuple[str | None, str | None]:
    get = lambda r: (con.execute("SELECT mode FROM runs WHERE run_id=?", (r,)).fetchone() or [None])[0]  # noqa: E731
    return get(new_run), get(old_run)


def compute_changes(con: sqlite3.Connection, new_run: int, old_run: int) -> pd.DataFrame:
    """Rows: company, type, change, severity. If the two runs used different modes (different universes),
    entries/exits of the universe are not reported (they would only reflect the different thresholds)."""
    s1, m1, f1 = _load(con, new_run)
    s0, m0, f0 = _load(con, old_run)
    mode_new, mode_old = run_modes(con, new_run, old_run)
    same_mode = mode_new == mode_old
    names = pd.read_sql_query("SELECT company_id, ticker, name, in_universe, exclusion_reason FROM companies",
                              con).set_index("company_id")
    out = []

    def add(cid, kind, msg, sev):
        out.append({"company_id": cid, "ticker": names["ticker"].get(cid), "name": names["name"].get(cid),
                    "tipo": kind, "cambiamento": msg, "gravità": sev})

    s0i, s1i = s0.set_index("company_id"), s1.set_index("company_id")
    for cid, r in s1i.iterrows():
        if cid not in s0i.index:
            if same_mode:
                add(cid, "Universo", "Nuova società analizzata", "info")
            continue
        o = s0i.loc[cid]
        if r["classification"] != o["classification"]:
            bad = r["classification"] in NEGATIVE_CLASSES
            add(cid, "Classificazione", f"{o['classification']} → {r['classification']}", "alta" if bad else "info")
        if r["valuation_verdict"] != o["valuation_verdict"] and r["valuation_verdict"] and o["valuation_verdict"]:
            add(cid, "Valutazione", f"{o['valuation_verdict']} → {r['valuation_verdict']}", "info")
        if pd.notna(r["robust_score"]) and pd.notna(o["robust_score"]) and abs(r["robust_score"] - o["robust_score"]) >= 10:
            d = r["robust_score"] - o["robust_score"]
            add(cid, "Punteggio", f"Punteggio robusto {o['robust_score']:.0f} → {r['robust_score']:.0f} ({d:+.0f})",
                "media" if d < 0 else "info")
    if same_mode:
        for cid in s0i.index.difference(s1i.index):
            reason = names["exclusion_reason"].get(cid)
            reason = reason if isinstance(reason, str) and reason else None
            if reason and any(k in reason for k in TEMPORARY_EXCLUSION):
                add(cid, "Universo", f"Non analizzata in questa esecuzione: {reason} (possibile problema temporaneo "
                                     "di dati, verrà ritentata)", "info")
            elif reason:
                add(cid, "Universo", f"Uscita dall'universo: {reason}", "media")
            else:
                add(cid, "Universo", "Non analizzata in questa esecuzione (motivo non registrato)", "info")

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

    # new flags; a TEXT flag counts as new only if the annual report was analysed in BOTH runs
    # (otherwise a company simply entering the deep-analysis shortlist would look like a new red flag)
    k0 = set(zip(f0["company_id"], f0["code"]))
    deep0 = set(s0.loc[s0["deep"], "company_id"])
    for _, r in f1.iterrows():
        if r["severity"] not in ("severe", "high", "medium") or (r["company_id"], r["code"]) in k0:
            continue
        if r["company_id"] not in s0i.index:
            continue                                   # company not analysed last time: nothing to compare
        if is_text_flag(r["code"]) and r["company_id"] not in deep0:
            continue
        add(r["company_id"], "Nuova segnalazione", r["message"], {"severe": "alta", "high": "alta", "medium": "media"}[r["severity"]])
    df = pd.DataFrame(out)
    if df.empty:
        return df
    order = {"alta": 0, "media": 1, "info": 2}
    return df.sort_values(by="gravità", key=lambda s: s.map(order), kind="stable").reset_index(drop=True)
