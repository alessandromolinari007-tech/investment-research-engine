"""Price store: daily prices in SQLite (raw Yahoo units) with freshness tracking."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd

from .config import load_config
from .db import now_iso, upsert
from .sources import yahoo

FULL_START = "2014-01-01"


class PriceStore:
    def __init__(self, con: sqlite3.Connection, progress: Callable[[str], None] = print):
        self.con = con
        self.progress = progress
        self.ttl_s = float(load_config().get("cache.prices_ttl_hours", 20)) * 3600

    # ------------------------------------------------------------------ freshness
    def _meta(self) -> dict[str, sqlite3.Row]:
        return {r["ticker"]: r for r in self.con.execute("SELECT * FROM price_meta")}

    def _is_fresh(self, meta: sqlite3.Row | None, start: str) -> bool:
        if meta is None or not meta["fetched_at"]:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta["fetched_at"])).total_seconds()
        except ValueError:
            return False
        if age > self.ttl_s:
            return False
        req = meta["requested_start"] or meta["first_date"] or "9999"
        return req <= start

    # ------------------------------------------------------------------ download + store
    def _download_store(self, tickers: list[str], start: str, actions: bool) -> None:
        if not tickers:
            return
        data = yahoo.download_prices(tickers, start=start, batch=100 if actions else 150, actions=actions)
        rows, split_rows, metas = [], [], []
        for t in tickers:
            df = data.get(t)
            if df is None or df.empty:
                metas.append({"ticker": t, "currency": None, "divisor": None, "first_date": None, "last_date": None,
                              "requested_start": start, "fetched_at": now_iso()})
                continue
            for dt, r in df.iterrows():
                rows.append((t, dt.strftime("%Y-%m-%d"), _f(r.get("close")), _f(r.get("adj_close")), _f(r.get("volume"))))
            if "splits" in df.columns:
                for dt, v in df["splits"].items():
                    if v and v > 0 and v != 1:
                        split_rows.append({"ticker": t, "date": dt.strftime("%Y-%m-%d"), "ratio": float(v)})
            old = self.con.execute("SELECT first_date, requested_start FROM price_meta WHERE ticker=?", (t,)).fetchone()
            first = df.index.min().strftime("%Y-%m-%d")
            req = start
            if old and old["requested_start"] and old["requested_start"] < start:
                req = old["requested_start"]
                first = min(first, old["first_date"] or first)
            metas.append({"ticker": t, "currency": None, "divisor": None, "first_date": first,
                          "last_date": df.index.max().strftime("%Y-%m-%d"), "requested_start": req, "fetched_at": now_iso()})
        self.con.executemany("INSERT OR REPLACE INTO prices (ticker, date, close, adj_close, volume) VALUES (?,?,?,?,?)", rows)
        if actions:
            upsert(self.con, "splits", split_rows)
        # keep currency/divisor if already known
        for m in metas:
            prev = self.con.execute("SELECT currency, divisor FROM price_meta WHERE ticker=?", (m["ticker"],)).fetchone()
            if prev:
                m["currency"], m["divisor"] = prev["currency"], prev["divisor"]
        upsert(self.con, "price_meta", metas)
        self.con.commit()

    def _read(self, tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for chunk in yahoo.iter_chunks(tickers, 400):
            q = ",".join("?" for _ in chunk)
            df = pd.read_sql_query(
                f"SELECT ticker, date, close, adj_close, volume FROM prices WHERE ticker IN ({q}) AND date >= ?",
                self.con, params=[*chunk, start])
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            for t, g in df.groupby("ticker"):
                out[t] = g.set_index("date")[["close", "adj_close", "volume"]].sort_index()
        return out

    def get_recent(self, tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
        meta = self._meta()
        stale = [t for t in tickers if not self._is_fresh(meta.get(t), start)]
        if stale:
            self.progress(f"  scarico prezzi recenti per {len(stale)} titoli ({len(tickers) - len(stale)} già in cache)")
            self._download_store(stale, start, actions=False)
        return self._read(tickers, start)

    def get_full(self, tickers: list[str], start: str = FULL_START) -> dict[str, pd.DataFrame]:
        meta = self._meta()
        stale = [t for t in tickers if not self._is_fresh(meta.get(t), start)]
        if stale:
            self.progress(f"  scarico storico prezzi dal {start} per {len(stale)} titoli")
            self._download_store(stale, start, actions=True)
        return self._read(tickers, start)

    def set_currency(self, ticker: str, currency_raw: str | None) -> None:
        cur, div = yahoo.normalize_currency(currency_raw)
        self.con.execute("UPDATE price_meta SET currency=?, divisor=? WHERE ticker=?", (currency_raw, div, ticker))

    def splits(self, ticker: str) -> list[tuple[str, float]]:
        return [(r["date"], float(r["ratio"])) for r in
                self.con.execute("SELECT date, ratio FROM splits WHERE ticker=? ORDER BY date", (ticker,))]


def main_unit(df: pd.DataFrame, divisor: float | None) -> pd.DataFrame:
    d = float(divisor or 1.0)
    if d == 1.0:
        return df
    out = df.copy()
    for c in ("close", "adj_close"):
        if c in out.columns:
            out[c] = out[c] / d
    return out


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None
