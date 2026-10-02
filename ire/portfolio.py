"""Portfolio construction and portfolio analytics.

Construction (not "top-N by score"):
  1. Eligibility: robust score percentile ≥ threshold, confidence not 'bassa', no red-flag /
     value-trap / deteriorating / insufficient-data classification, enough RECENT weekly returns
     (≥ `min_weeks_3y` of the last 156 weeks, last price ≤ 4 weeks old), and — for tickers added by
     the user — the same liquidity/size thresholds as the rest of the universe.
  2. Greedy selection maximizing   robust_score − λ·100·max(0, avg_corr_with_selected − 0.2)
     (+ a small bonus for names already held, to limit turnover) subject to: pairwise correlation
     ≤ max_pair_correlation (an UNKNOWN correlation is a rejection, not a pass), sector/region
     name counts compatible with the weight caps.
  3. If fewer than `min_positions` names qualify, the percentile threshold is lowered step by step
     (logged); if still too few, the portfolio is marked NOT PROPOSED (too concentrated).
  4. Weights: target = 50% equal weight + 50% inverse volatility, tilted ±30% by score; the final
     weights are the closest (least squares) to the target that satisfy sum = 1, min/max per
     position, max per sector AND max per region. Any constraint that cannot be met is relaxed by
     the smallest amount and reported against its configured value.
  5. Each position gets a role based on ABSOLUTE thresholds (volatility, beta, correlation,
     dividend coverage), and a written reason with the actual numbers.
Analytics use weekly returns in EUR (3 years, rows where all positions have data) with
Ledoit-Wolf shrinkage of the covariance. Historical figures apply TODAY's weights to the past: they
are descriptive, not a backtest (the names were selected knowing that past).
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from .classify import SECTOR_IT
from .scoring import C_NODATA, C_QDET, C_REDFLAG, C_TRAP, QUALITY_DISCOUNT
from .glossary import num_it, pct

MIN_WEEKS_RISK = 52        # weekly returns needed (last 3 years) to estimate a holding's risk
EXCLUDED_CLASSES = {C_REDFLAG, C_TRAP, C_NODATA, C_QDET}
PILLAR_SHORT = {"quality": "qualità", "valuation": "valutazione", "growth": "crescita", "financial_strength": "solidità"}


def ledoit_wolf(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf (2003, "Honey, I shrunk the sample covariance matrix") shrinkage toward the
    CONSTANT-CORRELATION target. For equities the identity target pulls every correlation toward 0 and
    understates portfolio risk; the constant-correlation target keeps the average co-movement.
    `returns` must have no NaN."""
    X = returns.values.astype(float)
    t, p = X.shape
    X = X - X.mean(axis=0)
    S = X.T @ X / t
    var = np.diag(S).copy()
    sd = np.sqrt(var)
    if p < 2 or (sd <= 0).any():
        return pd.DataFrame(S * t / max(t - 1, 1), index=returns.columns, columns=returns.columns)
    corr = S / np.outer(sd, sd)
    r_bar = (corr.sum() - p) / (p * (p - 1))
    F = r_bar * np.outer(sd, sd)
    np.fill_diagonal(F, var)
    # pi: sum of asymptotic variances of the sample covariances
    Y = X ** 2
    pi_mat = (Y.T @ Y) / t - S ** 2
    pi_hat = pi_mat.sum()
    # rho: asymptotic covariances between target and sample entries
    theta_ii_ij = ((X ** 3).T @ X) / t - var[:, None] * S          # θ_{ii,ij}
    theta_jj_ij = theta_ii_ij.T
    ratio = np.outer(1 / sd, sd)                                     # sqrt(s_jj / s_ii)
    off = r_bar / 2 * (ratio * theta_ii_ij + ratio.T * theta_jj_ij)
    np.fill_diagonal(off, 0.0)
    rho_hat = np.trace(pi_mat) + off.sum()
    gamma_hat = np.linalg.norm(F - S, "fro") ** 2
    delta = 1.0 if gamma_hat <= 0 else max(0.0, min(1.0, (pi_hat - rho_hat) / gamma_hat / t))
    cov = delta * F + (1 - delta) * S
    return pd.DataFrame(cov * t / max(t - 1, 1), index=returns.columns, columns=returns.columns)


