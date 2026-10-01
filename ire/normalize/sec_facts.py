"""SEC `companyfacts` → standardized annual series, TTM and latest balance sheet.

Key rules (all documented in METHODOLOGY.md):
* Fiscal-year values: duration facts from annual forms (10-K, 20-F, 40-F and amendments)
  lasting 330-400 days, keyed by period END date (the `fy` field of companyfacts is the
  fiscal year of the *filing*, not of the period, so it is never used as a period key).
* When several filings report the same period, the most recently filed value is used
  (captures restatements/reclassifications); the first-filed value is kept as
  `original_value` (point-in-time) and differences > 0.5% are marked `restated`.
* TTM (flows) = last FY + current YTD − prior-year YTD, from 10-Q facts. Without interim
  data (e.g. 20-F filers) TTM = last FY and the staleness is reported.
* Share counts/EPS filed BEFORE a stock split are multiplied/divided by the split ratio
  (domestic filers only; ADR ratio changes are not ordinary-share splits).
* Minor items never reported in any filing (e.g. no buybacks, no minority interest) are
  treated as 0 and tagged as an assumption. Debt is assumed 0 only when the balance sheet
  supports it (no interest expense, small non-current liabilities); capex and stock-based
  compensation are NEVER assumed 0 (left missing → dependent metrics missing).
* IFRS filers: lease liabilities are added to financial debt and lease principal payments
  are subtracted from FCF (IFRS 16 moves lease costs below EBITDA / into financing).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from ..sources.sec import ANNUAL_FORMS, QUARTERLY_FORMS
from .concepts import DURATION, INSTANT, ITEMS
from .model import Financials

SOURCE = "SEC XBRL companyfacts"
ASSUME_ZERO_IF_NEVER_REPORTED = {
    "dividends_paid", "buybacks", "acquisitions", "share_issuance", "goodwill", "intangibles",
    "minority_interest", "short_term_investments", "inventory", "preferred_equity",
}
# never reported ≠ zero for these: leaving them missing is safer than inventing a favourable 0
NEVER_ASSUME_ZERO = {"capex", "sbc", "total_debt"}
DEBT_PARTS = ["debt_total_reported", "debt_lt_total", "debt_noncurrent", "debt_current", "debt_lt_current", "st_borrowings"]
NICE_SPLIT_RATIOS = [1.25, 4 / 3, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 15, 20, 25, 50]
# US GAAP debt concepts that already include finance (capital) lease obligations
DEBT_WITH_LEASES = {"DebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                    "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsCurrent"}
CAPEX_BASE = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
CAPEX_COMPONENTS = [["PaymentsToAcquireOilAndGasPropertyAndEquipment", "PaymentsToAcquireOilAndGasProperty"],
                    ["PaymentsToAcquireOtherPropertyPlantAndEquipment"], ["PaymentsForCapitalImprovements"],
                    ["PaymentsToDevelopRealEstateAssets"], ["PaymentsToAcquireMachineryAndEquipment"]]


def _days(a: str, b: str) -> int:
    return (pd.Timestamp(b) - pd.Timestamp(a)).days


def detect_gaap(cf: dict[str, Any]) -> str:
    facts = cf.get("facts", {})
    return "IFRS" if len(facts.get("ifrs-full", {})) > len(facts.get("us-gaap", {})) else "US GAAP"


def detect_currency(cf: dict[str, Any]) -> str | None:
    """Currency of the MOST RECENT annual filing (a filer that switched from USD to EUR reporting must be
    read in EUR, otherwise all recent years are lost); the most frequent unit only as a tie-breaker."""
    counts: dict[str, int] = defaultdict(int)
    latest: dict[str, str] = {}
    facts = cf.get("facts", {})
    for tax in ("us-gaap", "ifrs-full"):
        for concept in ("Revenues", "Revenue", "NetIncomeLoss", "ProfitLoss", "Assets",
                        "RevenueFromContractWithCustomerExcludingAssessedTax"):
            node = facts.get(tax, {}).get(concept)
            if not node:
                continue
            for unit, arr in node.get("units", {}).items():
                if len(unit) == 3 and unit.isalpha() and unit.isupper():
                    counts[unit] += len(arr)
                    filed = [e.get("filed") or "" for e in arr if e.get("form") in ANNUAL_FORMS]
                    if filed:
                        latest[unit] = max(latest.get(unit, ""), max(filed))
    if not counts:
        return None
    if latest:
        return max(latest, key=lambda u: (latest[u], counts[u]))
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _add_capex_sum(cf: dict[str, Any]) -> dict[str, Any]:
    """Adds the synthetic concept us-gaap:IRE_CapexComponentsSum: per filing and period, the capex reported in
    separate lines (oil & gas properties, other PP&E, capital improvements, real-estate development,
    machinery). If a PP&E total is reported and is at least the sum of the other lines it is taken as
    inclusive; otherwise the lines are added to it."""
    us = cf.get("facts", {}).get("us-gaap", {})
    names = CAPEX_BASE + [c for grp in CAPEX_COMPONENTS for c in grp]
    if sum(1 for c in names if c in us) < 2:
        return cf
    by_key: dict[tuple, dict[str, float]] = defaultdict(dict)
    proto: dict[tuple, dict[str, Any]] = {}
    units = set()
    for c in names:
        for unit, arr in us.get(c, {}).get("units", {}).items():
            units.add(unit)
            for e in arr:
                k = (unit, e.get("filed"), e.get("start"), e.get("end"))     # same filing, same period
                by_key[k][c] = float(e["val"])
                proto.setdefault(k, e)
    out_units: dict[str, list] = defaultdict(list)
    for k, vals in by_key.items():
        base = max([vals[c] for c in CAPEX_BASE if c in vals], default=None)
        comps = sum(max(vals[c] for c in grp if c in vals) for grp in CAPEX_COMPONENTS if any(c in vals for c in grp))
        has_comp = any(c in vals for grp in CAPEX_COMPONENTS for c in grp)
        if base is None:
            val = comps
        elif not has_comp or base >= comps:
            val = base
        else:
            val = base + comps
        e = dict(proto[k])
        e["val"] = val
        out_units[k[0]].append(e)
    cf = dict(cf)
    cf["facts"] = dict(cf.get("facts", {}))
    cf["facts"]["us-gaap"] = dict(us)
    cf["facts"]["us-gaap"]["IRE_CapexComponentsSum"] = {"label": "Capex (somma delle voci, calcolata)",
                                                        "units": dict(out_units)}
    return cf


def _entries(cf: dict[str, Any], tax: str, concept: str, unit: str) -> list[dict[str, Any]]:
    node = cf.get("facts", {}).get(tax, {}).get(concept)
    if not node:
        return []
    return node.get("units", {}).get(unit, [])


def _unit_for(item_def: dict[str, Any], currency: str) -> str:
    if item_def.get("shares"):
        return "shares"
    if item_def.get("per_share"):
        return f"{currency}/shares"
    return currency


def _candidates(cf: dict[str, Any], item: str, currency: str) -> list[tuple[str, str, list[dict[str, Any]]]]:
    d = ITEMS[item]
    unit = _unit_for(d, currency)
    out = []
    for tax in ("dei", "us-gaap", "ifrs-full"):
        for concept in d.get(tax, []):
            arr = _entries(cf, tax, concept, unit)
            if arr:
                out.append((tax, concept, arr))
    return out


def _split_factor(filed: str, splits: list[tuple[str, float]]) -> float:
    f = 1.0
    for d, r in splits:
        if filed < d:
            f *= r
    return f


def _pick(entries: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (current=latest filed, original=first filed)."""
    s = sorted(entries, key=lambda e: (e.get("filed") or "", e.get("accn") or ""))
    return s[-1], s[0]


