"""SQLite storage. Every stored number keeps its provenance.

Design principles
-----------------
* ``facts``      : normalized financial line items, one row per (company, item, period).
                   Carries source, filing accession, filed date, original XBRL concept,
                   and whether the value was restated later.
* ``metrics``    : every computed metric per run, with kind
                   (observed | calculated | estimate | third_party_estimate),
                   method description and the inputs used.
* ``scores``     : pillar scores / classification per run (snapshots → diffs over time).
* ``flags``      : red flags and data-quality warnings, with evidence and source.
* ``conflicts``  : disagreements between sources for the same data point.
Nothing is imputed: a missing value stays NULL.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import load_config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS companies (
    company_id      TEXT PRIMARY KEY,      -- stable id: 'CIK0000320193' or 'YF:ENI.MI'
    ticker          TEXT NOT NULL,         -- primary trading ticker (Yahoo notation)
    name            TEXT,
    cik             TEXT,
    exchange        TEXT,
    country         TEXT,
    region          TEXT,
    sector          TEXT,
    industry        TEXT,
    sic             TEXT,
    sic_description TEXT,
    price_currency  TEXT,
    fin_currency    TEXT,
    filer_type      TEXT,                  -- 'domestic' (10-K) | 'foreign' (20-F/40-F) | 'non_sec'
    data_tier       TEXT,                  -- 'A' SEC XBRL | 'B' Yahoo statements
    is_banklike     INTEGER DEFAULT 0,
    is_reit         INTEGER DEFAULT 0,
    other_listings  TEXT,                  -- JSON list of alternate tickers
    index_membership TEXT,                 -- JSON list
    in_universe     INTEGER DEFAULT 1,
    exclusion_reason TEXT,
    description     TEXT,
    description_source TEXT,
    liquidity_usd   REAL,                  -- median daily traded value (3 months, USD)
    market_cap_usd  REAL,
    forced          INTEGER DEFAULT 0,     -- added by the user (watchlist/portfolio): size/liquidity filters bypassed
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_companies_ticker ON companies(ticker);

CREATE TABLE IF NOT EXISTS facts (
    company_id   TEXT NOT NULL,
    item         TEXT NOT NULL,            -- standardized line item, e.g. 'revenue'
    period_type  TEXT NOT NULL,            -- 'FY' | 'TTM' | 'INSTANT'
    period_start TEXT,
    period_end   TEXT NOT NULL,
    value        REAL,
    unit         TEXT,                     -- currency code, 'shares', 'CUR/shares'
    source       TEXT NOT NULL,            -- 'SEC XBRL companyfacts' | 'Yahoo Finance'
    source_ref   TEXT,                     -- accession number or endpoint
    concept      TEXT,                     -- original XBRL concept / Yahoo row name
    form         TEXT,
    filed        TEXT,
    derivation   TEXT,                     -- how the value was obtained if not reported directly
    restated     INTEGER DEFAULT 0,        -- 1 if later filing changed the value
    original_value REAL,                   -- first reported value (point-in-time)
    split_adjusted INTEGER DEFAULT 0,
    fetched_at   TEXT,
    PRIMARY KEY (company_id, item, period_type, period_end)
);

CREATE TABLE IF NOT EXISTS prices (
    ticker   TEXT NOT NULL,
    date     TEXT NOT NULL,
    close    REAL,          -- split-adjusted close (not dividend adjusted), RAW Yahoo units
    adj_close REAL,         -- split + dividend adjusted (total return proxy), RAW Yahoo units
                            -- (GBp/ZAc/ILA: divide by price_meta.divisor to get GBP/ZAR/ILS)
    volume   REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS price_meta (
    ticker      TEXT PRIMARY KEY,
    currency    TEXT,        -- as reported by Yahoo (GBp, ZAc... preserved)
    divisor     REAL,        -- 100 for GBp/ZAc/ILA → prices stored in main unit
    first_date  TEXT,
    last_date   TEXT,
    requested_start TEXT,
    fetched_at  TEXT,        -- last download of any kind (recent or full)
    full_fetched_at REAL     -- epoch of the last FULL (re-adjusted, with splits) download
);

CREATE TABLE IF NOT EXISTS splits (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    ratio   REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS fx (
    date      TEXT NOT NULL,
    currency  TEXT NOT NULL,
    per_eur   REAL NOT NULL,     -- units of currency per 1 EUR
    source    TEXT,
    PRIMARY KEY (date, currency)
);

CREATE TABLE IF NOT EXISTS macro (
    series  TEXT NOT NULL,
    date    TEXT NOT NULL,
    value   REAL,
    source  TEXT,
    PRIMARY KEY (series, date)
);

CREATE TABLE IF NOT EXISTS market_data (
    company_id  TEXT PRIMARY KEY,
    ticker      TEXT,
    as_of       TEXT,
    price       REAL,            -- in price_currency main unit
    price_currency TEXT,
    market_cap_yahoo REAL,       -- as reported by Yahoo, in price_currency
    shares_yahoo REAL,
    forward_eps REAL,            -- analyst consensus (third-party estimate)
    trailing_eps_yahoo REAL,
    dividend_rate REAL,
    beta_yahoo REAL,
    peg_yahoo  REAL,
    quote_type TEXT,
    raw_json   TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS filings (
    company_id  TEXT NOT NULL,
    accession   TEXT NOT NULL,
    form        TEXT,
    filed       TEXT,
    report_date TEXT,
    items       TEXT,
    primary_doc TEXT,
    url         TEXT,
    PRIMARY KEY (company_id, accession)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    mode       TEXT,
    status     TEXT,
    params     TEXT,
    summary    TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    run_id     INTEGER NOT NULL,
    company_id TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL,
    kind       TEXT,        -- observed | calculated | estimate | third_party_estimate | assumption
    period     TEXT,
    method     TEXT,
    inputs     TEXT,        -- JSON
    PRIMARY KEY (run_id, company_id, metric)
);
CREATE INDEX IF NOT EXISTS ix_metrics_company ON metrics(company_id);

CREATE TABLE IF NOT EXISTS scores (
    run_id      INTEGER NOT NULL,
    company_id  TEXT NOT NULL,
    quality     REAL,
    growth      REAL,
    financial_strength REAL,
    valuation   REAL,
    capital_allocation REAL,
    composite   REAL,
    robust_score REAL,
    robust_rank  INTEGER,
    rank_spread  REAL,
    coverage     REAL,
    confidence   TEXT,
    classification TEXT,
    valuation_verdict TEXT,
    valuation_confidence TEXT,
    peer_group   TEXT,
    detail       TEXT,       -- JSON: per-metric percentiles, signals, thesis
    PRIMARY KEY (run_id, company_id)
);

CREATE TABLE IF NOT EXISTS flags (
    run_id     INTEGER NOT NULL,
    company_id TEXT NOT NULL,
    code       TEXT NOT NULL,
    severity   TEXT NOT NULL,   -- severe | high | medium | info | data
    message    TEXT,
    evidence   TEXT,
    source     TEXT
);
CREATE INDEX IF NOT EXISTS ix_flags ON flags(run_id, company_id);

CREATE TABLE IF NOT EXISTS conflicts (
    run_id     INTEGER NOT NULL,
    company_id TEXT NOT NULL,
    item       TEXT,
    period_end TEXT,
    value_a    REAL, source_a TEXT,
    value_b    REAL, source_b TEXT,
    pct_diff   REAL,
    likely_reason TEXT
);

CREATE TABLE IF NOT EXISTS portfolios (
    run_id   INTEGER NOT NULL,
    name     TEXT NOT NULL,
    payload  TEXT,
    PRIMARY KEY (run_id, name)
);

CREATE TABLE IF NOT EXISTS watchlist (
    ticker  TEXT PRIMARY KEY,
    added_at TEXT,
    note    TEXT
);

CREATE TABLE IF NOT EXISTS user_portfolio (
    ticker  TEXT PRIMARY KEY,
    weight  REAL,
    note    TEXT
);

CREATE TABLE IF NOT EXISTS qualitative (
    company_id TEXT PRIMARY KEY,
    as_of      TEXT,
    payload    TEXT    -- JSON: text flags, risk-factor diff, sources
);

CREATE TABLE IF NOT EXISTS log (
    ts      TEXT,
    run_id  INTEGER,
    level   TEXT,
    stage   TEXT,
    message TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or load_config().db_path
    con = sqlite3.connect(str(p), timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(path: Path | None = None) -> sqlite3.Connection:
    con = connect(path)
    con.executescript(SCHEMA)
    con.commit()
    return con


@contextmanager
def transaction(con: sqlite3.Connection):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def upsert_merge(con: sqlite3.Connection, table: str, key: str, rows: Iterable[dict[str, Any]]) -> int:
    """INSERT, or UPDATE only the provided columns when the key exists (other columns are kept)."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sets = ",".join(f"{c}=excluded.{c}" for c in cols if c != key)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT({key}) DO UPDATE SET {sets}"
    con.executemany(sql, [tuple(_clean(r.get(c)) for c in cols) for r in rows])
    return len(rows)


def upsert(con: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    con.executemany(sql, [tuple(_clean(r.get(c)) for c in cols) for r in rows])
    return len(rows)


def _clean(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    try:
        import math

        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:  # pragma: no cover
        pass
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return _clean(v.item())
        except Exception:
            return v
    return v


def log(con: sqlite3.Connection, run_id: int | None, level: str, stage: str, message: str) -> None:
    con.execute(
        "INSERT INTO log (ts, run_id, level, stage, message) VALUES (?,?,?,?,?)",
        (now_iso(), run_id, level, stage, message[:4000]),
    )
    con.commit()


def latest_run_id(con: sqlite3.Connection, status: str | None = "completed") -> int | None:
    if status:
        r = con.execute(
            "SELECT run_id FROM runs WHERE status=? ORDER BY run_id DESC LIMIT 1", (status,)
        ).fetchone()
    else:
        r = con.execute("SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    return int(r[0]) if r else None
