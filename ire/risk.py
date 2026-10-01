"""Price-based risk metrics, computed in the investor's base currency (EUR by default).

Benchmark: iShares Core MSCI World UCITS ETF quoted in EUR on Borsa Italiana (SWDA.MI) —
the realistic passive alternative for an EU retail investor. Fallback: URTH (USD) → EUR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BENCHMARKS = [("SWDA.MI", "iShares Core MSCI World UCITS ETF (EUR, Borsa Italiana)"),
              ("IWDA.AS", "iShares Core MSCI World UCITS ETF (EUR, Euronext Amsterdam)"),
              ("URTH", "iShares MSCI World ETF (USD, convertito in EUR)")]

TRADING_DAYS = 252


def annualized_vol(prices: pd.Series, days: int) -> float | None:
    p = prices.dropna()
    p = p[p > 0]
    if len(p) < days * 0.8:
        return None
    r = np.log(p.iloc[-days:]).diff().dropna()
    if len(r) < 20:
        return None
    return float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))


def max_drawdown(prices: pd.Series, years: int) -> float | None:
    p = prices.dropna()
    if p.empty:
        return None
    start = p.index[-1] - pd.DateOffset(years=years)
    p = p[p.index >= start]
    if len(p) < TRADING_DAYS * years * 0.7:
        return None
    dd = p / p.cummax() - 1
    return float(dd.min())


def weekly_returns(prices: pd.Series) -> pd.Series:
    p = prices.dropna()
    w = p.resample("W-FRI").last().dropna()
    return w.pct_change().dropna()


def beta(asset_eur: pd.Series, bench_eur: pd.Series, years: int = 3) -> float | None:
    a, b = weekly_returns(asset_eur), weekly_returns(bench_eur)
    df = pd.concat([a, b], axis=1, join="inner").dropna()
    df = df[df.index >= df.index.max() - pd.DateOffset(years=years)] if len(df) else df
    if len(df) < 52 * years * 0.7:
        return None
    cov = np.cov(df.iloc[:, 0], df.iloc[:, 1], ddof=1)
    return float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else None


def price_risk_metrics(close_local: pd.Series, adj_local: pd.Series, adj_eur: pd.Series | None,
                       bench_eur: pd.Series | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    series = adj_eur if adj_eur is not None and adj_eur.dropna().size else adj_local
    out["vol_1y"] = annualized_vol(series, TRADING_DAYS)
    out["vol_3y"] = annualized_vol(series, TRADING_DAYS * 3)
    out["vol_1y_local"] = annualized_vol(adj_local, TRADING_DAYS)
    out["max_drawdown_5y"] = max_drawdown(series, 5)
    out["max_drawdown_10y"] = max_drawdown(series, 10)
    out["beta_world"] = beta(series, bench_eur) if bench_eur is not None else None
    c = close_local.dropna()
    if len(c) >= 200:
        hi = c.iloc[-TRADING_DAYS:].max()
        out["drawdown_from_52w_high"] = float(c.iloc[-1] / hi - 1)
        hi3 = c.iloc[-TRADING_DAYS * 3:].max()
        out["drawdown_from_3y_high"] = float(c.iloc[-1] / hi3 - 1)
    a = adj_local.dropna()
    if len(a) >= TRADING_DAYS + 5:
        out["momentum_12_1"] = float(a.iloc[-21] / a.iloc[-TRADING_DAYS] - 1)
        out["return_1y"] = float(series.dropna().iloc[-1] / series.dropna().iloc[-TRADING_DAYS] - 1) if len(series.dropna()) > TRADING_DAYS else None
    if len(a) >= TRADING_DAYS * 5:
        s5 = series.dropna()
        if len(s5) >= TRADING_DAYS * 5:
            out["return_5y_ann"] = float((s5.iloc[-1] / s5.iloc[-TRADING_DAYS * 5]) ** (1 / 5) - 1)
    return out