def _merge_same_filing_classes(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """dei share counts may appear once per share class with the same accn/end: sum them."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        groups[(e.get("accn"), e.get("end"))].append(e)
    out = []
    for (_, _), g in groups.items():
        vals = {e["val"] for e in g}
        if len(vals) > 1:
            m = dict(g[0])
            m["val"] = float(sum(vals))
            m["_classes_summed"] = len(vals)
            out.append(m)
        else:
            out.append(g[0])
    return out


def normalize_companyfacts(
    company_id: str,
    cf: dict[str, Any],
    splits: list[tuple[str, float]] | None = None,
    apply_splits: bool = True,
    fetched_at: str | None = None,
) -> Financials:
    splits = sorted(splits or []) if apply_splits else []
    cf = _add_capex_sum(cf)
    currency = detect_currency(cf)
    fin = Financials(company_id=company_id, currency=currency, source=SOURCE, tier="A", annual=pd.DataFrame())
    fin.gaap = detect_gaap(cf)
    us_facts = cf.get("facts", {}).get("us-gaap", {})
    fin.debt_has_leases = any(c in us_facts for c in DEBT_WITH_LEASES)
    if currency is None:
        fin.flags.append({"code": "NO_MONETARY_FACTS", "severity": "data",
                          "message": "Nessun dato contabile XBRL in valuta trovato"})
        return fin

    # ---------------------------------------------------------------- annual durations
    annual: dict[str, dict[str, Any]] = defaultdict(dict)          # item -> end -> record
    all_filed: list[str] = []
    for item, d in ITEMS.items():
        if d["type"] != DURATION:
            continue
        for tax, concept, arr in _candidates(cf, item, currency):
            by_end: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for e in arr:
                if e.get("form") not in ANNUAL_FORMS or not e.get("start") or not e.get("end"):
                    continue
                dur = _days(e["start"], e["end"])
                if 330 <= dur <= 400:
                    by_end[e["end"]].append(e)
            for end, es in by_end.items():
                if end in annual[item]:
                    continue  # higher-priority concept already filled this period
                cur, orig = _pick(es)
                all_filed.append(cur.get("filed") or "")
                annual[item][end] = {"entry": cur, "orig": orig, "concept": f"{tax}:{concept}", "start": cur["start"]}

    # fiscal year ends from core flows
    ends = set()
    for key in ("revenue", "net_income", "ocf", "operating_income"):
        ends.update(annual.get(key, {}).keys())
    ends = sorted(ends)
    # collapse ends closer than 300 days (fiscal-year change / 52-53 week drift): keep later
    fy_ends: list[str] = []
    for e in ends:
        if fy_ends and _days(fy_ends[-1], e) < 300:
            fy_ends[-1] = e
        else:
            fy_ends.append(e)
    if not fy_ends:
        fin.flags.append({"code": "NO_ANNUAL_DATA", "severity": "data",
                          "message": "Nessun bilancio annuale (10-K/20-F/40-F) trovato nei dati XBRL"})
        return fin

    # ---------------------------------------------------------------- instants at FY ends
    instants_all: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)   # item -> (tax:concept, entry)
    for item, d in ITEMS.items():
        if d["type"] != INSTANT:
            continue
        for tax, concept, arr in _candidates(cf, item, currency):
            if item == "shares_outstanding":
                arr = _merge_same_filing_classes(arr)
            for e in arr:
                if e.get("form") in ANNUAL_FORMS or e.get("form") in QUARTERLY_FORMS:
                    instants_all[item].append((f"{tax}:{concept}", concept, e))

    def instant_at(item: str, target: str, tol: int = 5):
        cands_by_concept: dict[str, list[dict[str, Any]]] = defaultdict(list)
        order: list[str] = []
        for key, _, e in instants_all.get(item, []):
            if abs(_days(target, e["end"])) <= tol:
                if key not in cands_by_concept:
                    order.append(key)
                cands_by_concept[key].append(e)
        # respect concept priority as listed in ITEMS (order of appearance == priority)
        for key in order:
            cur, orig = _pick(cands_by_concept[key])
            return {"entry": cur, "orig": orig, "concept": key, "start": None}
        return None

    # ---------------------------------------------------------------- build table
    rows: dict[str, dict[str, float]] = {}
    provenance: dict[tuple[str, str], dict[str, Any]] = {}
    for end in fy_ends:
        row: dict[str, float] = {}
        for item, d in ITEMS.items():
            rec = None
            if d["type"] == DURATION:
                rec = annual.get(item, {}).get(end)
                if rec is None:  # tolerate a few days of drift
                    for e2, r2 in annual.get(item, {}).items():
                        if abs(_days(end, e2)) <= 7:
                            rec = r2
                            break
            else:
                tol = 100 if item == "shares_outstanding" else 5   # cover-page date is after FY end
                if item == "shares_outstanding":
                    rec = _shares_after(instants_all.get(item, []), end, tol)
                else:
                    rec = instant_at(item, end)
            if rec is None:
                continue
            val = float(rec["entry"]["val"])
            orig_val = float(rec["orig"]["val"])
            adj = False
            if d.get("shares") or d.get("per_share"):
                f_cur = _split_factor(rec["entry"].get("filed") or "", splits)
                f_orig = _split_factor(rec["orig"].get("filed") or "", splits)
                if f_cur != 1.0:
                    adj = True
                    val = val * f_cur if d.get("shares") else val / f_cur
                if f_orig != 1.0:
                    orig_val = orig_val * f_orig if d.get("shares") else orig_val / f_orig
            row[item] = val
            provenance[(item, end)] = {**rec, "value": val, "orig_value": orig_val, "split_adjusted": adj}
        rows[end] = row

    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # ---------------------------------------------------------------- derived items
    reported_items = {it for it in ITEMS if it in df.columns and df[it].notna().any()}
    df = _derive(df, fin, reported_items)

    # ---------------------------------------------------------------- TTM + latest
    _compute_ttm(cf, currency, fin, df, fy_ends[-1], splits)
    # "latest" balance sheet = the most recent balance-sheet date (of total assets). An item whose last report
    # is OLDER than that date is not current: minor items are then 0 (assumption), the others unknown —
    # never a value from years ago (e.g. debt repaid and no longer shown).
    anchor = max((e["end"] for _, _, e in instants_all.get("total_assets", [])), default=None)
    for item, d in ITEMS.items():
        if d["type"] != INSTANT or not instants_all.get(item):
            continue
        last_end = max(e["end"] for _, _, e in instants_all[item])
        if anchor and item != "shares_outstanding" and _days(last_end, anchor) > 5:
            if item in ASSUME_ZERO_IF_NEVER_REPORTED:
                fin.latest[item] = (0.0, pd.Timestamp(anchor))
                fin.notes.append(f"{item}: non più riportato dopo il {last_end} → 0 nell'ultimo bilancio (assunzione).")
            continue
        rec = _shares_after(instants_all[item], last_end, 0) if item == "shares_outstanding" else instant_at(item, last_end, tol=0)
        if rec is None:
            continue
        val = float(rec["entry"]["val"])
        if d.get("shares"):
            val *= _split_factor(rec["entry"].get("filed") or "", splits)
        fin.latest[item] = (val, pd.Timestamp(last_end))
        provenance[(item, "LATEST")] = {**rec, "value": val, "orig_value": val, "split_adjusted": False}
    fin.annual = df
    _latest_debt(fin)
    fin.last_filed = max(all_filed) if all_filed else None
    fin.fiscal_year_end = pd.Timestamp(fy_ends[-1]).strftime("%m-%d")

    # ---------------------------------------------------------------- restatements
    for (item, end), p in provenance.items():
        if item not in ("revenue", "net_income", "ocf", "equity", "total_assets"):
            continue
        a, b = p["value"], p["orig_value"]
        if a and b and abs(a - b) / max(abs(a), abs(b)) > 0.005 and end != "LATEST":
            p["restated"] = True

    # ---------------------------------------------------------------- split sanity check
    _check_share_jumps(fin, df)

    # ---------------------------------------------------------------- DB rows
    for (item, end), p in provenance.items():
        e = p["entry"]
        fin.fact_rows.append({
            "company_id": company_id,
            "item": item,
            "period_type": "INSTANT" if end == "LATEST" else "FY",
            "period_start": p.get("start"),
            "period_end": e["end"] if end == "LATEST" else end,
            "value": p["value"],
            "unit": _unit_for(ITEMS[item], currency),
            "source": SOURCE,
            "source_ref": e.get("accn"),
            "concept": p["concept"],
            "form": e.get("form"),
            "filed": e.get("filed"),
            "derivation": ("somma di più classi di azioni" if e.get("_classes_summed") else None),
            "restated": 1 if p.get("restated") else 0,
            "original_value": p["orig_value"],
            "split_adjusted": 1 if p.get("split_adjusted") else 0,
            "fetched_at": fetched_at,
        })
    _derived_fact_rows(fin, df, fetched_at)
    return fin


DERIVED_FORMULAS = {
    "total_debt": "debito lungo termine (incl. quota corrente) + debito a breve (vedi regole debito)",
    "fcf": "flusso di cassa operativo − capex",
    "ebitda": "utile operativo + ammortamenti",
    "ebit": "utile operativo (o utile ante imposte + interessi passivi)",
}


def _derived_fact_rows(fin: Financials, df: pd.DataFrame, fetched_at: str | None) -> None:
    existing = {(r["item"], r["period_type"], r["period_end"]) for r in fin.fact_rows}
    cur = fin.currency
    for end, row in df.iterrows():
        e = end.strftime("%Y-%m-%d")
        for item in list(DERIVED_FORMULAS) + ["gross_profit"] + sorted(fin.assumed_zero):
            if item not in df.columns or pd.isna(row.get(item)) or (item, "FY", e) in existing:
                continue
            if item in fin.assumed_zero:
                deriv = "mai riportato in alcun filing → trattato come 0 (ASSUNZIONE)"
            elif item == "gross_profit":
                deriv = "ricavi − costo del venduto"
            else:
                deriv = DERIVED_FORMULAS[item]
            fin.fact_rows.append({
                "company_id": fin.company_id, "item": item, "period_type": "FY", "period_start": None,
                "period_end": e, "value": float(row[item]), "unit": cur,
                "source": f"Calcolato da {fin.source}", "source_ref": None, "concept": None, "form": None,
                "filed": None, "derivation": deriv, "restated": 0, "original_value": None,
                "split_adjusted": 0, "fetched_at": fetched_at,
            })
            existing.add((item, "FY", e))
    if fin.ttm_end is not None:
        te = fin.ttm_end.strftime("%Y-%m-%d")
        for item in ("fcf", "ebitda", "ebit", "gross_profit"):
            if item in fin.ttm and (item, "TTM", te) not in existing:
                fin.fact_rows.append({
                    "company_id": fin.company_id, "item": item, "period_type": "TTM", "period_start": None,
                    "period_end": te, "value": float(fin.ttm[item]), "unit": cur,
                    "source": f"Calcolato da {fin.source}", "source_ref": None, "concept": None, "form": None,
                    "filed": None, "derivation": DERIVED_FORMULAS.get(item, "ricavi − costo del venduto") + " (TTM)",
                    "restated": 0, "original_value": None, "split_adjusted": 0, "fetched_at": fetched_at,
                })


def _shares_after(entries, target: str, tol: int):
    """Share count reported on the cover page shortly AFTER the period end (dei), or at period end."""
    best = None
    for key, concept, e in entries:
        delta = _days(target, e["end"])
        if 0 <= delta <= max(tol, 0) or (tol == 0 and delta == 0):
            score = (0 if concept == "EntityCommonStockSharesOutstanding" else 1, delta)
            if best is None or score < best[0]:
                best = (score, key, e)
    if best is None:
        return None
    _, key, e = best
    same = [x for k, _, x in entries if k == key and x["end"] == e["end"]]
    cur, orig = _pick(same)
    return {"entry": cur, "orig": orig, "concept": key, "start": None}


def _lease_total(r: pd.Series) -> float:
    g = lambda k: r.get(k) if k in r.index and pd.notna(r.get(k)) else None  # noqa: E731
    if g("lease_liab_total") is not None:
        return float(g("lease_liab_total"))
    parts = [x for x in (g("lease_liab_noncurrent"), g("lease_liab_current")) if x is not None]
    return float(sum(parts)) if parts else 0.0


def _derive(df: pd.DataFrame, fin: Financials, reported: set[str]) -> pd.DataFrame:
    """Derived line items. Each derivation is recorded in fin.notes / assumed_zero / flags."""
    df = df.copy()
    for item in ASSUME_ZERO_IF_NEVER_REPORTED:
        if item not in reported:
            df[item] = 0.0
            fin.assumed_zero.add(item)
    # gross profit
    if "gross_profit" not in df.columns:
        df["gross_profit"] = np.nan
    if "revenue" in df.columns and "cost_of_revenue" in df.columns:
        calc = df["revenue"] - df["cost_of_revenue"]
        mask = df["gross_profit"].isna() & calc.notna()
        if mask.any():
            df.loc[mask, "gross_profit"] = calc[mask]
            fin.notes.append("Utile lordo calcolato come ricavi − costo del venduto dove non riportato.")
    for c in ("ocf", "capex", "operating_income", "da", "interest_expense", "pretax_income", "lease_payments",
              "total_assets", "total_liabilities", "current_liabilities", "ppe_net"):
        if c not in df.columns:
            df[c] = np.nan
    ifrs = getattr(fin, "gaap", "US GAAP") == "IFRS"
    # leases added to debt: IFRS 16 leases; US GAAP finance leases unless the debt concept already includes them
    add_leases = ifrs or not getattr(fin, "debt_has_leases", False)
    fin.add_leases = add_leases
    # total liabilities: many US filers do not tag "Liabilities" → liabilities and equity − equity (incl. NCI)
    if "liabilities_and_equity" in df.columns:
        eq_all = df["equity_incl_nci"] if "equity_incl_nci" in df.columns else pd.Series(np.nan, index=df.index)
        if "equity" in df.columns:
            eq_all = eq_all.fillna(df["equity"] + df.get("minority_interest", 0.0).fillna(0.0))
        tmp = df["temporary_equity"].fillna(0.0) if "temporary_equity" in df.columns else 0.0
        calc = df["liabilities_and_equity"] - eq_all - tmp
        mask = df["total_liabilities"].isna() & calc.notna()
        if mask.any():
            df.loc[mask, "total_liabilities"] = calc[mask]
            fin.notes.append("Passività totali calcolate come totale passivo e patrimonio − patrimonio netto "
                             "(voce 'Liabilities' non riportata).")
    # debt
    df["total_debt"] = df.apply(_debt_row, axis=1)
    any_debt_concept = any(c in reported for c in DEBT_PARTS)
    last = df.iloc[-1]
    if not any_debt_concept:
        ta, tl, cl = last.get("total_assets"), last.get("total_liabilities"), last.get("current_liabilities")
        noncur_share = ((tl - cl) / ta) if all(pd.notna(x) for x in (ta, tl, cl)) and ta > 0 else None
        no_interest = ("interest_expense" not in reported) or not (df["interest_expense"].fillna(0) > 0).any()
        if no_interest and noncur_share is not None and noncur_share < 0.15:
            df["total_debt"] = 0.0
            fin.assumed_zero.add("total_debt")
            fin.flags.append({"code": "ASSUMED_ZERO_DEBT", "severity": "data",
                              "message": "Nessun debito finanziario riportato nei filing XBRL: assunto zero perché non ci "
                                         f"sono interessi passivi e le passività non correnti sono solo il {noncur_share:.0%} "
                                         "dell'attivo (ASSUNZIONE, da verificare)."})
        else:
            fin.flags.append({"code": "DEBT_UNKNOWN", "severity": "data",
                              "message": "Debito finanziario non identificabile nei dati XBRL (voce con nome non standard): "
                                         "EV, debito netto e multipli EV non calcolati."})
    else:
        if pd.isna(df["total_debt"].iloc[-1]) and df["total_debt"].notna().any():
            # debt shown in earlier years, no debt line in the latest balance sheet: probably repaid. Assumed 0
            # only with the same evidence required above (no interest expense in the last year)
            ie_last = df["interest_expense"].iloc[-1]
            ta, tl, cl = last.get("total_assets"), last.get("total_liabilities"), last.get("current_liabilities")
            small_noncur = all(pd.notna(x) for x in (ta, tl, cl)) and ta > 0 and (tl - cl) / ta < 0.15
            if (pd.notna(ie_last) and ie_last <= 0) or (pd.isna(ie_last) and small_noncur):
                df.loc[df.index[-1], "total_debt"] = 0.0
                fin.flags.append({"code": "ASSUMED_DEBT_REPAID", "severity": "data",
                                  "message": "Debito riportato negli anni precedenti ma non nell'ultimo bilancio, senza "
                                             "interessi passivi nell'ultimo anno: assunto rimborsato (0) — ASSUNZIONE, da verificare."})
            else:
                fin.flags.append({"code": "DEBT_UNKNOWN", "severity": "data",
                                  "message": "Nell'ultimo bilancio il debito non è identificabile ma ci sono interessi passivi: "
                                             "EV, debito netto e multipli EV dell'ultimo anno non calcolati."})
        cols = [c for c in DEBT_PARTS if c in df.columns]
        only_current = (last[[c for c in cols if c in ("debt_current", "debt_lt_current", "st_borrowings")]].notna().any()
                        and not last[[c for c in cols if c in ("debt_total_reported", "debt_lt_total", "debt_noncurrent")]].notna().any())
        ta, tl, cl = last.get("total_assets"), last.get("total_liabilities"), last.get("current_liabilities")
        if only_current and all(pd.notna(x) for x in (ta, tl, cl)) and ta > 0 and (tl - cl) / ta > 0.2:
            fin.flags.append({"code": "DEBT_PARTIAL", "severity": "data",
                              "message": "Nell'ultimo bilancio è riportato solo il debito a breve, ma le passività non correnti "
                                         "sono rilevanti: il debito totale potrebbe essere sottostimato."})
    if add_leases:
        leases = df.apply(_lease_total, axis=1)
        if (leases > 0).any():
            df["lease_liabilities"] = leases
            df["total_debt"] = df["total_debt"] + leases
            fin.notes.append("IFRS 16: passività per leasing aggiunte al debito finanziario." if ifrs else
                             "US GAAP: passività per leasing FINANZIARI aggiunte al debito (come i leasing IFRS 16).")
    # capex
    if "capex" not in reported:
        ppe, ta = last.get("ppe_net"), last.get("total_assets")
        if pd.notna(ppe) and pd.notna(ta) and ta > 0 and ppe / ta < 0.01:
            df["capex"] = 0.0
            fin.assumed_zero.add("capex")
            fin.flags.append({"code": "ASSUMED_ZERO_CAPEX", "severity": "data",
                              "message": "Capex mai riportato; immobilizzazioni materiali < 1% dell'attivo: capex assunto zero "
                                         "(ASSUNZIONE)."})
        else:
            fin.flags.append({"code": "CAPEX_UNKNOWN", "severity": "data",
                              "message": "Capex non identificabile nei dati XBRL: free cash flow e metriche collegate non calcolati."})
    if "sbc" not in reported:
        fin.notes.append("Compensi in azioni (SBC) mai riportati: metriche che li usano non calcolate (non assunti zero).")
    # FCF, EBITDA
    df["fcf"] = df["ocf"] - df["capex"]
    if df["lease_payments"].notna().any():
        df["fcf"] = df["fcf"] - df["lease_payments"].fillna(0.0)
        fin.notes.append("FCF = flusso operativo − capex − rimborsi quota capitale dei leasing "
                         + ("(IFRS 16)." if ifrs else "finanziari (US GAAP)."))
    df["ebit"] = df["operating_income"]
    mask = df["ebit"].isna() & df["pretax_income"].notna() & df["interest_expense"].notna()
    if mask.any():
        df.loc[mask, "ebit"] = df.loc[mask, "pretax_income"] + df.loc[mask, "interest_expense"]
        fin.notes.append("EBIT = utile ante imposte + interessi passivi dove l'utile operativo non è riportato.")
    df["ebitda"] = df["ebit"] + df["da"]
    minor = sorted(fin.assumed_zero - {"total_debt", "capex"})
    if minor:
        fin.notes.append("Voci mai riportate nei filing, trattate come 0 (assunzione): " + ", ".join(minor) + ".")
    return df


def _debt_row(r: pd.Series) -> float:
    g = lambda k: r.get(k) if k in r.index and pd.notna(r.get(k)) else None  # noqa: E731
    if g("debt_total_reported") is not None:
        return float(g("debt_total_reported"))
    cur = g("debt_current")
    if cur is None:
        parts = [x for x in (g("debt_lt_current"), g("st_borrowings")) if x is not None]
        cur = sum(parts) if parts else None
    noncur = g("debt_noncurrent")
    if noncur is None and g("debt_lt_total") is not None:
        lt_total = g("debt_lt_total")
        if g("debt_lt_current") is None and g("debt_current") is not None:
            # long-term total ALREADY includes its current portion, which is also inside "debt current":
            # add only the other short-term borrowings to avoid counting the current maturities twice
            return float(lt_total + (g("st_borrowings") or 0.0))
        noncur = lt_total - (g("debt_lt_current") or 0.0)
    if noncur is None and cur is None:
        return np.nan
    return float((noncur or 0.0) + (cur or 0.0))


def _latest_debt(fin: Financials) -> None:
    parts = {k: fin.latest[k][0] for k in DEBT_PARTS if k in fin.latest}
    if not parts:
        if "total_debt" in fin.assumed_zero:
            fin.latest["total_debt"] = (0.0, fin.annual.index.max() if len(fin.annual.index) else pd.NaT)
        return
    dates = [fin.latest[k][1] for k in parts]
    latest_date = max(dates)
    # only combine components reported at the same latest date
    same = {k: v for k, v in parts.items() if fin.latest[k][1] == latest_date}
    val = _debt_row(pd.Series(same))
    if pd.notna(val):
        if getattr(fin, "add_leases", getattr(fin, "gaap", "") == "IFRS"):
            lease = {k: fin.latest[k][0] for k in ("lease_liab_total", "lease_liab_noncurrent", "lease_liab_current")
                     if k in fin.latest and fin.latest[k][1] == latest_date}
            val += _lease_total(pd.Series(lease)) if lease else 0.0
        fin.latest["total_debt"] = (float(val), latest_date)


def _compute_ttm(cf, currency, fin: Financials, df: pd.DataFrame, last_fy_end: str, splits) -> None:
    last_fy = pd.Timestamp(last_fy_end)
    ttm: dict[str, float] = {}
    rolled: set[str] = set()
    ttm_end = None
    used_interim = False
    for item, d in ITEMS.items():
        if d["type"] != DURATION or d.get("per_share") or d.get("shares"):
            continue
        fy_val = df[item].get(last_fy) if item in df.columns else None
        if fy_val is None or pd.isna(fy_val):
            continue
        best = None
        for tax, concept, arr in _candidates(cf, item, currency):
            ytd = [e for e in arr if e.get("form") in QUARTERLY_FORMS and e.get("start") and e.get("end")
                   and pd.Timestamp(e["end"]) > last_fy
                   and abs((pd.Timestamp(e["start"]) - last_fy).days - 1) <= 7
                   and 80 <= _days(e["start"], e["end"]) <= 290]
            if not ytd:
                continue
            cur, _ = _pick([x for x in ytd if x["end"] == max(y["end"] for y in ytd)])
            dur = _days(cur["start"], cur["end"])
            prior_end = pd.Timestamp(cur["end"]) - pd.Timedelta(days=365)
            prior = [e for e in arr if e.get("start") and e.get("end")
                     and abs((pd.Timestamp(e["end"]) - prior_end).days) <= 10
                     and abs(_days(e["start"], e["end"]) - dur) <= 10]
            if not prior:
                continue
            p, _ = _pick(prior)
            best = (cur, p, f"{tax}:{concept}")
            break
        if best:
            cur, p, concept = best
            ttm[item] = float(fy_val) + float(cur["val"]) - float(p["val"])
            e_end = pd.Timestamp(cur["end"])
            ttm_end = e_end if ttm_end is None else max(ttm_end, e_end)
            used_interim = True
            rolled.add(item)
            fin.fact_rows.append({
                "company_id": fin.company_id, "item": item, "period_type": "TTM",
                "period_start": None, "period_end": cur["end"], "value": ttm[item], "unit": currency,
                "source": SOURCE, "source_ref": cur.get("accn"), "concept": concept, "form": cur.get("form"),
                "filed": cur.get("filed"),
                "derivation": f"FY {last_fy.date()} + YTD al {cur['end']} ({cur['val']:.6g}) − YTD anno prec. ({p['val']:.6g})",
                "restated": 0, "original_value": None, "split_adjusted": 0, "fetched_at": None,
            })
        else:
            ttm[item] = float(fy_val)
    # consistency: all TTM items should refer to the same end; items that could not be rolled
    # forward are left at FY values → mixing periods. Drop the roll-forward if coverage is partial.
    core = ["revenue", "net_income", "ocf"]
    if used_interim:
        # keep only items rolled forward to the most recent interim date
        max_end = ttm_end.strftime("%Y-%m-%d")
        stale = {r["item"] for r in fin.fact_rows if r["period_type"] == "TTM" and r["period_end"] != max_end}
        for item in stale:
            rolled.discard(item)
            ttm[item] = float(df[item].get(last_fy))
        fin.fact_rows = [r for r in fin.fact_rows if not (r["period_type"] == "TTM" and r["item"] in stale)]
    if used_interim and len([k for k in core if k in rolled]) < len([k for k in core if k in ttm]):
        # partial → revert to FY to avoid mixing periods
        for item in list(ttm):
            if item in df.columns and pd.notna(df[item].get(last_fy)):
                ttm[item] = float(df[item].get(last_fy))
        fin.fact_rows = [r for r in fin.fact_rows if r["period_type"] != "TTM"]
        fin.notes.append("TTM non calcolabile in modo coerente (dati trimestrali parziali): uso l'ultimo anno fiscale.")
        ttm_end, used_interim = None, False
        rolled = set()
    if used_interim:
        # items that could not be rolled forward would mix periods: drop them from TTM
        for item in list(ttm):
            if item not in rolled and item not in fin.assumed_zero:
                del ttm[item]
    # derived TTM
    g = lambda k: ttm.get(k)  # noqa: E731
    if g("ocf") is not None:
        cap = g("capex") if g("capex") is not None else (0.0 if "capex" in fin.assumed_zero else None)
        ttm["fcf"] = g("ocf") - cap if cap is not None else np.nan
        if pd.notna(ttm["fcf"]):
            lp = g("lease_payments")
            if lp is None and "lease_payments" in df.columns and pd.notna(df["lease_payments"].get(last_fy)):
                lp = float(df["lease_payments"].get(last_fy))
            if lp:
                ttm["fcf"] -= lp
    if g("operating_income") is not None:
        ttm["ebit"] = g("operating_income")
    elif g("pretax_income") is not None and g("interest_expense") is not None:
        # same period as the other TTM items (never an older fiscal-year EBIT mixed with rolled-forward items)
        ttm["ebit"] = g("pretax_income") + g("interest_expense")
    if ttm.get("ebit") is not None and g("da") is not None:
        ttm["ebitda"] = ttm["ebit"] + g("da")
    if g("gross_profit") is None and g("revenue") is not None and g("cost_of_revenue") is not None:
        ttm["gross_profit"] = g("revenue") - g("cost_of_revenue")
    for z in fin.assumed_zero:
        ttm.setdefault(z, 0.0)
    fin.ttm = {k: v for k, v in ttm.items() if v is not None and pd.notna(v)}
    fin.ttm_end = ttm_end if used_interim else last_fy
    fin.ttm_derivation = ("TTM = ultimo anno fiscale + YTD corrente − YTD anno precedente (10-Q)"
                          if used_interim else "TTM = ultimo anno fiscale disponibile (nessun trimestrale XBRL più recente)")


def _check_share_jumps(fin: Financials, df: pd.DataFrame) -> None:
    for col in ("shares_diluted", "shares_outstanding"):
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if len(s) < 2:
            continue
        ratios = (s / s.shift(1)).dropna()
        for dt, r in ratios.items():
            for nice in NICE_SPLIT_RATIOS:
                if abs(r - nice) / nice < 0.04 or abs(r - 1 / nice) / (1 / nice) < 0.04:
                    fin.flags.append({
                        "code": "POSSIBLE_UNADJUSTED_SPLIT", "severity": "data",
                        "message": f"Numero di azioni ({col}) cambia di ×{r:.2f} nel periodo terminato il {dt.date()}: "
                                   "possibile split non rettificato o operazione straordinaria. "
                                   "Le metriche per azione che attraversano questa data non sono affidabili.",
                        "evidence": {"column": col, "date": str(dt.date()), "ratio": float(r)},
                    })
                    fin.notes.append(f"Salto anomalo nel numero di azioni al {dt.date()} (×{r:.2f}).")
                    break
