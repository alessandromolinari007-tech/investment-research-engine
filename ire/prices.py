"""Price store: daily prices in SQLite (raw Yahoo units) with freshness tracking.

Two kinds of download:
* FULL: whole history from `start` with corporate actions; replaces everything stored for the ticker
  and sets `price_meta.full_fetched_at`. Repeated every `cache.prices_full_ttl_days`.
* INCREMENTAL: only the recent window. Before the new rows are stored, the dates that overlap the
  stored history are compared: Yahoo re-adjusts the WHOLE history after a split (close) or a dividend
  (adj_close), so a difference above `OVERLAP_TOL` (or a split not yet recorded) means the stored
  history is no longer consistent. The ticker's rows are then replaced by the window just downloaded
  and the next `get_full` downloads the full history again.

Rows are written and committed one download batch at a time (memory stays bounded by the batch).
"""
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
OVERLAP_TOL = 0.005          # 0.5%: above this the stored history was re-adjusted by Yahoo
REFRESH_OVERLAP_DAYS = 14    # incremental refresh re-downloads this many days already stored
STORE_BATCH = 50             # tickers per download → write → commit cycle


class PriceStore:
    def __init__(self, con: sqlite3.Connection, progress: Callable[[str], None] = print):
        self.con = con
        self.progress = progress
        cfg = load_config()
        self.ttl_s = float(cfg.get("cache.prices_ttl_hours", 20)) * 3600
        self.full_ttl_s = float(cfg.get("cache.prices_full_ttl_days", 7)) * 86400
        self.last_empty: list[str] = []      # tickers for which the last download returned nothing

    # ------------------------------------------------------------------ freshness
    def _meta(self) -> dict[str, sqlite3.Row]:
        return {r["ticker"]: r for r in self.con.execute("SELECT * FROM price_meta")}

    def _is_fresh(self, meta: sqlite3.Row | None, start: str) -> bool:
        """Recent prices are fresh: downloaded less than `prices_ttl_hours` ago and covering `start`."""
        if meta is None or not meta["fetched_at"] or not meta["last_date"]:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta["fetched_at"])).total_seconds()
        except ValueError:
            return False
        if age > self.ttl_s:
            return False
        return (meta["requested_start"] or "9999") <= start

    def _full_is_fresh(self, meta: sqlite3.Row | None, start: str) -> bool:
        """The stored history is a consistent FULL download from `start`, not older than `prices_full_ttl_days`."""
        if meta is None or meta["full_fetched_at"] is None or not meta["last_date"]:
            return False
        if time.time() - float(meta["full_fetched_at"]) > self.full_ttl_s:
            return False
        return (meta["requested_start"] or "9999") <= start

    # ------------------------------------------------------------------ public API
    def get_recent(self, tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
        self.last_empty = []
        meta = self._meta()
        stale = [t for t in dict.fromkeys(tickers) if not self._is_fresh(meta.get(t), start)]
        if stale:
            self.progress(f"  scarico prezzi recenti per {len(stale)} titoli ({len(set(tickers)) - len(stale)} già in cache)")
            readj = self._incremental(stale, {t: start for t in stale}, actions=False)
            if readj:
                self.progress(f"  {len(readj)} titoli con storico rettificato da Yahoo (split/dividendi): "
                              "lo storico completo verrà riscaricato")
        return self._read(tickers, start)

    def get_full(self, tickers: list[str], start: str = FULL_START, read: bool = True) -> dict[str, pd.DataFrame]:
        """Makes sure the full history is stored and fresh; returns it only if `read` (memory: ~3000 tickers × 12 years)."""
        self.last_empty = []
        meta = self._meta()
        uniq = list(dict.fromkeys(tickers))
        full = [t for t in uniq if not self._full_is_fresh(meta.get(t), start)]
        top_up = [t for t in uniq if t not in set(full) and not self._is_fresh(meta.get(t), start)]
        if top_up:
            starts = {t: (pd.Timestamp(meta[t]["last_date"]) - pd.Timedelta(days=REFRESH_OVERLAP_DAYS)).strftime("%Y-%m-%d")
                      for t in top_up}
            self.progress(f"  aggiorno gli ultimi giorni di prezzi per {len(top_up)} titoli")
            full += self._incremental(top_up, starts, actions=True)
        if full:
            self.progress(f"  scarico storico prezzi completo dal {start} per {len(full)} titoli")
            self._full(full, start)
        if self.last_empty:
            self.progress(f"  nessun prezzo ricevuto da Yahoo per {len(self.last_empty)} titoli "
                          "(delistati, ticker errato o limite di richieste): verranno ritentati al prossimo avvio")
        return self._read(tickers, start) if read else {}

    def set_currency(self, ticker: str, currency_raw: str | None) -> None:
        cur, div = yahoo.normalize_currency(currency_raw)
        self.con.execute("UPDATE price_meta SET currency=?, divisor=? WHERE ticker=?", (currency_raw, div, ticker))

    def splits(self, ticker: str) -> list[tuple[str, float]]:
        return [(r["date"], float(r["ratio"])) for r in
                self.con.execute("SELECT date, ratio FROM splits WHERE ticker=? ORDER BY date", (ticker,))]

    # ------------------------------------------------------------------ downloads
    def _batches(self, tickers: list[str], start: str | dict[str, str], actions: bool):
        """Yields (chunk, {ticker: DataFrame}) one batch at a time; tickers with no data go to `last_empty`."""
        for chunk in yahoo.iter_chunks(tickers, STORE_BATCH):
            if isinstance(start, dict):
                s = min(start[t] for t in chunk)
            else:
                s = start
            data = yahoo.download_prices(chunk, start=s, actions=actions)
            # today's bar can be an intraday snapshot (US market open in the European evening): never stored,
            # otherwise the next run sees a "different" close and believes the history was re-adjusted
            today = pd.Timestamp.today().normalize()
            got = {}
            for t, df in data.items():
                if df is not None and not df.empty:
                    df = df[df.index < today]
                    if not df.empty:
                        got[t] = df
            self.last_empty += [t for t in chunk if t not in got]
            yield chunk, got

    def _full(self, tickers: list[str], start: str) -> None:
        for _, got in self._batches(tickers, start, actions=True):
            for t, df in got.items():
                self.con.execute("DELETE FROM prices WHERE ticker=?", (t,))
                self.con.execute("DELETE FROM splits WHERE ticker=?", (t,))
                self._insert_rows(t, df)
                upsert(self.con, "splits", _split_rows(t, df))
                self._write_meta(t, df.index.min(), df.index.max(), start, full=True)
            self.con.commit()

    def _incremental(self, tickers: list[str], starts: dict[str, str], actions: bool) -> list[str]:
        """Downloads the recent window; returns the tickers whose stored history was re-adjusted by Yahoo."""
        readjusted: list[str] = []
        meta = self._meta()
        for _, got in self._batches(tickers, starts, actions=actions):
            for t, df in got.items():
                df = df[df.index >= pd.Timestamp(starts[t])]
                if df.empty:
                    continue
                m = meta.get(t)
                consistent = m is not None and m["last_date"] and self._consistent(t, df, m)
                if consistent:
                    self._insert_rows(t, df)
                    first = min(pd.Timestamp(m["first_date"]), df.index.min()) if m["first_date"] else df.index.min()
                    req = min(m["requested_start"] or starts[t], starts[t])
                    self._write_meta(t, first, max(pd.Timestamp(m["last_date"]), df.index.max()), req, full=None)
                elif m is not None and m["last_date"]:
                    # Yahoo re-adjusted the history (split/dividend) or there is a gap: the stored history is KEPT
                    # (it is replaced only when the full re-download succeeds); only the new days are appended,
                    # and the ticker is marked for a full download (full_fetched_at = NULL)
                    readjusted.append(t)
                    newer = df[df.index > pd.Timestamp(m["last_date"])]
                    if not newer.empty:
                        self._insert_rows(t, newer)
                    self._write_meta(t, pd.Timestamp(m["first_date"] or df.index.min()),
                                     max(pd.Timestamp(m["last_date"]), df.index.max()),
                                     m["requested_start"] or starts[t], full=False)
                else:
                    self._insert_rows(t, df)                       # first download of this ticker
                    self._write_meta(t, df.index.min(), df.index.max(), starts[t], full=False)
                if actions:
                    upsert(self.con, "splits", _split_rows(t, df))
            self.con.commit()
        return readjusted

    def _consistent(self, t: str, df: pd.DataFrame, m: sqlite3.Row) -> bool:
        """True if the stored prices on the overlapping dates match the new download and no new split appeared."""
        if df.index.min() > pd.Timestamp(m["last_date"]) + pd.Timedelta(days=7):
            return False                    # gap between stored history and new window: cannot verify continuity
        if "splits" in df.columns:
            known = {d for d, _ in self.splits(t)}
            for dt, v in df["splits"].items():
                if v and v > 0 and v != 1 and dt.strftime("%Y-%m-%d") not in known:
                    return False
        old = pd.read_sql_query("SELECT date, close, adj_close FROM prices WHERE ticker=? AND date>=? AND date<=?",
                                self.con, params=[t, df.index.min().strftime("%Y-%m-%d"),
                                                  df.index.max().strftime("%Y-%m-%d")])
        if old.empty:
            return True
        old["date"] = pd.to_datetime(old["date"])
        j = old.set_index("date").join(df[["close", "adj_close"]], rsuffix="_new", how="inner")
        for col in ("close", "adj_close"):
            a, b = j[col].astype(float), j[f"{col}_new"].astype(float)
            ok = a.notna() & b.notna() & (a != 0)
            if ok.any() and float((b[ok] / a[ok] - 1).abs().max()) > OVERLAP_TOL:
                return False
        return True

    def _insert_rows(self, t: str, df: pd.DataFrame) -> None:
        dates = df.index.strftime("%Y-%m-%d")
        cols = [_clean(df[c]) if c in df.columns else [None] * len(df) for c in ("close", "adj_close", "volume")]
        self.con.executemany("INSERT OR REPLACE INTO prices (ticker, date, close, adj_close, volume) VALUES (?,?,?,?,?)",
                             zip([t] * len(df), dates, *cols))

    def _write_meta(self, t: str, first, last, requested_start: str, full: bool | None) -> None:
        """full=True: full download now; full=False: history replaced by a partial window; None: keep as is."""
        prev = self.con.execute("SELECT currency, divisor, full_fetched_at FROM price_meta WHERE ticker=?", (t,)).fetchone()
        full_at = time.time() if full else (None if full is False else (prev["full_fetched_at"] if prev else None))
        upsert(self.con, "price_meta", [{
            "ticker": t, "currency": prev["currency"] if prev else None, "divisor": prev["divisor"] if prev else None,
            "first_date": pd.Timestamp(first).strftime("%Y-%m-%d"), "last_date": pd.Timestamp(last).strftime("%Y-%m-%d"),
            "requested_start": requested_start, "fetched_at": now_iso(), "full_fetched_at": full_at}])

    # ------------------------------------------------------------------ read
    def _read(self, tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for chunk in yahoo.iter_chunks(list(dict.fromkeys(tickers)), 400):
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


def _split_rows(t: str, df: pd.DataFrame) -> list[dict]:
    if "splits" not in df.columns:
        return []
    return [{"ticker": t, "date": dt.strftime("%Y-%m-%d"), "ratio": float(v)}
            for dt, v in df["splits"].items() if v and v > 0 and v != 1]


def _clean(s: pd.Series) -> list:
    a = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
    return [float(x) if np.isfinite(x) else None for x in a]


def main_unit(df: pd.DataFrame, divisor: float | None) -> pd.DataFrame:
    d = float(divisor or 1.0)
    if d == 1.0:
        return df
    out = df.copy()
    for c in ("close", "adj_close"):
        if c in out.columns:
            out[c] = out[c] / d
    return out
