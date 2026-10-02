"""Historical test of the scoring method ("would it have worked?").

What it does
------------
For a series of past dates (every 6 months) it re-runs the REAL analysis and scoring code on the information
that was public on that day, then looks at what the shares did afterwards (6 and 12 months, total return in EUR):

* financial statements: every SEC XBRL fact filed AFTER the date is discarded (`truncate_companyfacts`), so a
  restated value or a later filing cannot leak into the past; the first value filed is the one used;
* prices, volatility, momentum, risk-free rate, "age of the data": all cut at the date (`clock.as_of`);
* the same cross-sectional scoring (sector-relative percentiles, 5 weight schemes, robust score) is applied
  to the companies of that date only.

Then it checks whether higher scores were followed by higher returns (rank correlation, quintiles, the top 20
against 500 random portfolios of 20 drawn from the same companies, and against the MSCI World ETF).

Limits that CANNOT be removed with free data (always shown next to the result)
-----------------------------------------------------------------------------
* survivorship: the companies are those that are in the database today; shares that were delisted, went
  bankrupt or were acquired since are missing (free sources keep no prices for them);
* size selection: the companies in the database passed today's size filter;
* only US domestic SEC filers reporting in USD (cleanest point-in-time data: filing dates);
* not used: analyst estimates, Yahoo statements, text of the filings, 8-K events (no point-in-time copy);
* sector and industry labels are today's.
Both survivorship and size selection flatter the absolute results; the comparison between high and low scores
within the SAME set of survivors is less affected, but not immune. A positive result is therefore a necessary
condition, not a proof.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import clock, scoring
from .analysis import analyze_company
from .config import load_config
from .db import init_db, now_iso, upsert
from .normalize.sec_facts import normalize_companyfacts
from .prices import PriceStore, main_unit
from .risk import BENCHMARKS
from .sources import sec, yahoo
from .sources.fx_macro import RATE_SERIES, FxTable

HORIZONS = (6, 12)                 # months
TOP_N = 20
N_RANDOM = 500
MIN_PRICE_HISTORY = 250            # trading days needed before the date (risk metrics, momentum)
MIN_UNIVERSE = 60                  # fewer companies with a score on a date: the date is skipped
CAVEATS = [
    "Sopravvivenza: restano solo le società ancora quotate oggi. Quelle fallite, acquisite o tolte dalla borsa "
    "mancano (le fonti gratuite non conservano i loro prezzi). Questo rende i risultati assoluti più belli del vero.",
    "Selezione per dimensione: le società analizzate hanno superato il filtro di capitalizzazione di oggi.",
    "Solo società USA che depositano alla SEC in dollari: sono i dati storici più puliti, ma non rappresentano "
    "l'intero universo (Europa, Asia).",
    "Non usati perché non esistono copie storiche gratuite: stime degli analisti, bilanci Yahoo, testo dei report, "
    "eventi 8-K. Il sistema vero usa anche questi: il test ne misura solo una parte.",
    "Settore e industria sono quelli di oggi.",
]


# ---------------------------------------------------------------------------------------------------------------
# point-in-time helpers
# ---------------------------------------------------------------------------------------------------------------
def truncate_companyfacts(cf: dict[str, Any], asof) -> dict[str, Any]:
    """Copy of a SEC `companyfacts` payload with every fact filed after `asof` removed.

    A fact without a filing date is dropped (cannot be proven to be public)."""
    cutoff = str(pd.Timestamp(asof).date())
    facts: dict[str, Any] = {}
    for tax, concepts in cf.get("facts", {}).items():
        kept_concepts: dict[str, Any] = {}
        for concept, node in concepts.items():
            units = {}
            for unit, arr in node.get("units", {}).items():
                kept = [e for e in arr if (e.get("filed") or "9999") <= cutoff]
                if kept:
                    units[unit] = kept
            if units:
                kept_concepts[concept] = {**{k: v for k, v in node.items() if k != "units"}, "units": units}
        if kept_concepts:
            facts[tax] = kept_concepts
    return {**{k: v for k, v in cf.items() if k != "facts"}, "facts": facts}


def rebalance_dates(start, last, step_months: int = 6) -> list[pd.Timestamp]:
    out, k = [], 0
    s = pd.Timestamp(start).normalize()
    while True:
        d = s + pd.DateOffset(months=k * step_months)
        if d > last:
            return out
        out.append(d)
        k += 1


def forward_return(eur: pd.Series, asof, months: int) -> float | None:
    """Total return in EUR from the first close AFTER `asof` to the last close within `months` months of it.
    None if the history does not reach the end of the window (delisted, or window not over yet)."""
    s = eur.dropna()
    after = s[s.index > pd.Timestamp(asof)]
    if after.empty or (after.index[0] - pd.Timestamp(asof)).days > 7:
        return None
    entry_date = after.index[0]
    end = entry_date + pd.DateOffset(months=months)
    if s.index[-1] < end - pd.Timedelta(days=7):
        return None
    exit_ = s[s.index <= end]
    p0, p1 = float(after.iloc[0]), float(exit_.iloc[-1])
    return p1 / p0 - 1 if p0 > 0 and p1 > 0 else None


# ---------------------------------------------------------------------------------------------------------------
# statistics (pure functions on the observations: unit-tested without any data source)
# ---------------------------------------------------------------------------------------------------------------
def _non_overlapping(dates: list[pd.Timestamp], months: int) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    for d in sorted(dates):
        if not out or d >= out[-1] + pd.DateOffset(months=months):
            out.append(d)
    return out


def _t_stat(x: list[float]) -> float | None:
    x = [v for v in x if v is not None and not math.isnan(v)]
    if len(x) < 4:
        return None
    sd = float(np.std(x, ddof=1))
    return float(np.mean(x) / (sd / math.sqrt(len(x)))) if sd > 0 else None


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), nd)


def evaluate(obs: pd.DataFrame, bench: dict[str, dict[int, float | None]], horizons=HORIZONS, top_n: int = TOP_N,
             n_random: int = N_RANDOM, seed: int = 0) -> dict[str, Any]:
    """obs: one row per (asof, company) with robust_score, classification, fwd_<h>m (EUR total return).
    bench: {asof ISO date: {months: benchmark return}}."""
    rng = np.random.default_rng(seed)
    out: dict[str, Any] = {}
    for h in horizons:
        col = f"fwd_{h}m"
        if col not in obs.columns:
            continue
        per_date = []
        pooled = []
        for d, g in obs.groupby("asof"):
            g = g.dropna(subset=["robust_score", col])
            if len(g) < MIN_UNIVERSE:
                continue
            fwd = g[col].to_numpy(float)
            uni = float(fwd.mean())
            ic = g["robust_score"].corr(g[col], method="spearman")
            rank = g["robust_score"].rank(method="first", ascending=False)
            quint = pd.qcut(rank, 5, labels=False)                 # 0 = highest scores
            qmeans = [float(fwd[(quint == q).to_numpy()].mean()) for q in range(5)]
            order = np.argsort(-g["robust_score"].to_numpy(float), kind="stable")
            top_mean = float(fwd[order[:top_n]].mean())
            r = rng.random((n_random, len(fwd)))
            idx = np.argpartition(r, top_n, axis=1)[:, :top_n]
            rand_means = fwd[idx].mean(axis=1)
            b = (bench.get(str(pd.Timestamp(d).date())) or {}).get(h)
            per_date.append({"asof": pd.Timestamp(d), "n": len(g), "ic": float(ic) if pd.notna(ic) else None,
                             "uni": uni, "q": qmeans, "top": top_mean, "rand_pct": float((rand_means < top_mean).mean()),
                             "bench": b})
            gg = g.assign(excess=g[col] - uni)
            pooled.append(gg[["classification", "excess"]])
        if not per_date:
            out[str(h)] = {"n_dates": 0}
            continue
        dates = [p["asof"] for p in per_date]
        nov = set(_non_overlapping(dates, h))
        ics = [p["ic"] for p in per_date if p["ic"] is not None]
        ics_nov = [p["ic"] for p in per_date if p["asof"] in nov and p["ic"] is not None]
        q_excess = np.mean([[qm - p["uni"] for qm in p["q"]] for p in per_date], axis=0)
        top_ex = [p["top"] - p["uni"] for p in per_date]
        bench_ex = [p["top"] - p["bench"] for p in per_date if p["bench"] is not None]
        uni_vs_bench = [p["uni"] - p["bench"] for p in per_date if p["bench"] is not None]
        cls = pd.concat(pooled)
        cls_tab = (cls.groupby("classification")["excess"].agg(["count", "mean", "median"]).reset_index()
                   .sort_values("mean", ascending=False))
        out[str(h)] = {
            "n_dates": len(per_date),
            "n_dates_non_overlapping": len(nov),
            "first_date": str(min(dates).date()), "last_date": str(max(dates).date()),
            "avg_universe": _r(np.mean([p["n"] for p in per_date]), 0),
            "ic_mean": _r(np.mean(ics)), "ic_t_non_overlapping": _r(_t_stat(ics_nov), 2),
            "ic_positive_share": _r(np.mean([i > 0 for i in ics]), 3),
            "quintile_excess": [_r(x) for x in q_excess],            # Q1 (best scores) … Q5, vs the universe average
            "q1_minus_q5": _r(q_excess[0] - q_excess[4]),
            "top_n": top_n,
            "top_excess_vs_universe": _r(np.mean(top_ex)),
            "top_beats_universe_share": _r(np.mean([x > 0 for x in top_ex]), 3),
            "top_total": _r(np.mean([p["top"] for p in per_date])),
            "universe_total": _r(np.mean([p["uni"] for p in per_date])),
            "bench_total": _r(np.mean([p["bench"] for p in per_date if p["bench"] is not None])) if bench_ex else None,
            "top_excess_vs_bench": _r(np.mean(bench_ex)) if bench_ex else None,
            "top_beats_bench_share": _r(np.mean([x > 0 for x in bench_ex]), 3) if bench_ex else None,
            "universe_excess_vs_bench": _r(np.mean(uni_vs_bench)) if uni_vs_bench else None,
            "random_percentile_mean": _r(np.mean([p["rand_pct"] for p in per_date]), 3),
            "classes": [{"classification": r["classification"], "n": int(r["count"]), "mean_excess": _r(r["mean"]),
                         "median_excess": _r(r["median"])} for _, r in cls_tab.iterrows()],
        }
    return out


def reading(stats_h: dict[str, Any]) -> tuple[str, str]:
    """(level, plain-Italian reading) for one horizon. Deliberately conservative."""
    if not stats_h or not stats_h.get("n_dates"):
        return "n/d", "Dati insufficienti per una conclusione."
    ic, t = stats_h.get("ic_mean"), stats_h.get("ic_t_non_overlapping")
    spread, pct = stats_h.get("q1_minus_q5"), stats_h.get("random_percentile_mean")
    if ic is None or spread is None:
        return "n/d", "Dati insufficienti per una conclusione."
    if ic > 0.02 and spread > 0 and (t is not None and t >= 2) and (pct is not None and pct >= 0.6):
        return "favorevole", ("Nei dati storici i punteggi più alti sono stati seguiti, in media, da rendimenti migliori, "
                              "in modo abbastanza costante. È un risultato incoraggiante, non una prova: sopravvivenza "
                              "e selezione per dimensione lo rendono più bello del vero.")
    if ic < -0.02 and t is not None and t <= -2:
        return "contrario", ("Nei dati storici i punteggi più alti sono stati seguiti da rendimenti peggiori: "
                             "il metodo, così com'è, non va usato per scegliere le azioni.")
    return "non dimostrato", ("Nei dati storici non emerge un vantaggio chiaro dei punteggi alti rispetto agli altri: "
                              "la differenza è piccola o incostante e potrebbe essere solo fortuna. Finché non c'è "
                              "evidenza migliore, il sito va usato per studiare le società, non per batterne il mercato.")


# ---------------------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------------------
def _rates_at(macro: dict[str, pd.Series], asof) -> dict[str, tuple[float, str]]:
    out = {}
    for cur, series in RATE_SERIES.items():
        s = macro.get(series)
        if s is None:
            continue
        s = s[s.index <= asof]
        if len(s) and (asof - s.index[-1]).days <= 120:
            out[cur] = (float(s.iloc[-1]) / 100, f"FRED {series} al {s.index[-1].date()}")
    return out


def _load_benchmark(prices: PriceStore, fx: FxTable) -> pd.Series | None:
    for t, _name in BENCHMARKS:
        df = prices._read([t], "2013-01-01").get(t)
        if df is None or df.empty:
            continue
        info = yahoo.info(t) or {}
        cur, div = yahoo.normalize_currency(info.get("currency") or ("EUR" if t.endswith((".MI", ".AS")) else "USD"))
        s = fx.series_to_eur(df["adj_close"] / div, cur)
        if s is not None and s.dropna().size > 500:
            return s.dropna()
    return None


def run_backtest(progress: Callable[[str], None] = print, start: str = "2017-06-30", step_months: int = 6,
                 limit: int | None = None, min_cap_usd: float | None = None, workers: int = 3) -> int:
    """Runs the historical test on the companies of the database; stores and returns the backtest id."""
    import time

    t0 = time.time()

    def say(msg: str) -> None:
        progress(f"[{(time.time() - t0) / 60:5.1f} min] {msg}")

    cfg = load_config()
    con = init_db()
    last_run = con.execute("SELECT run_id, mode FROM runs WHERE status IN ('completed','degraded') "
                           "ORDER BY run_id DESC LIMIT 1").fetchone()
    if last_run is None:
        raise RuntimeError("Nessuna analisi completata nel database: esegui prima run_quick_test.bat o run_pipeline.bat.")
    min_cap = float(min_cap_usd if min_cap_usd is not None else cfg.min_market_cap_usd(last_run["mode"]))
    comp = [dict(r) for r in con.execute(
        "SELECT * FROM companies WHERE in_universe=1 AND cik IS NOT NULL AND filer_type='domestic' AND data_tier='A' "
        "AND fin_currency='USD' AND price_currency='USD' ORDER BY ticker")]
    if limit:
        comp = comp[:limit]
    if len(comp) < MIN_UNIVERSE:
        raise RuntimeError(f"Solo {len(comp)} società USA con bilanci SEC nel database: ne servono almeno {MIN_UNIVERSE}.")
    say(f"Test storico su {len(comp)} società USA (bilanci SEC), capitalizzazione minima {min_cap / 1e9:.0f} mld $ alla data")

    fx = FxTable(pd.read_sql_query("SELECT date, currency, per_eur, source FROM fx", con))
    store = PriceStore(con, progress=lambda m: None)
    bench = _load_benchmark(store, fx)
    if bench is None:
        say("ATTENZIONE: benchmark MSCI World non disponibile, il confronto con l'ETF sarà omesso")
    macro: dict[str, pd.Series] = {}
    for r in con.execute("SELECT series, date, value FROM macro"):
        macro.setdefault(r["series"], []).append((r["date"], r["value"]))
    macro = {k: pd.Series([v for _, v in sorted(rows)], index=pd.to_datetime([d for d, _ in sorted(rows)])) for k, rows in macro.items()}

    px_all = store._read([c["ticker"] for c in comp], "2013-01-01")
    latest_price = max((df.index.max() for df in px_all.values()), default=None)
    if latest_price is None:
        raise RuntimeError("Nessun prezzo nel database.")
    dates = rebalance_dates(start, latest_price - pd.DateOffset(months=min(HORIZONS)), step_months)
    if not dates:
        raise RuntimeError("Nessuna data di verifica: storico prezzi troppo corto.")
    say(f"{len(dates)} date di verifica dal {dates[0].date()} al {dates[-1].date()}, ogni {step_months} mesi")

    snaps: dict[pd.Timestamp, list[dict[str, Any]]] = {d: [] for d in dates}
    fwd: dict[str, dict[pd.Timestamp, dict[int, float | None]]] = {}

    def load(c):
        try:
            cf, _ = sec.companyfacts(c["cik"])
            return c, cf, None
        except Exception as e:  # noqa: BLE001
            return c, None, e

    done = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k0 in range(0, len(comp), 12):
            for c, cf, err in ex.map(load, comp[k0:k0 + 12]):
                done += 1
                if done % 100 == 0:
                    say(f"   {done}/{len(comp)} società")
                df = px_all.get(c["ticker"])
                if err is not None or df is None or df.empty or not cf.get("facts"):
                    failed += 1
                    continue
                df = main_unit(df, 1.0)
                splits = store.splits(c["ticker"])
                eur = fx.series_to_eur(df["adj_close"], "USD")
                fwd[c["company_id"]] = {d: {h: (forward_return(eur, d, h) if eur is not None else None) for h in HORIZONS}
                                        for d in dates}
                for d in dates:
                    snap = snapshot_company(c, cf, d, df, splits, fx, bench, macro, cfg)
                    if snap is not None:
                        snaps[d].append(snap)
    if failed:
        say(f"   {failed} società senza dati utilizzabili (cache SEC o prezzi mancanti)")

    # ---- cross-section per date: the same scoring as the real pipeline
    weights = dict(cfg.get("scoring.weights", {}))
    min_peer = int(cfg.get("scoring.min_peer_group", 8))
    obs_rows: list[dict[str, Any]] = []
    bench_fwd: dict[str, dict[int, float | None]] = {}
    for d in dates:
        scored = score_snapshot(snaps[d], weights, min_peer, min_cap)
        if scored is None:
            say(f"   {d.date()}: meno di {MIN_UNIVERSE} società con punteggio, data saltata")
            continue
        for _, r in scored.iterrows():
            f = fwd.get(r["company_id"], {}).get(d, {})
            obs_rows.append({"asof": str(d.date()), "company_id": r["company_id"], "ticker": r["ticker"],
                             "sector": r.get("sector"), "market_cap_usd": _num(r.get("market_cap")),
                             "robust_score": _num(r.get("robust_score")), "robust_percentile": _num(r.get("robust_percentile")),
                             "classification": r.get("classification"),
                             **{f"fwd_{h}m": f.get(h) for h in HORIZONS}})
        bench_fwd[str(d.date())] = {h: (forward_return(bench, d, h) if bench is not None else None) for h in HORIZONS}
        say(f"   {d.date()}: {len(scored)} società con punteggio")
    if not obs_rows:
        raise RuntimeError("Nessuna data con abbastanza società: test storico non eseguibile.")
    obs = pd.DataFrame(obs_rows)
    stats = evaluate(obs, bench_fwd)
    summary = {"stats": stats, "caveats": CAVEATS, "n_companies": len(comp), "min_market_cap_usd": min_cap,
               "dates": [str(d.date()) for d in dates], "step_months": step_months,
               "readings": {h: dict(zip(("level", "text"), reading(s))) for h, s in stats.items()}}
    cur = con.execute("INSERT INTO backtests (created_at, params, summary) VALUES (?,?,?)",
                      (now_iso(), json.dumps({"start": start, "step_months": step_months, "limit": limit,
                                              "min_cap_usd": min_cap}), json.dumps(summary)))
    bt_id = cur.lastrowid
    upsert(con, "backtest_obs", [{"bt_id": bt_id, **r} for r in obs_rows])
    con.commit()
    say(f"Test storico #{bt_id} completato: {len(obs_rows)} osservazioni")
    for h, s in stats.items():
        lvl, text = summary["readings"][h]["level"], summary["readings"][h]["text"]
        say(f"   orizzonte {h} mesi: {lvl} (correlazione media {s.get('ic_mean')}, differenza Q1-Q5 {s.get('q1_minus_q5')})")
    return int(bt_id)


def _num(x):
    try:
        return None if x is None or pd.isna(x) else float(x)
    except (TypeError, ValueError):
        return None


def snapshot_company(c: dict[str, Any], cf: dict[str, Any], asof: pd.Timestamp, df: pd.DataFrame,
                     splits: list[tuple[str, float]], fx: FxTable, bench: pd.Series | None,
                     macro: dict[str, pd.Series], cfg) -> dict[str, Any] | None:
    """Metrics of one company as they could have been computed on `asof` (None if not computable)."""
    close, adj = df["close"][df.index <= asof].dropna(), df["adj_close"][df.index <= asof].dropna()
    if len(close) < MIN_PRICE_HISTORY or (asof - close.index[-1]).days > 7:
        return None
    price = float(close.iloc[-1])
    cft = truncate_companyfacts(cf, asof)
    if not cft["facts"]:
        return None
    try:
        # splits: ALL known splits, also those after `asof`. Share counts are then on the same basis as the
        # split-adjusted prices (market cap is unchanged by a split: nothing about the future leaks).
        fin = normalize_companyfacts(c["company_id"], cft, splits=splits, apply_splits=True, known_splits=splits,
                                     fetched_at=None)
        if fin.annual.empty or fin.currency != "USD":
            return None
        bench_t = bench[bench.index <= asof] if bench is not None else None
        with clock.as_of(asof):
            a = analyze_company(c, fin, {}, price, close, adj, fx, bench_t, _rates_at(macro, asof), cfg)
    except Exception:  # noqa: BLE001
        return None
    return {"company": c, "metrics": {k: mv.value for k, mv in a.metrics.items()}, "flags": a.flags}


def score_snapshot(snap: list[dict[str, Any]], weights: dict[str, float], min_peer: int,
                   min_cap: float) -> pd.DataFrame | None:
    """Scoring of one date, mirroring Pipeline._scoring_frame / stage_scoring (same gates, same functions)."""
    if not snap:
        return None
    base = pd.DataFrame([s["company"] for s in snap])
    mdf = pd.DataFrame([s["metrics"] for s in snap])
    df = pd.concat([base.reset_index(drop=True), mdf.reset_index(drop=True)], axis=1)
    flags = {s["company"]["company_id"]: s["flags"] for s in snap}
    df = df[pd.to_numeric(df["market_cap"], errors="coerce") >= min_cap].reset_index(drop=True)
    if "momentum_12_1" in df.columns:
        df["momentum_pct"] = pd.to_numeric(df["momentum_12_1"], errors="coerce").rank(pct=True) * 100
    age = pd.to_numeric(df.get("data_age_days"), errors="coerce")
    df = df[~(age > 550)].reset_index(drop=True)
    if len(df) < MIN_UNIVERSE:
        return None
    scored = scoring.score_universe(df, weights, min_peer)
    scored["robust_percentile"] = scored["robust_score"].rank(pct=True) * 100
    scored["robust_rank"] = scored["robust_score"].rank(ascending=False, method="min")
    scored = scored[scored["robust_score"].notna()].copy()
    if len(scored) < MIN_UNIVERSE:
        return None
    cls = []
    for _, r in scored.iterrows():
        try:
            cls.append(scoring.classify_row(r, flags.get(r["company_id"], []))[0])
        except Exception:  # noqa: BLE001
            cls.append(None)
    scored["classification"] = cls
    return scored
