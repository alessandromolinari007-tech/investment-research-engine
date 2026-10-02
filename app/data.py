"""Read-only data access for the UI (cached)."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ire.config import load_config  # noqa: E402
from ire.db import connect, init_db  # noqa: E402
from ire.sources.yahoo import normalize_currency  # noqa: E402

KEY_METRICS = [
    "market_cap", "pe", "ev_ebit", "fcf_yield", "fcf_sbc_yield", "dividend_yield", "gross_margin", "operating_margin",
    "roic_5y_median", "roe", "revenue_cagr_5y", "revenue_cagr_3y", "eps_cagr_5y", "net_debt_ebitda", "interest_coverage",
    "share_change_cagr_5y", "implied_fcf_growth", "growth_gap", "vol_1y", "max_drawdown_5y", "beta_world",
    "pe_vs_history_pct", "earnings_yield", "pb", "years_of_data", "drawdown_from_3y_high", "fcf_margin", "ev_sales",
    "net_margin", "piotroski_f", "momentum_12_1", "return_1y", "roic", "fcf_ps_cagr_5y", "sbc_to_revenue",
    "shareholder_yield", "equity_to_assets", "roe_5y_median", "p_ffo", "data_age_days",
]


_LOCAL = threading.local()


@st.cache_resource
def _schema_ready(path: str) -> bool:
    init_db(Path(path)).close()          # create / upgrade the schema once per process and database
    return True


def con():
    """One SQLite connection per thread: Streamlit runs every browser session in its own thread, and a
    single connection shared between sessions can interleave transactions."""
    path = load_config().db_path
    _schema_ready(str(path))
    if getattr(_LOCAL, "path", None) != path:
        _LOCAL.con, _LOCAL.path = connect(path), path
    return _LOCAL.con


def q(sql: str, params: list | tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con(), params=list(params))


@st.cache_data(ttl=60)
def runs() -> pd.DataFrame:
    return q("SELECT * FROM runs ORDER BY run_id DESC")


def latest_run() -> int | None:
    r = runs()
    done = r[r["status"] == "completed"]
    return int(done["run_id"].iloc[0]) if len(done) else None


@st.cache_data(ttl=300)
def universe(run_id: int) -> pd.DataFrame:
    s = q("""SELECT c.company_id, c.ticker, c.name, c.country, c.region, c.sector, c.industry, c.data_tier, c.filer_type,
                    c.price_currency, c.fin_currency, c.is_banklike, c.is_reit, c.exchange, c.other_listings,
                    s.quality, s.growth, s.financial_strength, s.valuation, s.capital_allocation, s.composite,
                    s.robust_score, s.robust_rank, s.rank_spread, s.coverage, s.confidence, s.classification,
                    s.valuation_verdict, s.valuation_confidence, s.peer_group
             FROM scores s JOIN companies c USING(company_id) WHERE s.run_id=?""", [run_id])
    ph = ",".join("?" for _ in KEY_METRICS)
    m = q(f"SELECT company_id, metric, value FROM metrics WHERE run_id=? AND metric IN ({ph})", [run_id, *KEY_METRICS])
    if not m.empty:
        mp = m.pivot_table(index="company_id", columns="metric", values="value", aggfunc="last")
        s = s.merge(mp, left_on="company_id", right_index=True, how="left")
    for k in KEY_METRICS:          # a metric missing for every company must still exist as an empty column
        if k not in s.columns:
            s[k] = float("nan")
    fx = fx_table()
    s["market_cap_eur"] = [fx.convert(float(mc), cur or "USD", "EUR") if pd.notna(mc) else None
                           for mc, cur in zip(s["market_cap"], s["fin_currency"])]
    s["market_cap_eur"] = pd.to_numeric(s["market_cap_eur"], errors="coerce")
    s["label"] = s["ticker"] + " — " + s["name"].fillna("")
    return s


@st.cache_data(ttl=300)
def company(cid: str, run_id: int) -> dict[str, Any]:
    c = q("SELECT * FROM companies WHERE company_id=?", [cid])
    out: dict[str, Any] = {"company": c.iloc[0].to_dict() if len(c) else {}}
    out["metrics"] = q("SELECT metric, value, kind, period, method, inputs FROM metrics WHERE run_id=? AND company_id=?", [run_id, cid])
    out["flags"] = q("SELECT code, severity, message, evidence, source FROM flags WHERE run_id=? AND company_id=?", [run_id, cid])
    out["facts"] = q("SELECT * FROM facts WHERE company_id=? ORDER BY item, period_end", [cid])
    out["filings"] = q("SELECT form, filed, report_date, items, url FROM filings WHERE company_id=? ORDER BY filed DESC", [cid])
    out["conflicts"] = q("SELECT item, period_end, value_a, source_a, value_b, source_b, pct_diff, likely_reason "
                         "FROM conflicts WHERE run_id=? AND company_id=?", [run_id, cid])
    sc = q("SELECT * FROM scores WHERE run_id=? AND company_id=?", [run_id, cid])
    out["score"] = sc.iloc[0].to_dict() if len(sc) else {}
    out["detail"] = json.loads(out["score"].get("detail") or "{}") if out["score"] else {}
    md = q("SELECT * FROM market_data WHERE company_id=?", [cid])
    out["market"] = md.iloc[0].to_dict() if len(md) else {}
    out["market_extra"] = json.loads(out["market"].get("raw_json") or "{}") if out["market"] else {}
    ql = q("SELECT payload, as_of FROM qualitative WHERE company_id=?", [cid])
    out["qualitative"] = json.loads(ql["payload"].iloc[0]) if len(ql) else {}
    return out


@st.cache_data(ttl=300)
def prices(ticker: str, currency: str | None = None) -> pd.DataFrame:
    df = q("SELECT date, close, adj_close FROM prices WHERE ticker=? ORDER BY date", [ticker])
    if df.empty:
        return df
    meta = q("SELECT currency, divisor FROM price_meta WHERE ticker=?", [ticker])
    div = float(meta["divisor"].iloc[0]) if len(meta) and pd.notna(meta["divisor"].iloc[0]) else normalize_currency(currency)[1]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df / div


@st.cache_data(ttl=3600)
def fx_table():
    from ire.sources.fx_macro import FxTable

    return FxTable(q("SELECT date, currency, per_eur, source FROM fx"))


@st.cache_data(ttl=300)
def benchmark(run_id: int | None) -> tuple[pd.Series | None, str]:
    """The benchmark the pipeline actually used for this run (SWDA.MI → IWDA.AS → URTH), in EUR."""
    from ire.risk import BENCHMARKS

    names = dict(BENCHMARKS)
    tick, cur = "SWDA.MI", "EUR"
    if run_id is not None:
        r = q("SELECT summary FROM runs WHERE run_id=?", [run_id])
        if len(r):
            summ = json.loads(r["summary"].iloc[0] or "{}")
            tick, cur = summ.get("benchmark_ticker") or tick, summ.get("benchmark_currency") or cur
    px = prices(tick, cur)
    if px.empty:
        return None, names.get(tick, tick)
    eur = fx_table().series_to_eur(px["adj_close"], cur)
    return (eur.dropna() if eur is not None else None), names.get(tick, tick)


@st.cache_data(ttl=300)
def portfolio(run_id: int) -> dict[str, Any]:
    r = q("SELECT payload FROM portfolios WHERE run_id=? AND name='proposto'", [run_id])
    return json.loads(r["payload"].iloc[0]) if len(r) else {}


@st.cache_data(ttl=300)
def changes(new_run: int, old_run: int) -> pd.DataFrame:
    from ire.changes import compute_changes

    return compute_changes(con(), new_run, old_run)


def all_companies() -> pd.DataFrame:
    return q("SELECT company_id, ticker, name, in_universe, exclusion_reason, region, sector, data_tier FROM companies")


def watchlist() -> list[str]:
    return list(q("SELECT ticker FROM watchlist ORDER BY ticker")["ticker"])


def set_watch(ticker: str, add: bool) -> None:
    from ire.db import now_iso

    c = con()
    if add:
        c.execute("INSERT OR REPLACE INTO watchlist (ticker, added_at) VALUES (?,?)", (ticker, now_iso()))
    else:
        c.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
    c.commit()


def user_portfolio() -> pd.DataFrame:
    return q("SELECT ticker, weight, note FROM user_portfolio ORDER BY weight DESC")


def save_user_portfolio(df: pd.DataFrame) -> None:
    """Rows with the same ticker (several purchase lots) are SUMMED, never overwritten."""
    c = con()
    rows: dict[str, list] = {}
    for _, r in df.iterrows():
        t = str(r.get("ticker") or "").strip().upper()
        w = pd.to_numeric(r.get("weight"), errors="coerce")
        if t and t != "NAN" and pd.notna(w):
            note = r.get("note") if isinstance(r.get("note"), str) and r.get("note") else None
            prev = rows.get(t, [0.0, []])
            rows[t] = [prev[0] + float(w), prev[1] + ([note] if note else [])]
    c.execute("DELETE FROM user_portfolio")
    for t, (w, notes) in rows.items():
        c.execute("INSERT OR REPLACE INTO user_portfolio (ticker, weight, note) VALUES (?,?,?)",
                  (t, w, "; ".join(notes) or None))
    c.commit()


@st.cache_data(ttl=60)
def latest_backtest() -> dict[str, Any] | None:
    """Most recent historical test (summary JSON + id + date), or None."""
    import json as _json

    r = q("SELECT bt_id, created_at, params, summary FROM backtests ORDER BY bt_id DESC LIMIT 1")
    if r.empty:
        return None
    row = r.iloc[0]
    out = _json.loads(row["summary"] or "{}")
    out.update({"bt_id": int(row["bt_id"]), "created_at": row["created_at"], "params": _json.loads(row["params"] or "{}")})
    return out


@st.cache_data(ttl=300)
def track_record() -> list[dict[str, Any]]:
    """How the portfolios proposed by past analyses did since the day they were made (ire/track.py)."""
    from ire.track import live_track_record

    return live_track_record(con())