def robust_cov(R: pd.DataFrame, min_rows: int = 52) -> tuple[pd.DataFrame, str]:
    """Annualized covariance without inventing zero returns: common window + Ledoit-Wolf, or (if the
    common window is too short) pairwise covariance; pairs that never overlap get the AVERAGE observed
    correlation (never 0, which would make them look like perfect diversifiers); then the nearest PSD matrix.
    Every column must have enough observations (see analyze_portfolio)."""
    common = R.dropna()
    if len(common) >= min_rows:
        return ledoit_wolf(common) * 52, f"Ledoit-Wolf (correlazione costante) su {len(common)} settimane comuni"
    C = R.cov(min_periods=26)
    sd = np.sqrt(np.diag(C.values))
    corr = C.values / np.outer(sd, sd)
    known = corr[~np.eye(len(sd), dtype=bool)]
    known = known[np.isfinite(known)]
    fill = float(known.mean()) if known.size else 0.3
    corr = np.where(np.isfinite(corr), corr, fill)
    np.fill_diagonal(corr, 1.0)
    C = corr * np.outer(sd, sd)
    vals, vecs = np.linalg.eigh((C + C.T) / 2)
    C = vecs @ np.diag(np.clip(vals, 1e-10, None)) @ vecs.T
    return (pd.DataFrame(C * 52, index=R.columns, columns=R.columns),
            f"covarianza a coppie (solo {len(common)} settimane in comune; coppie senza storico comune: correlazione "
            f"media {num_it(fill, 2)})")


def _eligible(scores: pd.DataFrame, weekly: pd.DataFrame, min_pct: float, min_weeks: int, cfg,
              log: list[str]) -> pd.DataFrame:
    s = scores[scores["robust_score"].notna()]
    elig = s[(s["robust_percentile"] >= min_pct) & (~s["classification"].isin(EXCLUDED_CLASSES))
             & (s["confidence"] != "bassa")]
    # the engine's own verdict: "costosa" with at least medium confidence is not proposed
    if "valuation_verdict" in elig.columns and "valuation_confidence" in elig.columns:
        dear = (elig["valuation_verdict"] == "costosa") & elig["valuation_confidence"].isin(["media", "alta"])
        if dear.any():
            log.append(f"Esclusi {int(dear.sum())} candidati con valutazione 'costosa' (confidenza media o alta)")
        elig = elig[~dear]
    # user-added tickers must still meet the universe liquidity/size thresholds to enter the PROPOSAL
    if "forced" in elig.columns:
        min_dv = float(cfg.get("universe.min_median_dollar_volume_usd", 2e6))
        liq = pd.to_numeric(elig.get("liquidity_usd"), errors="coerce")
        bad = (pd.to_numeric(elig["forced"], errors="coerce") == 1) & ~(liq >= min_dv)
        if bad.any():
            log.append(f"Esclusi {int(bad.sum())} titoli aggiunti a mano con liquidità sotto la soglia")
        elig = elig[~bad]
    if weekly.empty:
        return elig.iloc[0:0]
    last_date = weekly.index.max()
    R = weekly.iloc[-156:]
    ok = []
    for cid in elig["company_id"]:
        if cid not in R.columns:
            continue
        col = R[cid].dropna()
        if len(col) >= min_weeks and (last_date - col.index.max()).days <= 28:
            ok.append(cid)
    return elig[elig["company_id"].isin(ok)]


def _select(info: pd.DataFrame, corr: pd.DataFrame, target: int, max_corr: float, lam: float,
            sector_max_n: int, region_max_n: dict[str, int], held: set[str], bonus: float,
            rejected: dict[str, str]) -> list[str]:
    selected: list[str] = []
    sector_count: dict[str, int] = {}
    region_count: dict[str, int] = {}
    while len(selected) < target:
        best, best_adj = None, -1e9
        for cid, r in info.iterrows():
            if cid in selected:
                continue
            sec, reg = r["_sector"], r["_region"]
            if sector_count.get(sec, 0) >= sector_max_n:
                rejected[cid] = f"limite settore {SECTOR_IT.get(sec, sec)}"
                continue
            if region_count.get(reg, 0) >= region_max_n.get(reg, target):
                rejected[cid] = f"limite area {reg}"
                continue
            avg = 0.0
            if selected:
                cs = corr.loc[cid, selected]
                if cs.isna().any():
                    rejected[cid] = "correlazione non stimabile con i titoli già scelti (storico insufficiente)"
                    continue
                if cs.max() > max_corr:
                    rejected[cid] = f"correlazione {num_it(cs.max(), 2)} con {info.loc[cs.idxmax(), 'ticker']}"
                    continue
                avg = float(cs.mean())
            adj = float(r["robust_score"]) - lam * 100 * max(0.0, avg - 0.2) + (bonus if cid in held else 0.0)
            if adj > best_adj:
                best, best_adj = cid, adj
        if best is None:
            break
        selected.append(best)
        rejected.pop(best, None)
        sector_count[info.loc[best, "_sector"]] = sector_count.get(info.loc[best, "_sector"], 0) + 1
        region_count[info.loc[best, "_region"]] = region_count.get(info.loc[best, "_region"], 0) + 1
    return selected


