"""Live track record: how the portfolios PROPOSED by past analyses have actually done since the day they were made.

No hypothetical assumptions beyond: bought at the first close after the analysis finished, weights as proposed,
no rebalancing, no costs or taxes, total return in EUR (dividends included), compared with the MSCI World ETF over
exactly the same days. It is the only test that cannot suffer from hindsight or survivorship: the proposal existed
before the prices did. It needs time: a few months of data is noise.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pandas as pd

from .backtest import _load_benchmark
from .prices import PriceStore, main_unit
from .sources.fx_macro import FxTable


def _eur_series(store: PriceStore, fx: FxTable, ticker: str, currency: str | None) -> pd.Series | None:
    df = store._read([ticker], "2013-01-01").get(ticker)
    if df is None or df.empty:
        return None
    meta = store.con.execute("SELECT divisor FROM price_meta WHERE ticker=?", (ticker,)).fetchone()
    div = float(meta["divisor"]) if meta and meta["divisor"] else 1.0
    s = main_unit(df, div)["adj_close"]
    return fx.series_to_eur(s, currency or "USD")


def _return_since(eur: pd.Series | None, start: pd.Timestamp) -> tuple[float | None, pd.Timestamp | None]:
    if eur is None:
        return None, None
    s = eur.dropna()
    after = s[s.index > start]
    if after.empty or (after.index[0] - start).days > 7:
        return None, None
    p0, p1 = float(after.iloc[0]), float(s.iloc[-1])
    return (p1 / p0 - 1, s.index[-1]) if p0 > 0 and p1 > 0 else (None, None)


def live_track_record(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """One row per completed analysis with a proposed portfolio, oldest first."""
    rows = con.execute("SELECT r.run_id, r.mode, r.finished_at, p.payload FROM runs r "
                       "JOIN portfolios p ON p.run_id=r.run_id AND p.name='proposto' "
                       "WHERE r.status='completed' AND r.finished_at IS NOT NULL ORDER BY r.run_id").fetchall()
    if not rows:
        return []
    fx = FxTable(pd.read_sql_query("SELECT date, currency, per_eur, source FROM fx", con))
    store = PriceStore(con, progress=lambda m: None)
    bench = _load_benchmark(store, fx)
    cur = {r["company_id"]: (r["ticker"], r["price_currency"]) for r in
           con.execute("SELECT company_id, ticker, price_currency FROM companies")}
    cache: dict[str, pd.Series | None] = {}
    out = []
    for r in rows:
        port = json.loads(r["payload"])
        pos = port.get("positions") or []
        if port.get("status") not in ("proposto", "concentrato") or not pos:      # "non proposto" = list of candidates only
            continue
        start = pd.Timestamp(r["finished_at"]).tz_convert(None).normalize() if pd.Timestamp(r["finished_at"]).tzinfo \
            else pd.Timestamp(r["finished_at"]).normalize()
        tot_w, acc, last_dates = 0.0, 0.0, []
        for p in pos:
            tick, pcur = cur.get(p["company_id"], (p["ticker"], None))
            if tick not in cache:
                cache[tick] = _eur_series(store, fx, tick, pcur)
            ret, last = _return_since(cache[tick], start)
            if ret is None:
                continue
            tot_w += float(p["weight"])
            acc += float(p["weight"]) * ret
            last_dates.append(last)
        if not tot_w or not last_dates:
            continue
        end = min(last_dates)
        b_ret, _ = _return_since(bench.loc[bench.index <= end] if bench is not None else None, start)
        pr = acc / tot_w
        out.append({"run_id": int(r["run_id"]), "mode": r["mode"], "status": port.get("status"), "start": str(start.date()), "end": str(end.date()),
                    "days": int((end - start).days), "positions": len(pos),
                    "priced_weight": round(tot_w, 4), "portfolio_return": pr, "benchmark_return": b_ret,
                    "excess": (pr - b_ret) if b_ret is not None else None})
    return out
