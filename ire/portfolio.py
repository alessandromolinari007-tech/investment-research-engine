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
from typing import Any

import numpy as np
import pandas as pd

from .classify import SECTOR_IT
from .scoring import C_NODATA, C_QDET, C_REDFLAG, C_TRAP, QUALITY_DISCOUNT

EXCLUDED_CLASSES = {C_REDFLAG, C_TRAP, C_NODATA, C_QDET}
PILLAR_SHORT = {"quality": "qualità", "valuation": "valutazione", "growth": "crescita", "financial_strength": "solidità"}


def ledoit_wolf(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf (2004) shrinkage toward scaled identity. `returns` must have no NaN."""
    X = returns.values
    X = X - X.mean(axis=0)
    n, p = X.shape
    S = X.T @ X / n
    mu = np.trace(S) / p
    F = mu * np.eye(p)
    d2 = np.linalg.norm(S - F, "fro") ** 2
    b_bar = 0.0
    for k in range(n):
        xk = X[k][:, None]
        b_bar += np.linalg.norm(xk @ xk.T - S, "fro") ** 2
    b_bar /= n ** 2
    b2 = min(b_bar, d2)
    delta = b2 / d2 if d2 > 0 else 1.0
    cov = delta * F + (1 - delta) * S
    return pd.DataFrame(cov * n / max(n - 1, 1), index=returns.columns, columns=returns.columns)


def robust_cov(R: pd.DataFrame, min_rows: int = 52) -> tuple[pd.DataFrame, str]:
    """Annualized covariance without inventing zero returns: common window + Ledoit-Wolf, or (if the
    common window is too short) pairwise covariance projected to the nearest PSD matrix."""
    common = R.dropna()
    if len(common) >= min_rows:
        return ledoit_wolf(common) * 52, f"Ledoit-Wolf su {len(common)} settimane comuni"
    C = R.cov(min_periods=26).values
    C = np.nan_to_num(C, nan=0.0)
    vals, vecs = np.linalg.eigh((C + C.T) / 2)
    C = vecs @ np.diag(np.clip(vals, 1e-10, None)) @ vecs.T
    return pd.DataFrame(C * 52, index=R.columns, columns=R.columns), "covarianza a coppie (storico comune breve)"


def _eligible(scores: pd.DataFrame, weekly: pd.DataFrame, min_pct: float, min_weeks: int, cfg,
              log: list[str]) -> pd.DataFrame:
    s = scores[scores["robust_score"].notna()]
    elig = s[(s["robust_percentile"] >= min_pct) & (~s["classification"].isin(EXCLUDED_CLASSES))
             & (s["confidence"] != "bassa")]
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
                    rejected[cid] = f"correlazione {cs.max():.2f} con {info.loc[cs.idxmax(), 'ticker']}"
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
        log.append(f"Soglia percentile {min_pct:.0f}: {len(info)} candidati con storico prezzi sufficiente → {len(selected)} scelti")
        if len(selected) >= min_pos:
            break
    if used_pct < min_pct0:
        log.append(f"ATTENZIONE: soglia di qualità abbassata da {min_pct0:.0f} a {used_pct:.0f} per raggiungere un numero "
                   "minimo di titoli diversificati")
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
        tgt = w.copy()
        for cid in tgt.index:
            if cid in previous and abs(tgt[cid] - previous[cid]) < band:
                tgt[cid] = previous[cid]
        if not np.allclose(tgt.values, w.values):
            w, report = enforce_caps(tgt / tgt.sum(), sectors, regions, min_w, max_w, max_sector, region_caps, [])
    turnover = None
    if previous:
        allk = set(previous) | set(w.index)
        turnover = 0.5 * sum(abs(float(w.get(k, 0.0)) - float(previous.get(k, 0.0))) for k in allk)
        log.append(f"Rotazione rispetto al portafoglio precedente: {turnover:.0%} del capitale "
                   f"({len(set(w.index) - set(previous))} nuovi, {len(set(previous) - set(w.index))} usciti)")

    positions = []
    avg_corr = {c: float(corr.loc[c, [x for x in selected if x != c]].mean()) if len(selected) > 1 else 0.0 for c in selected}
    for cid in selected:
        r = info.loc[cid]
        role, why_role = assign_role(r, float(vol[cid]), avg_corr[cid])
        top_p = sorted([(p, r.get(p)) for p in ("quality", "valuation", "growth", "financial_strength")
                        if pd.notna(r.get(p))], key=lambda x: -x[1])[:2]
        reason = (f"{r.get('classification')}. Punti forti vs pari: " + ", ".join(f"{PILLAR_SHORT[p]} {v:.0f}/100" for p, v in top_p)
                  + f". Correlazione media con gli altri titoli {avg_corr[cid]:.2f}, volatilità 3 anni {vol[cid]:.0%}.")
        if not bool(r.get("deep_analysis", False)):
            reason += " ⚠ Analisi del testo dei report annuali NON disponibile per questa società."
        positions.append({
            "company_id": cid, "ticker": r["ticker"], "name": r.get("name"), "weight": float(w[cid]),
            "previous_weight": float(previous[cid]) if cid in previous else None,
            "sector": r.get("sector"), "region": r.get("region"), "role": role, "role_reason": why_role,
            "reason": reason, "robust_score": float(r["robust_score"]), "classification": r.get("classification"),
            "vol": float(vol[cid]), "avg_corr": avg_corr[cid],
            "risk_note": _risk_note(r),
        })
    positions.sort(key=lambda p: -p["weight"])
    return {"positions": positions, "status": status, "log": log, "constraints": report, "turnover": turnover,
            "exited": sorted(set(previous) - set(w.index)), "min_percentile_used": used_pct,
            "rejected_examples": dict(list(rejected.items())[:30])}


def enforce_caps(w: pd.Series, sectors: pd.Series, regions: pd.Series, min_w: float, max_w: float, max_sector: float,
                 region_caps: dict[str, float], log: list[str] | None = None) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Weights closest (least squares) to the target `w` subject to: sum = 1, min/max per position,
    max per sector, max per region. If infeasible, the per-position max is relaxed first (up to 12%),
    then the group caps; every relaxation is reported against the configured value."""
    from scipy.optimize import minimize

    n = len(w)
    lo = min(min_w, 1.0 / n)
    hi = max_w
    s_cap = max_sector
    r_caps = {r: float(region_caps.get(r, 1.0)) for r in regions.unique()}
    s_counts, r_counts = sectors.value_counts(), regions.value_counts()

    def feasible():
        if n * hi < 1 - 1e-9:
            return False
        if sum(min(s_cap, s_counts[s] * hi) for s in s_counts.index) < 1 - 1e-9:
            return False
        if sum(min(r_caps[r], r_counts[r] * hi) for r in r_counts.index) < 1 - 1e-9:
            return False
        return True

    steps = 0
    while not feasible() and steps < 2000:
        steps += 1
        if hi < 0.12:
            hi += 0.0025
            continue
        # relax the tightest group caps a little at a time
        s_cap = min(1.0, s_cap + 0.01)
        for r in r_caps:
            r_caps[r] = min(1.0, r_caps[r] + 0.01)
        if s_cap >= 1.0 and all(v >= 1.0 for v in r_caps.values()):
            hi = min(1.0, hi + 0.01)
    w0 = w.values.astype(float)
    cons = [{"type": "eq", "fun": lambda x: x.sum() - 1.0}]
    for s_ in s_counts.index:
        m = (sectors == s_).values.astype(float)
        cons.append({"type": "ineq", "fun": lambda x, m=m: s_cap - (x * m).sum()})
    for r_ in r_counts.index:
        m = (regions == r_).values.astype(float)
        cons.append({"type": "ineq", "fun": lambda x, m=m, c=r_caps[r_]: c - (x * m).sum()})
    x0 = np.clip(w0, lo, hi)
    x0 = x0 / x0.sum()
    res = minimize(lambda x: ((x - w0) ** 2).sum(), x0, method="SLSQP", bounds=[(lo, hi)] * n, constraints=cons,
                   options={"maxiter": 1000, "ftol": 1e-12})
    x = np.clip(res.x, 0, None) if res.success else x0
    if not res.success and log is not None:
        log.append(f"Ottimizzatore dei pesi non convergente ({res.message}): uso pesi approssimati, vedi verifica vincoli")
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
        for c in report:
            if not c["rispettato"]:
                log.append(f"VINCOLO NON RISPETTATO: {c['vincolo']} = {c['effettivo']:.1%} (configurato {c['configurato']:.1%}) "
                           "— troppi pochi candidati diversificati per rispettarlo")
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
        return "Compounder di qualità", f"Qualità {q:.0f}/100 e crescita {g:.0f}/100 vs pari: motore di lungo periodo." if pd.notna(g) \
            else f"Qualità {q:.0f}/100 vs pari: motore di lungo periodo."
    if pd.notna(g) and g >= 80:
        return "Crescita", f"Crescita {g:.0f}/100 vs pari: aumenta il potenziale ma anche la volatilità ({vol3y:.0%})."
    if vol3y < 0.20 and beta_ok and beta < 0.8:
        return "Difensivo", f"Volatilità 3 anni {vol3y:.0%} e beta {beta:.2f}: tende ad attenuare le discese del mercato."
    pay, fpay = r.get("payout_ratio"), r.get("fcf_payout")
    covered = (pay is not None and pd.notna(pay) and pay < 0.8) and (fpay is None or pd.isna(fpay) or fpay < 1.0)
    if dy is not None and pd.notna(dy) and dy >= 0.03:
        if covered and "UNCOVERED_DIVIDEND" not in flags:
            return "Rendita", f"Dividendo {dy:.1%}, pagato con il {pay:.0%} degli utili: flussi di cassa regolari."
        return "Rendita (da verificare)", f"Dividendo {dy:.1%}, ma copertura con utili/FCF non dimostrata."
    if avg_corr < 0.3:
        return "Diversificatore", f"Correlazione media {avg_corr:.2f} con gli altri titoli: si muove in modo diverso."
    return "Core", "Posizione equilibrata su più fattori."


def _risk_note(r: pd.Series) -> str:
    parts = []
    if pd.notna(r.get("vol_1y")):
        parts.append(f"volatilità 1 anno {r['vol_1y']:.0%}")
    if pd.notna(r.get("max_drawdown_5y")):
        parts.append(f"perdita massima 5 anni {r['max_drawdown_5y']:.0%}")
    if r.get("price_currency") and r.get("price_currency") != "EUR":
        parts.append(f"rischio cambio {r['price_currency']}/EUR")
    return ", ".join(parts)


def analyze_portfolio(weights: dict[str, float], weekly: pd.DataFrame, meta: pd.DataFrame,
                      bench_weekly: pd.Series | None) -> dict[str, Any]:
    ids = [c for c in weights if c in weekly.columns and weekly[c].notna().any()]
    missing = [c for c in weights if c not in ids]
    out: dict[str, Any] = {"missing_prices": missing}
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
        warnings.append(f"Posizione più grande = {top.iloc[0]:.0%}: rischio specifico concentrato")
    for sec, x in out.get("sector_exposure", {}).items():
        if x > 0.35:
            warnings.append(f"Settore {SECTOR_IT.get(sec, sec)} = {x:.0%} del portafoglio")
    for cur, x in out.get("currency_exposure", {}).items():
        if cur != "EUR" and x > 0.6:
            warnings.append(f"{x:.0%} del portafoglio in {cur}: forte esposizione al cambio contro EUR")
    if out.get("avg_pair_correlation") and out["avg_pair_correlation"] > 0.5:
        warnings.append(f"Correlazione media tra titoli {out['avg_pair_correlation']:.2f}: diversificazione limitata")
    if len(ids) < 12:
        warnings.append(f"Solo {len(ids)} titoli: il rischio specifico di ogni azienda pesa molto")
    if missing:
        warnings.append(f"{len(missing)} titoli senza prezzi utilizzabili esclusi dall'analisi di rischio")
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