def construct_portfolio(scores: pd.DataFrame, weekly: pd.DataFrame, cfg,
                        previous: dict[str, float] | None = None) -> dict[str, Any]:
    target = int(cfg.get("portfolio.target_positions", 20))
    min_pos = int(cfg.get("portfolio.min_positions", 12))
    max_w = float(cfg.get("portfolio.max_weight", 0.08))
    min_w = float(cfg.get("portfolio.min_weight", 0.025))
    max_sector = float(cfg.get("portfolio.max_sector_weight", 0.25))
    max_corr = float(cfg.get("portfolio.max_pair_correlation", 0.8))
    lam = float(cfg.get("portfolio.correlation_penalty", 0.5))
    min_pct0 = float(cfg.get("portfolio.min_robust_percentile", 70))
    min_weeks = int(cfg.get("portfolio.min_weeks_3y", 130))
    bonus = float(cfg.get("portfolio.holding_bonus", 3.0))
    band = float(cfg.get("portfolio.no_trade_band", 0.015))
    region_caps: dict[str, float] = dict(cfg.get("portfolio.max_region_weight", {}) or {})
    previous = previous or {}
    held = set(previous)

    log: list[str] = []
    rejected: dict[str, str] = {}
    selected: list[str] = []
    info = pd.DataFrame()
    corr = pd.DataFrame()
    R = pd.DataFrame()
    used_pct = min_pct0
    for min_pct in [min_pct0] + [p for p in (60.0, 50.0) if p < min_pct0]:
        sub_log: list[str] = []
        elig = _eligible(scores, weekly, min_pct, min_weeks, cfg, sub_log)
        info = elig.set_index("company_id")
        if info.empty:
            continue
        info["_sector"] = info["sector"].where(info["sector"].apply(lambda x: isinstance(x, str) and bool(x)), "Unknown")
        info["_region"] = info["region"].where(info["region"].apply(lambda x: isinstance(x, str) and bool(x)), "Altro")
        R = weekly[list(info.index)].iloc[-156:]
        corr = R.corr(min_periods=min(min_weeks, 104))
        n_target = min(target, len(info))
        # name caps derived from the WEIGHT caps: a group cannot hold more names than cap / min weight,
        # and (to keep the caps meaningful) not more than cap × target names
        sector_max_n = max(1, math.floor(max_sector * target + 1e-9))
        region_max_n = {reg: max(1, math.floor(region_caps.get(reg, 1.0) * target + 1e-9)) for reg in info["_region"].unique()}
        selected = _select(info, corr, n_target, max_corr, lam, sector_max_n, region_max_n, held, bonus, rejected)
        used_pct = min_pct
        log.extend(sub_log)
        log.append(f"Soglia percentile {num_it(min_pct, 0)}: {len(info)} candidati con storico prezzi sufficiente → {len(selected)} scelti")
        if len(selected) >= min_pos:
            break
    if used_pct < min_pct0:
        log.append(f"ATTENZIONE: soglia di percentile del punteggio robusto abbassata da {num_it(min_pct0, 0)} a {num_it(used_pct, 0)} "
                   "per raggiungere un numero minimo di titoli diversificati")
    if not selected:
        return {"positions": [], "status": "non proposto", "log": log + ["Nessun candidato idoneo."], "analytics": {}}
    status = "proposto"
    if len(selected) < min_pos:
        status = "non proposto"
        log.append(f"Solo {len(selected)} titoli rispettano i vincoli (minimo {min_pos}): portafoglio NON proposto, "
                   "mostrato solo come elenco di candidati. Allargare l'universo o rivedere i vincoli in config.toml.")

    # ---------------------------------------------------------------- weights
    Rs = R[selected]
    vol = Rs.std() * np.sqrt(52)
    inv = (1 / vol.replace(0, np.nan)).fillna(0)
    w = 0.5 / len(selected) + 0.5 * inv / inv.sum()
    sc = info.loc[selected, "robust_score"].astype(float)
    z = ((sc - sc.mean()) / (sc.std() if sc.std() > 0 else 1)).clip(-1, 1)
    w = w * (1 + 0.3 * z)
    w = w / w.sum()
    sectors = info.loc[selected, "_sector"]
    regions = info.loc[selected, "_region"]
    w, report = enforce_caps(w, sectors, regions, min_w, max_w, max_sector, region_caps, log)
    # no-trade band: small changes to existing holdings are not worth the costs → keep previous weight
    if previous:
        # names whose new weight is within the band KEEP their previous weight exactly (no tiny orders);
        # only the other names absorb the difference
        keep = {cid: float(previous[cid]) for cid in w.index if cid in previous and abs(w[cid] - previous[cid]) < band}
        if keep:
            log = [line for line in log if not line.startswith("VINCOLO NON RISPETTATO")]   # re-reported below
            w, report = enforce_caps(w, sectors, regions, min_w, max_w, max_sector, region_caps, log, fixed=keep)
    # caps broken by a wide margin (after the band step): a concentrated list, not a diversified proposal
    far = [c for c in report if not c["rispettato"] and c["vincolo"] != "peso minimo per titolo"
           and c["effettivo"] - c["configurato"] > (0.04 if c["vincolo"] == "peso massimo per titolo" else 0.10) + 1e-6]
    if status == "proposto" and far:
        status = "concentrato"
        log.append("Portafoglio CONCENTRATO: " + "; ".join(f"{c['vincolo']} {pct(c['effettivo'], 0)} (configurato "
                                                          f"{pct(c['configurato'], 0)})" for c in far)
                   + ". Non è una proposta diversificata: allargare l'universo (modalità standard/full).")
    log = list(dict.fromkeys(log))           # the same note can be produced at every percentile threshold
    turnover = None
    if previous:
        allk = set(previous) | set(w.index)
        turnover = 0.5 * sum(abs(float(w.get(k, 0.0)) - float(previous.get(k, 0.0))) for k in allk)
        log.append(f"Rotazione rispetto al portafoglio precedente: {pct(turnover, 0)} del capitale "
                   f"({len(set(w.index) - set(previous))} nuovi, {len(set(previous) - set(w.index))} usciti)")

    positions = []
    avg_corr = {c: float(corr.loc[c, [x for x in selected if x != c]].mean()) if len(selected) > 1 else 0.0 for c in selected}
    for cid in selected:
        r = info.loc[cid]
        role, why_role = assign_role(r, float(vol[cid]), avg_corr[cid])
        top_p = sorted([(p, r.get(p)) for p in ("quality", "valuation", "growth", "financial_strength")
                        if pd.notna(r.get(p))], key=lambda x: -x[1])[:2]
        reason = (f"{r.get('classification')}. Punti forti vs pari: " + ", ".join(f"{PILLAR_SHORT[p]} {num_it(v, 0)}/100" for p, v in top_p)
                  + f". Correlazione media con gli altri titoli {num_it(avg_corr[cid], 2)}, volatilità 3 anni {pct(vol[cid], 0)}.")
        if not bool(r.get("deep_analysis", False)):
            reason += " ⚠ Analisi del testo dei report annuali NON disponibile per questa società."
        positions.append({
            "company_id": cid, "ticker": r["ticker"], "name": r.get("name"), "weight": float(w[cid]),
            "previous_weight": float(previous[cid]) if cid in previous else None,
            "sector": r.get("sector"), "region": r.get("region"), "role": role, "role_reason": why_role,
            "reason": reason, "robust_score": float(r["robust_score"]), "classification": r.get("classification"),
            "valuation_verdict": r.get("valuation_verdict"), "valuation_confidence": r.get("valuation_confidence"),
            "vol": float(vol[cid]), "avg_corr": avg_corr[cid],
            "risk_note": _risk_note(r),
        })
    positions.sort(key=lambda p: -p["weight"])
    return {"positions": positions, "status": status, "log": log, "constraints": report, "turnover": turnover,
            "exited": sorted(set(previous) - set(w.index)), "min_percentile_used": used_pct,
            "rejected_examples": dict(list(rejected.items())[:30])}


