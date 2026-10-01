"""FX (ECB reference rates, official & free) and interest rates (FRED, free, no key).

FX convention in the DB: `per_eur` = units of currency per 1 EUR (ECB convention).
Currencies not published by the ECB (e.g. TWD) fall back to Yahoo FX pairs and are
tagged with that source.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import numpy as np
import pandas as pd

from ..http import SourceUnavailable, get_client

ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# 10-year government bond yield by currency (risk-free proxy for that currency).
RATE_SERIES = {
    "USD": "DGS10",               # daily, %
    "EUR": "IRLTLT01DEM156N",     # Germany 10y, monthly, %
    "GBP": "IRLTLT01GBM156N",
    "JPY": "IRLTLT01JPM156N",
    "CAD": "IRLTLT01CAM156N",
    "AUD": "IRLTLT01AUM156N",
    "CHF": "IRLTLT01CHM156N",
    "SEK": "IRLTLT01SEM156N",
    "DKK": "IRLTLT01DKM156N",
    "NOK": "IRLTLT01NOM156N",
    "KRW": "IRLTLT01KRM156N",
}


def ecb_history() -> pd.DataFrame:
    """Long DataFrame [date, currency, per_eur]."""
    r = get_client().get(ECB_HIST_URL, ttl_seconds=20 * 3600)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        df = pd.read_csv(z.open(name))
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df = df.rename(columns={"Date": "date"})
    long = df.melt(id_vars="date", var_name="currency", value_name="per_eur")
    long["currency"] = long["currency"].str.strip()
    long["per_eur"] = pd.to_numeric(long["per_eur"], errors="coerce")
    long = long.dropna(subset=["per_eur"])
    long["date"] = pd.to_datetime(long["date"]).dt.strftime("%Y-%m-%d")
    long["source"] = "ECB euro foreign exchange reference rates"
    return long


def fred_series(series: str) -> pd.DataFrame:
    r = get_client().get(FRED_CSV_URL.format(series=series), ttl_seconds=20 * 3600)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    df["series"] = series
    df["source"] = f"FRED (Federal Reserve Bank of St. Louis) series {series}"
    return df


class FxTable:
    """In-memory FX lookup built from the DB `fx` table."""

    def __init__(self, df: pd.DataFrame):
        # wide: index date, columns currency, values per_eur
        if df.empty:
            self.wide = pd.DataFrame()
        else:
            w = df.pivot_table(index="date", columns="currency", values="per_eur", aggfunc="last")
            w.index = pd.to_datetime(w.index)
            w = w.sort_index()
            w["EUR"] = 1.0
            self.wide = w.ffill()
        self.sources = {}
        if not df.empty and "source" in df.columns:
            self.sources = df.groupby("currency")["source"].last().to_dict()
        self.sources["EUR"] = "identity"

    def has(self, cur: str) -> bool:
        return cur == "EUR" or (not self.wide.empty and cur in self.wide.columns)

    def rate(self, cur: str, on: date | str | pd.Timestamp | None = None) -> float | None:
        """Units of `cur` per 1 EUR on (or before) date. None if unknown."""
        if cur == "EUR":
            return 1.0
        if self.wide.empty or cur not in self.wide.columns:
            return None
        s = self.wide[cur].dropna()
        if s.empty:
            return None
        if on is None:
            return float(s.iloc[-1])
        ts = pd.Timestamp(on)
        s2 = s.loc[:ts]
        if s2.empty:
            return None
        # stale guard: don't use a rate more than 10 days old for a given date
        if (ts - s2.index[-1]).days > 10:
            return None
        return float(s2.iloc[-1])

    def convert(self, amount: float | None, from_cur: str, to_cur: str, on=None) -> float | None:
        if amount is None or (isinstance(amount, float) and np.isnan(amount)):
            return None
        if from_cur == to_cur:
            return float(amount)
        a = self.rate(from_cur, on)
        b = self.rate(to_cur, on)
        if a is None or b is None:
            return None
        return float(amount) / a * b

    def series_to_eur(self, prices: pd.Series, cur: str) -> pd.Series | None:
        """Convert a daily price series in `cur` to EUR using same-day (ffilled) rates."""
        if cur == "EUR":
            return prices
        if self.wide.empty or cur not in self.wide.columns:
            return None
        r = self.wide[cur].reindex(prices.index.union(self.wide.index)).ffill().reindex(prices.index)
        out = prices / r
        return out
