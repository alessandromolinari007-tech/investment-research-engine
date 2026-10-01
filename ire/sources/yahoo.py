"""Yahoo Finance via the `yfinance` library (free, unofficial).

Used for: prices (all markets), splits, market metadata (sector, currency, market cap,
analyst forward EPS), and — only for companies that do NOT file XBRL with the SEC —
annual/quarterly financial statements (≈4-5 years).

Caveats (documented in the UI):
* unofficial endpoint, can change or rate-limit without notice;
* statements are standardized by Yahoo's data vendor: definitions may differ from filings;
* London prices are quoted in pence (GBp), Johannesburg in cents (ZAc), Tel Aviv in agorot (ILA):
  we convert to the main unit and record the divisor.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..config import load_config

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
EMPTY_RETRY_SHARE = 0.5     # more than this share of a price batch empty → probably rate-limited: wait and retry
EMPTY_RETRIES = 2
FINAL_RETRY_MAX = 500          # tickers retried one last time at the end of download_prices
FINAL_RETRY_PAUSE_S = 15

SUBUNIT_CURRENCIES = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}

_yf = None


def yf():
    global _yf
    if _yf is None:
        import yfinance

        cache = load_config().cache_dir / "yfinance"
        cache.mkdir(parents=True, exist_ok=True)
        try:
            yfinance.set_tz_cache_location(str(cache))
        except Exception:
            pass
        _yf = yfinance
    return _yf


def normalize_currency(cur: str | None) -> tuple[str | None, float]:
    """'GBp' -> ('GBP', 100). Returns (iso_code, divisor)."""
    if not cur:
        return None, 1.0
    if cur in SUBUNIT_CURRENCIES:
        return SUBUNIT_CURRENCIES[cur]
    return cur.upper(), 1.0


# ---------------------------------------------------------------------------
# JSON disk cache for per-ticker endpoints
# ---------------------------------------------------------------------------
def _cache_path(kind: str, ticker: str) -> Path:
    d = load_config().cache_dir / "yahoo" / kind
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-^=" else "_" for c in ticker)
    if safe.split(".")[0].upper() in WINDOWS_RESERVED:      # e.g. ticker "CON": reserved device name on Windows
        safe = "_" + safe
    return d / f"{safe}.json"


def _read_cache(kind: str, ticker: str, ttl_s: float) -> Any | None:
    p = _cache_path(kind, ticker)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - payload.get("_fetched_at", 0) > ttl_s:
        return None
    return payload


def _write_cache(kind: str, ticker: str, payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload["_fetched_at"] = time.time()
    _cache_path(kind, ticker).write_text(json.dumps(payload, default=str), encoding="utf-8")


class YahooThrottle:
    """Yahoo rate-limits aggressively: keep ~1-2 req/s and back off on errors."""

    def __init__(self, min_interval: float = 0.6):
        self.min_interval = min_interval
        self.last = 0.0
        self.penalty = 0.0

    def wait(self):
        dt = self.last + self.min_interval + self.penalty - time.monotonic()
        if dt > 0:
            time.sleep(dt)
        self.last = time.monotonic()

    def error(self):
        self.penalty = min(self.penalty * 2 + 2, 60)

    def ok(self):
        self.penalty = max(0.0, self.penalty / 2 - 0.5)


THROTTLE = YahooThrottle()


def _with_retries(fn, retries: int = 3):
    last = None
    for i in range(retries):
        THROTTLE.wait()
        try:
            out = fn()
            THROTTLE.ok()
            return out
        except Exception as e:  # yfinance raises many exception types
            last = e
            msg = str(e).lower()
            THROTTLE.error()
            if "rate" in msg or "too many" in msg or "429" in msg:
                time.sleep(10 * (i + 1))
            elif "not found" in msg or "404" in msg or "delisted" in msg:
                break
    raise RuntimeError(f"Yahoo request failed: {last}")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
INFO_KEYS = [
    "symbol", "shortName", "longName", "quoteType", "exchange", "fullExchangeName", "currency",
    "financialCurrency", "country", "sector", "industry", "marketCap", "sharesOutstanding",
    "impliedSharesOutstanding", "floatShares", "currentPrice", "regularMarketPrice", "previousClose",
    "forwardEps", "trailingEps", "forwardPE", "trailingPE", "pegRatio", "trailingPegRatio",
    "dividendRate", "dividendYield", "payoutRatio", "beta", "averageVolume", "averageDailyVolume3Month",
    "longBusinessSummary", "website", "numberOfAnalystOpinions", "earningsGrowth", "revenueGrowth",
    "totalRevenue", "netIncomeToCommon", "enterpriseValue", "bookValue", "priceToBook",
    "mostRecentQuarter", "lastFiscalYearEnd", "isin",
]


NEGATIVE_INFO_TTL_H = 3      # "unknown ticker" answers can also be swallowed errors: remembered only briefly


def info(ticker: str, ttl_hours: float = 20) -> dict[str, Any] | None:
    cached = _read_cache("info", ticker, ttl_hours * 3600)
    if cached is not None and (cached.get("info") is not None or
                               time.time() - cached.get("_fetched_at", 0) < NEGATIVE_INFO_TTL_H * 3600):
        return cached.get("info")

    def _get():
        t = yf().Ticker(ticker)
        return t.get_info()

    try:
        raw = _with_retries(_get)
    except Exception:
        return None
    if not raw:
        return None          # empty answer (often a silent rate limit): never cached, retried next time
    if raw.get("quoteType") is None and raw.get("regularMarketPrice") is None:
        _write_cache("info", ticker, {"info": None})      # Yahoo answered, but the ticker is unknown
        return None
    slim = {k: raw.get(k) for k in INFO_KEYS if k in raw}
    _write_cache("info", ticker, {"info": slim})
    return slim


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def download_prices(tickers: list[str], start: str = "2014-01-01", batch: int = 50,
                    actions: bool = False) -> dict[str, pd.DataFrame]:
    """Bulk daily prices. Returns {ticker: DataFrame[close, adj_close, volume(, splits)]} (raw Yahoo units).

    yfinance returns EMPTY frames (no exception) when Yahoo rate-limits: if more than half of a batch
    comes back empty, wait and retry the empty tickers (sequential download, `threads=False`)."""
    out: dict[str, pd.DataFrame] = {}
    tickers = [t for t in dict.fromkeys(tickers) if t]
    for i in range(0, len(tickers), batch):
        todo = tickers[i : i + batch]
        for attempt in range(EMPTY_RETRIES + 1):
            got = _download_chunk(todo, start, actions)
            out.update(got)
            empty = [t for t in todo if t not in got]
            if len(todo) < 4 or len(empty) <= EMPTY_RETRY_SHARE * len(todo) or attempt == EMPTY_RETRIES:
                break
            THROTTLE.error()
            time.sleep(30 * (attempt + 1))
            todo = empty
    # last recovery pass: isolated failures (Yahoo "Invalid Crumb" / "no timezone found" on valid tickers such as
    # NOVO.CO or ROG.SW in a real run) do not trigger the batch retry above; retry them once in small batches
    left = [t for t in tickers if t not in out]
    if left and len(tickers) > 1 and len(left) <= FINAL_RETRY_MAX:
        time.sleep(FINAL_RETRY_PAUSE_S)
        for j in range(0, len(left), 10):
            out.update(_download_chunk(left[j : j + 10], start, actions))
    return out


def _download_chunk(chunk: list[str], start: str, actions: bool) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}

    def _dl():
        return yf().download(
            chunk, start=start, auto_adjust=False, actions=actions, group_by="ticker",
            threads=False, progress=False, timeout=60,
        )

    try:
        df = _with_retries(_dl)
    except Exception:
        return out
    if df is None or df.empty:
        return out
    for t in chunk:
        try:
            if isinstance(df.columns, pd.MultiIndex):
                if t not in df.columns.get_level_values(0):
                    continue
                sub = df[t]
            else:
                sub = df
            sub = sub.rename(columns=str.lower)
            cols = {"close": "close", "adj close": "adj_close", "volume": "volume", "stock splits": "splits"}
            sub = sub[[c for c in cols if c in sub.columns]].rename(columns=cols)
            sub = sub.dropna(subset=["close"])
            if sub.empty:
                continue
            sub.index = pd.to_datetime(sub.index).tz_localize(None).normalize()
            out[t] = sub
        except Exception:
            continue
    return out


def splits(ticker: str, ttl_days: float = 7) -> list[tuple[str, float]]:
    cached = _read_cache("splits", ticker, ttl_days * 86400)
    if cached is not None:
        return [tuple(x) for x in cached.get("splits", [])]

    def _get():
        return yf().Ticker(ticker).splits

    try:
        s = _with_retries(_get)
    except Exception:
        return []
    res = []
    if s is not None and len(s):
        for d, r in s.items():
            if r and r > 0 and r != 1:
                res.append((pd.Timestamp(d).tz_localize(None).strftime("%Y-%m-%d"), float(r)))
    _write_cache("splits", ticker, {"splits": res})
    return res


# ---------------------------------------------------------------------------
# Statements (tier B fundamentals)
# ---------------------------------------------------------------------------
def _df_to_records(df: pd.DataFrame | None) -> dict[str, dict[str, float]]:
    if df is None or df.empty:
        return {}
    out: dict[str, dict[str, float]] = {}
    for col in df.columns:
        key = pd.Timestamp(col).strftime("%Y-%m-%d")
        vals = {}
        for row, v in df[col].items():
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            try:
                vals[str(row)] = float(v)
            except (TypeError, ValueError):
                continue
        out[key] = vals
    return out


def statements(ticker: str, ttl_days: float = 7) -> dict[str, Any] | None:
    cached = _read_cache("statements", ticker, ttl_days * 86400)
    if cached is not None and (not cached.get("_partial") or time.time() - cached.get("_fetched_at", 0) < 86400):
        return cached.get("statements")

    def _get():
        t = yf().Ticker(ticker)
        return {
            "income_annual": _df_to_records(t.income_stmt),
            "balance_annual": _df_to_records(t.balance_sheet),
            "cashflow_annual": _df_to_records(t.cashflow),
            "income_quarterly": _df_to_records(t.quarterly_income_stmt),
            "balance_quarterly": _df_to_records(t.quarterly_balance_sheet),
            "cashflow_quarterly": _df_to_records(t.quarterly_cashflow),
        }

    try:
        st = _with_retries(_get, retries=2)
    except Exception:
        return None
    if not any(st.values()):
        return None          # empty frames = no data OR silent rate limit (yfinance does not raise): never cached
    # yfinance swallows rate-limit errors per statement: a result with only some of the three annual statements
    # is used for this run but NOT cached, so it is downloaded again next time
    if all(st.get(k) for k in ("income_annual", "balance_annual", "cashflow_annual")):
        # quarterly statements all empty: possibly a rate limit later in the same call, or a company that does
        # not publish quarterly data → cached only for a day
        partial = not any(st.get(k) for k in ("income_quarterly", "balance_quarterly", "cashflow_quarterly"))
        _write_cache("statements", ticker, {"statements": st, "_partial": partial})
    return st


def fx_history(pair: str, start: str = "2014-01-01") -> pd.Series:
    """Yahoo FX, e.g. 'EURTWD=X' = TWD per 1 EUR. Used only for currencies the ECB does not publish."""
    data = download_prices([pair], start=start, batch=1)
    df = data.get(pair)
    if df is None:
        return pd.Series(dtype=float)
    return df["close"]


def iter_chunks(seq: Iterable[Any], n: int):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