def enforce_caps(w: pd.Series, sectors: pd.Series, regions: pd.Series, min_w: float, max_w: float, max_sector: float,
                 region_caps: dict[str, float], log: list[str] | None = None,
                 fixed: dict[str, float] | None = None) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Weights closest (least squares) to the target `w` subject to: sum = 1, min/max per position,
    max per sector, max per region.

    If the caps cannot all hold, a linear program with one slack variable per cap finds the SMALLEST
    relaxation (only the caps that are really binding are relaxed; raising one name's cap costs 3× a
    group cap, because concentration in a single company is the worse risk). Every relaxation is reported
    against the configured value. `fixed`: names whose weight must stay as given (no-trade band); ignored
    if that makes the problem infeasible."""
    from scipy.optimize import linprog, minimize

    names = list(w.index)
    n = len(names)
    lo = min(min_w, 1.0 / n)
    secs, regs = list(sectors.unique()), list(regions.unique())
    r_caps = {r: float(region_caps.get(r, 1.0)) for r in regs}
    S_m = np.array([(sectors == s_).values for s_ in secs], dtype=float)
    R_m = np.array([(regions == r_).values for r_ in regs], dtype=float)

    # ---- phase 1: minimal relaxation (variables: x[n], ONE shared per-name slack, sector slack[k], region slack[m]).
    # The per-name slack is shared: raising "max per name" applies to every name, so the weights can spread
    # evenly (8.33% each) instead of piling the whole relaxation on one arbitrary name.
    k, m = len(secs), len(regs)
    nv = n + 1 + k + m
    c = np.concatenate([np.zeros(n), [3.0 * n], np.ones(k), np.ones(m)])
    A, bnd = [], []
    for i in range(n):                                  # x_i − s ≤ max_w
        row = np.zeros(nv)
        row[i], row[n] = 1, -1
        A.append(row)
        bnd.append(max_w)
    for j in range(k):                                  # Σ_sector x − t_j ≤ max_sector
        row = np.zeros(nv)
        row[:n], row[n + 1 + j] = S_m[j], -1
        A.append(row)
        bnd.append(max_sector)
    for j, r_ in enumerate(regs):                       # Σ_region x − u_j ≤ cap
        row = np.zeros(nv)
        row[:n], row[n + 1 + k + j] = R_m[j], -1
        A.append(row)
        bnd.append(r_caps[r_])
    Aeq = np.zeros((1, nv))
    Aeq[0, :n] = 1
    bounds = [(lo, 1.0)] * n + [(0, 1.0)] * (1 + k + m)
    lp = linprog(c, A_ub=np.array(A), b_ub=np.array(bnd), A_eq=Aeq, b_eq=[1.0], bounds=bounds, method="highs")
    slack = lp.x[n:] if lp.success else np.zeros(1 + k + m)
    tol = 1e-7
    hi_i = np.full(n, max_w + (slack[0] if slack[0] > tol else 0.0))
    s_caps = max_sector + np.where(slack[1:1 + k] > tol, slack[1:1 + k], 0.0)
    rg_caps = np.array([r_caps[r_] for r_ in regs]) + np.where(slack[1 + k:] > tol, slack[1 + k:], 0.0)

    # ---- phase 2: weights closest to the target within the (minimally relaxed) caps
    w0 = w.values.astype(float)

    def solve(bnds):
        cons = [{"type": "eq", "fun": lambda x: x.sum() - 1.0}]
        for j in range(k):
            cons.append({"type": "ineq", "fun": lambda x, j=j: s_caps[j] + 1e-9 - S_m[j] @ x})
        for j in range(m):
            cons.append({"type": "ineq", "fun": lambda x, j=j: rg_caps[j] + 1e-9 - R_m[j] @ x})
        x0 = np.clip(w0, [b[0] for b in bnds], [b[1] for b in bnds])
        x0 = x0 / x0.sum()
        return minimize(lambda x: ((x - w0) ** 2).sum(), x0, method="SLSQP", bounds=bnds, constraints=cons,
                        options={"maxiter": 1000, "ftol": 1e-12})

    base_bounds = [(lo, max(lo, float(h))) for h in hi_i]
    res = None
    if fixed:
        fb = [((fixed[nm], fixed[nm]) if nm in fixed and b[0] <= fixed[nm] <= b[1] else b)
              for nm, b in zip(names, base_bounds)]
        if all(lo_ == hi_ for lo_, hi_ in fb):
            # every name is inside the band (e.g. nothing changed since the last run): no free variable, SLSQP
            # cannot solve it; the previous weights are kept as they are if they still respect the caps
            x_f = np.array([b[0] for b in fb])
            ok = (abs(x_f.sum() - 1.0) < 1e-6 and all(S_m[j] @ x_f <= s_caps[j] + 1e-6 for j in range(k))
                  and all(R_m[j] @ x_f <= rg_caps[j] + 1e-6 for j in range(m)))
            res = SimpleNamespace(success=ok, x=x_f, message="pesi precedenti")
        else:
            res = solve(fb)
        if not res.success:
            res = None
            if log is not None:
                log.append("Banda di non intervento non applicabile (i pesi precedenti non rispettano i vincoli): "
                           "pesi ricalcolati per tutti i titoli")
    if res is None:
        res = solve(base_bounds)
    if res.success:
        x = np.clip(res.x, 0, None)
    else:
        x = lp.x[:n] if lp.success else np.full(n, 1.0 / n)      # LP solution satisfies the relaxed caps
        if log is not None:
            log.append(f"Ottimizzatore dei pesi non convergente ({res.message}): uso i pesi della fase di "
                       "fattibilità, vedi verifica vincoli")
    x = x / x.sum()
    out = pd.Series(x, index=w.index)
    # ---- report every constraint against its CONFIGURED value
    report = []
    report.append({"vincolo": "peso massimo per titolo", "configurato": max_w, "effettivo": float(out.max()),
                   "rispettato": bool(out.max() <= max_w + 1e-6)})
    report.append({"vincolo": "peso minimo per titolo", "configurato": min_w, "effettivo": float(out.min()),
                   "rispettato": bool(out.min() >= min_w - 1e-6)})
    sec_w = out.groupby(sectors).sum()
    report.append({"vincolo": "peso massimo per settore", "configurato": max_sector, "effettivo": float(sec_w.max()),
                   "rispettato": bool(sec_w.max() <= max_sector + 1e-6), "dettaglio": SECTOR_IT.get(sec_w.idxmax(), sec_w.idxmax())})
    reg_w = out.groupby(regions).sum()
    for r_, v in reg_w.items():
        cap = float(region_caps.get(r_, 1.0))
        report.append({"vincolo": f"peso massimo area {r_}", "configurato": cap, "effettivo": float(v),
                       "rispettato": bool(v <= cap + 1e-6)})
    if log is not None:
        for c_ in report:
            if not c_["rispettato"]:
                log.append(f"VINCOLO NON RISPETTATO: {c_['vincolo']} = {pct(c_['effettivo'], 1)} (configurato "
                           f"{pct(c_['configurato'], 1)}) — troppo pochi candidati diversificati per rispettarlo")
    return out, report


def _enforce_caps(w, sectors, min_w, max_w, max_sector, log=None):
    """Backward-compatible wrapper (sector caps only)."""
    regions = pd.Series("Tutte", index=sectors.index)
    out, _ = enforce_caps(w, sectors, regions, min_w, max_w, max_sector, {}, log)
    return out


def assign_role(r: pd.Series, vol3y: float, avg_corr: float) -> tuple[str, str]:
    """Role from ABSOLUTE thresholds, with the actual numbers in the explanation."""
    q, g = r.get("quality"), r.get("growth")
    dy = r.get("dividend_yield")
    beta = r.get("beta_world")
    beta_ok = beta is not None and pd.notna(beta)
    flags = r.get("flag_codes") or set()
    if r.get("classification") in QUALITY_DISCOUNT:
        return ("Opportunità da verificare",
                "Qualità alta e multipli più bassi dei pari: può rivalutarsi SE i fondamentali tengono (non è garantito).")
    if pd.notna(q) and q >= 80 and (pd.isna(g) or g >= 50):
        return "Compounder di qualità", f"Qualità {num_it(q, 0)}/100 e crescita {num_it(g, 0)}/100 vs pari: motore di lungo periodo." if pd.notna(g) \
            else f"Qualità {num_it(q, 0)}/100 vs pari: motore di lungo periodo."
    if pd.notna(g) and g >= 80:
        return "Crescita", f"Crescita {num_it(g, 0)}/100 vs pari: aumenta il potenziale ma anche la volatilità ({pct(vol3y, 0)})."
    if vol3y < 0.20 and beta_ok and beta < 0.8:
        return "Difensivo", f"Volatilità 3 anni {pct(vol3y, 0)} e beta {num_it(beta, 2)}: tende ad attenuare le discese del mercato."
    pay, fpay = r.get("payout_ratio"), r.get("fcf_payout")
    covered = (pay is not None and pd.notna(pay) and pay < 0.8) and (fpay is None or pd.isna(fpay) or fpay < 1.0)
    if dy is not None and pd.notna(dy) and dy >= 0.03:
        if covered and "UNCOVERED_DIVIDEND" not in flags:
            return "Rendita", f"Dividendo {pct(dy, 1)}, pagato con il {pct(pay, 0)} degli utili: flussi di cassa regolari."
        return "Rendita (da verificare)", f"Dividendo {pct(dy, 1)}, ma copertura con utili/FCF non dimostrata."
    if avg_corr < 0.3:
        return "Diversificatore", f"Correlazione media {num_it(avg_corr, 2)} con gli altri titoli: si muove in modo diverso."
    return "Core", "Posizione equilibrata su più fattori."


def _risk_note(r: pd.Series) -> str:
    parts = []
    if pd.notna(r.get("vol_1y")):
        parts.append(f"volatilità 1 anno {pct(r['vol_1y'], 0)}")
    if pd.notna(r.get("max_drawdown_5y")):
        parts.append(f"perdita massima 5 anni {pct(r['max_drawdown_5y'], 0)}")
    if r.get("price_currency") and r.get("price_currency") != "EUR":
        parts.append(f"rischio cambio {r['price_currency']}/EUR")
    return ", ".join(parts)


def analyze_portfolio(weights: dict[str, float], weekly: pd.DataFrame, meta: pd.DataFrame,
                      bench_weekly: pd.Series | None) -> dict[str, Any]:
    # a holding needs at least a year of weekly returns in the 3-year window, up to date: otherwise its
    # risk cannot be estimated (with fewer points it used to look riskless)
    win = weekly.iloc[-156:] if len(weekly) else weekly
    last = win.index.max() if len(win) else None
    ids, short = [], []
    for c in weights:
        if c not in win.columns or not win[c].notna().any():
            continue
        col = win[c].dropna()
        if len(col) >= MIN_WEEKS_RISK and (last - col.index.max()).days <= 28:
            ids.append(c)
        else:
            short.append(c)
    missing = [c for c in weights if c not in ids]
    out: dict[str, Any] = {"missing_prices": missing, "short_history": short}
    if not ids:
        return out
    w = pd.Series({c: weights[c] for c in ids}, dtype=float)
    w = w / w.sum()
    R3 = weekly[ids].iloc[-156:].dropna(how="all")
    cov, cov_method = robust_cov(R3)
    out["cov_method"] = cov_method
    port_var = float(w @ cov @ w)
    vol_p = math.sqrt(port_var)
    indiv_vol = np.sqrt(np.diag(cov))
    rc = (w * (cov @ w)) / port_var
    out["volatility"] = vol_p
    out["diversification_ratio"] = float((w * indiv_vol).sum() / vol_p)
    out["effective_n"] = float(1 / (w ** 2).sum())
    out["risk_contribution"] = rc.sort_values(ascending=False).to_dict()
    corr = R3.corr(min_periods=52)
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            c = corr.loc[a, b]
            if pd.notna(c):
                pairs.append((a, b, float(c)))
    pairs.sort(key=lambda x: -x[2])
    out["top_correlated_pairs"] = pairs[:8]
    out["avg_pair_correlation"] = float(np.mean([p[2] for p in pairs])) if pairs else None
    out["correlation_matrix"] = corr

    # historical behaviour of CURRENT weights (descriptive, not a strategy backtest!)
    R5 = weekly[ids].iloc[-260:]
    avail = R5.notna()
    wr = R5.fillna(0.0).mul(w, axis=1).sum(axis=1) / avail.mul(w, axis=1).sum(axis=1).replace(0, np.nan)
    wr = wr.dropna()
    # keep only weeks where at least 80% of the weight has data
    cover = avail.mul(w, axis=1).sum(axis=1).reindex(wr.index)
    wr = wr[cover >= 0.8]
    if len(wr) > 52:
        if bench_weekly is not None:
            b = bench_weekly.reindex(wr.index)
            j = pd.concat([wr, b], axis=1, join="inner").dropna()
        else:
            j = None
        # portfolio and benchmark on the SAME window when a benchmark exists
        base = j.iloc[:, 0] if j is not None and len(j) > 52 else wr
        cum = (1 + base).cumprod()
        years = len(base) / 52
        out["hist_window"] = [str(base.index[0].date()), str(base.index[-1].date())]
        out["hist_return_ann"] = float(cum.iloc[-1] ** (1 / years) - 1)
        out["hist_vol"] = float(base.std() * np.sqrt(52))
        out["hist_max_drawdown"] = float((cum / cum.cummax() - 1).min())
        out["hist_cum"] = cum
        if j is not None and len(j) > 52:
            cb = (1 + j.iloc[:, 1]).cumprod()
            out["bench_return_ann"] = float(cb.iloc[-1] ** (52 / len(j)) - 1)
            out["bench_vol"] = float(j.iloc[:, 1].std() * np.sqrt(52))
            out["bench_max_drawdown"] = float((cb / cb.cummax() - 1).min())
            out["bench_cum"] = cb
            covm = np.cov(j.iloc[:, 0], j.iloc[:, 1])
            out["beta"] = float(covm[0, 1] / covm[1, 1])
            roll_b = (1 + j.iloc[:, 1]).rolling(13).apply(np.prod, raw=True) - 1
            end = roll_b.idxmin()
            if pd.notna(end):
                win = j.loc[:end].iloc[-13:]
                out["stress_window"] = {"end": str(end.date()), "start": str(win.index[0].date()),
                                        "benchmark": float((1 + win.iloc[:, 1]).prod() - 1),
                                        "portfolio": float((1 + win.iloc[:, 0]).prod() - 1)}
    m = meta.set_index("company_id").reindex(ids)
    for col, key in (("sector", "sector_exposure"), ("region", "region_exposure"), ("price_currency", "currency_exposure")):
        if col in m.columns:
            out[key] = w.groupby(m[col].fillna("n/d")).sum().sort_values(ascending=False).to_dict()
    tilts = {}
    for col, lab in (("quality", "Qualità"), ("valuation", "Valore"), ("growth", "Crescita"),
                     ("financial_strength", "Solidità"), ("momentum_pct", "Momentum")):
        if col in m.columns:
            v = pd.to_numeric(m[col], errors="coerce")
            ok = v.notna()
            if ok.any():
                tilts[lab] = float((v[ok] * w[ok]).sum() / w[ok].sum())
    out["factor_tilts"] = tilts
    if "market_cap_eur" in m.columns:
        mc = pd.to_numeric(m["market_cap_eur"], errors="coerce")
        ok = mc.notna() & (mc > 0)
        if ok.any():
            out["weighted_geo_mean_mcap_eur"] = float(np.exp((np.log(mc[ok]) * w[ok]).sum() / w[ok].sum()))
    warnings = []
    top = w.sort_values(ascending=False)
    if top.iloc[0] > 0.15:
        warnings.append(f"Posizione più grande = {pct(top.iloc[0], 0)}: rischio specifico concentrato")
    for sec, x in out.get("sector_exposure", {}).items():
        if x > 0.35:
            warnings.append(f"Settore {SECTOR_IT.get(sec, sec)} = {pct(x, 0)} del portafoglio")
    for cur, x in out.get("currency_exposure", {}).items():
        if cur != "EUR" and x > 0.6:
            warnings.append(f"{pct(x, 0)} del portafoglio in {cur}: forte esposizione al cambio contro EUR")
    if out.get("avg_pair_correlation") and out["avg_pair_correlation"] > 0.5:
        warnings.append(f"Correlazione media tra titoli {num_it(out['avg_pair_correlation'], 2)}: diversificazione limitata")
    if len(ids) < 12:
        warnings.append(f"Solo {len(ids)} titoli: il rischio specifico di ogni azienda pesa molto")
    if missing:
        warnings.append(f"{len(missing)} titoli esclusi dall'analisi di rischio (prezzi assenti, meno di {MIN_WEEKS_RISK} "
                        "settimane negli ultimi 3 anni o non aggiornati): i pesi degli altri sono stati riproporzionati")
    out["warnings"] = warnings
    return out


def serializable(an: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly copy of analyze_portfolio's output (DataFrame/Series → dict of lists)."""
    out = {}
    for k, v in an.items():
        if isinstance(v, pd.DataFrame):
            out[k] = {"index": list(v.index), "columns": list(v.columns), "values": np.round(v.values.astype(float), 4).tolist()}
        elif isinstance(v, pd.Series):
            out[k] = {"index": [str(i.date()) if hasattr(i, "date") else str(i) for i in v.index], "values": [float(x) for x in v.values]}
        else:
            out[k] = v
    return out
