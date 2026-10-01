"""Investable-universe construction.

Definition (documented in the UI):
  A stock is in the investable universe if it is
   1. an operating company's common equity (no ETFs/funds, SPACs, preferreds, warrants, units);
   2. listed on a major regulated exchange reachable by EU retail brokers
      (US: NYSE / Nasdaq; International: constituents of major national indices);
   3. liquid: median daily traded value over ~3 months ≥ threshold (default 2M USD);
   4. above a market-cap floor that depends on the mode (quick / standard / full);
   5. one listing per company (ADR vs home listing de-duplicated; SEC filings preferred).
Stocks failing a step are recorded with the reason (table companies.exclusion_reason).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import classify
from .config import load_config
from .sources import sec, universe_intl, yahoo
from .sources.fx_macro import FxTable

US_EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSE MKT"}
# US companies with a public float below this share of the market-cap threshold are dropped before any price
# download. Decision (real run of 2026-10-01): 0.10 keeps every company whose float is within 10x of the threshold
# (covers controlled companies with a small free float and a 15-month-old measurement); in quick mode it removes
# floats < 2 bn $.
PUBLIC_FLOAT_PREFILTER = 0.10
BAD_TICKER = re.compile(r"(-P[A-Z]?$|-W$|-WS$|-WT$|-U$|-UN$|-R$|-RT$|\^)")


def to_yahoo_us(ticker: str) -> str:
    return ticker.replace(".", "-").upper()


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    n = name.lower()
    n = re.sub(r"[\.,&'\-\(\)/]", " ", n)
    stop = {"inc", "corp", "corporation", "co", "company", "plc", "ag", "sa", "se", "nv", "n", "v", "spa", "s", "p",
            "a", "ltd", "limited", "holding", "holdings", "group", "the", "ab", "asa", "oyj", "as", "kgaa", "adr",
            "ads", "sponsored", "de", "cv", "bhd", "tbk", "publ", "class", "ordinary", "shares", "and"}
    toks = [t for t in n.split() if t not in stop]
    return " ".join(toks)


@dataclass
class Candidate:
    ticker: str
    source: str                       # 'SEC' | 'INTL'
    cik: str | None = None
    sec_name: str | None = None
    exchange: str | None = None
    index_membership: list[str] = field(default_factory=list)
    price: float | None = None
    price_currency: str | None = None
    median_dollar_volume_usd: float | None = None
    market_cap_usd: float | None = None
    info: dict[str, Any] | None = None
    exclusion: str | None = None
    forced: bool = False              # added by the user (watchlist / portfolio / CLI): skip size & liquidity filters

    @property
    def company_id(self) -> str:
        return f"CIK{self.cik}" if self.cik else f"YF:{self.ticker}"


def _liquidity(prices: dict[str, pd.DataFrame], tickers: list[str], fx: FxTable,
               currencies: dict[str, str] | None = None) -> dict[str, tuple[float | None, float | None]]:
    """ticker → (last_price_main_unit, median_dollar_volume_usd). Currency from Yahoo meta if provided."""
    out = {}
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty:
            out[t] = (None, None)
            continue
        df = df.iloc[-66:]
        cur_raw = (currencies or {}).get(t, "USD")
        cur, div = yahoo.normalize_currency(cur_raw)
        px = df["close"] / div
        dv = (px * df["volume"]).median()
        dv_usd = fx.convert(float(dv), cur, "USD") if cur and pd.notna(dv) else None
        out[t] = (float(px.iloc[-1]), dv_usd)
    return out


def build_universe(fx: FxTable, mode: str, price_cache, progress: Callable[[str], None] = print,
                   extra_tickers: list[str] | None = None, limit: int | None = None) -> tuple[list[Candidate], dict[str, Any]]:
    cfg = load_config()
    min_dv = float(cfg.get("universe.min_median_dollar_volume_usd", 2e6))
    min_mcap = cfg.min_market_cap_usd(mode)
    report: dict[str, Any] = {"mode": mode, "min_market_cap_usd": min_mcap, "min_median_dollar_volume_usd": min_dv,
                              "steps": [], "notes": []}
    cands: list[Candidate] = []

    # ------------------------------------------------------------------ US (SEC registrants)
    rows = []
    if cfg.get("universe.include_us", True):
        try:
            rows = sec.company_tickers()
        except Exception as e:  # noqa: BLE001  (missing SEC contact, SEC offline...)
            msg = (f"USA (SEC) SALTATI: {e}. L'analisi prosegue solo con i mercati internazionali; "
                   f"i titoli USA aggiunti a mano useranno i dati Yahoo (livello B).")
            report["steps"].append(msg)
            report["notes"].append(msg)
            report["us_skipped"] = str(e)
            progress("   ATTENZIONE: " + msg)
    if rows:
        report["steps"].append(f"SEC: {len(rows)} ticker registrati")
        seen_cik: set[str] = set()
        for r in rows:
            if r.get("exchange") not in US_EXCHANGES:
                continue
            t = to_yahoo_us(r["ticker"])
            if BAD_TICKER.search(t):
                continue
            if r["cik"] in seen_cik:      # keep first (primary) ticker per company
                continue
            seen_cik.add(r["cik"])
            cands.append(Candidate(ticker=t, source="SEC", cik=r["cik"], sec_name=r.get("name"), exchange=r.get("exchange")))
        report["steps"].append(f"SEC: {len(cands)} società su NYSE/Nasdaq (1 ticker per società)")

    # ------------------------------------------------------------------ International
    if cfg.get("universe.include_international", True):
        intl, notes = universe_intl.international_candidates()
        report["notes"].extend(notes)
        for _, r in intl.iterrows():
            cands.append(Candidate(ticker=r["ticker"], source="INTL", index_membership=list(r["index"])))
        report["steps"].append(f"Internazionale: {len(intl)} titoli candidati da indici principali")

    by_ticker = {c.ticker: c for c in cands}
    for t in extra_tickers or []:
        t = t.strip().upper()
        if not t:
            continue
        if t in by_ticker:
            by_ticker[t].forced = True
        else:
            c = Candidate(ticker=t, source="INTL", index_membership=["aggiunto dall'utente"], forced=True)
            cands.append(c)
            by_ticker[t] = c

    if limit:
        cands = [c for c in cands if c.forced] + [c for c in cands if not c.forced][:limit]

    # ------------------------------------------------------------------ US pre-filter on SEC public float
    # (real run: 6033 SEC companies, mostly small; downloading their prices only to discard them took ~20 min)
    if any(c.source == "SEC" for c in cands):
        floats = _frames_public_float()
        if floats:
            n = public_float_prefilter(cands, floats, min_mcap)
            report["steps"].append(f"Pre-filtro flottante SEC (dei:EntityPublicFloat): {n} società USA escluse "
                                   f"prima di scaricare i prezzi")

    # ------------------------------------------------------------------ liquidity (bulk prices, 3 months)
    todo = [c for c in cands if not c.exclusion]
    progress(f"Controllo liquidità di {len(todo)} titoli (prezzi ultimi 3 mesi)…")
    start = (pd.Timestamp.today() - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    prices = price_cache.get_recent([c.ticker for c in todo], start=start)
    # currency unknown before `info`; US tickers are USD, international resolved after info
    for c in todo:
        df = prices.get(c.ticker)
        if df is None or df.empty:
            c.exclusion = "nessun prezzo su Yahoo (ticker non valido, delistato o non supportato)"
            continue
        if (pd.Timestamp.today() - df.index.max()).days > 10:
            c.exclusion = f"ultimo prezzo vecchio ({df.index.max().date()}): possibile sospensione/delisting"
            continue
    alive = [c for c in cands if not c.exclusion]
    report["steps"].append(f"Con prezzi recenti: {len(alive)}")

    # US: currency USD → liquidity now; international needs currency from info
    us = [c for c in alive if c.source == "SEC"]
    liq = _liquidity(prices, [c.ticker for c in us], fx)
    for c in us:
        c.price, c.median_dollar_volume_usd = liq[c.ticker]
        c.price_currency = "USD"
        if (c.median_dollar_volume_usd is None or c.median_dollar_volume_usd < min_dv) and not c.forced:
            c.exclusion = f"liquidità insufficiente (mediana {_fmt_usd(c.median_dollar_volume_usd)}/giorno)"

    # US pre-filter on market cap using SEC frames (one request per quarter for all companies)
    us_alive = [c for c in us if not c.exclusion]
    shares = _frames_shares()
    if shares:
        for c in us_alive:
            sh = shares.get(int(c.cik))
            if sh and c.price and not c.forced:
                est = sh * c.price
                if est < 0.5 * min_mcap:
                    c.exclusion = f"capitalizzazione stimata {_fmt_usd(est)} sotto la soglia"
        report["steps"].append("Pre-filtro capitalizzazione USA con SEC frames (azioni in circolazione)")

    # ------------------------------------------------------------------ Yahoo info for survivors
    need_info = [c for c in cands if not c.exclusion]
    progress(f"Metadati Yahoo per {len(need_info)} titoli (settore, valuta, capitalizzazione)…")
    for i, c in enumerate(need_info):
        if i and i % 100 == 0:
            progress(f"  metadati {i}/{len(need_info)}")
        c.info = yahoo.info(c.ticker)
        if not c.info:
            c.exclusion = "metadati Yahoo non disponibili"
            continue
        qt = c.info.get("quoteType")
        if qt and qt != "EQUITY":
            c.exclusion = f"non è un'azione ordinaria (Yahoo quoteType={qt})"
            continue
        cur, divisor = yahoo.normalize_currency(c.info.get("currency"))
        c.price_currency = cur
        if c.source == "INTL":
            p, dv = _liquidity(prices, [c.ticker], fx, {c.ticker: c.info.get("currency")})[c.ticker]
            c.price, c.median_dollar_volume_usd = p, dv
            if (dv is None or dv < min_dv) and not c.forced:
                c.exclusion = f"liquidità insufficiente (mediana {_fmt_usd(dv)}/giorno)"
                continue
        mcap_local = _yahoo_mcap_main_unit(c.info, c.price)
        c.market_cap_usd = fx.convert(mcap_local, cur, "USD") if (mcap_local and cur) else None
        if c.market_cap_usd is None:
            c.exclusion = "capitalizzazione non determinabile"
        elif c.market_cap_usd < min_mcap and not c.forced:
            c.exclusion = f"capitalizzazione {_fmt_usd(c.market_cap_usd)} sotto la soglia {_fmt_usd(min_mcap)}"

    # ------------------------------------------------------------------ de-duplicate listings
    kept = [c for c in cands if not c.exclusion]
    _dedupe(kept)
    final = [c for c in kept if not c.exclusion]
    report["steps"].append(f"Universo finale: {len(final)} società")
    report["excluded_counts"] = pd.Series([_reason_key(c.exclusion) for c in cands if c.exclusion]).value_counts().to_dict()
    return cands, report


def _reason_key(r: str | None) -> str:
    if not r:
        return ""
    return r.split("(")[0].strip()


def _fmt_usd(x: float | None) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/d"
    for unit, div in (("mld $", 1e9), ("mln $", 1e6)):
        if abs(x) >= div:
            return f"{x / div:.1f} {unit}"
    return f"{x:,.0f} $"


def _yahoo_mcap_main_unit(info: dict[str, Any], price_main: float | None) -> float | None:
    """Market cap in the main currency unit. Uses price × shares (consistent units); the
    reported marketCap is only a fallback (its unit for GBp/ZAc listings is inconsistent)."""
    sh = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
    if price_main and sh:
        mc = price_main * sh
        rep = info.get("marketCap")
        if rep and rep > 0:
            ratio = rep / mc
            # accept reported cap when it agrees; if 100x off it's a pence issue → keep computed
            if 0.8 < ratio < 1.25:
                return float(rep)
        return float(mc)
    rep = info.get("marketCap")
    if rep and price_main and info.get("currency") in yahoo.SUBUNIT_CURRENCIES:
        return None   # unit ambiguous without share count
    return float(rep) if rep else None


def _frames_latest(concept: str, unit: str, quarters: int) -> dict[int, float]:
    """Latest value of a dei: cover-page fact per CIK, from the last `quarters` quarterly instant frames."""
    today = pd.Timestamp.today()
    out: dict[int, tuple[str, float]] = {}
    for back in range(1, quarters + 1):
        q = (today - pd.DateOffset(months=3 * back))
        period = f"CY{q.year}Q{(q.month - 1) // 3 + 1}I"
        try:
            data = sec.frame("dei", concept, unit, period)
        except Exception:  # noqa: BLE001
            data = []
        for d in data:
            cik = int(d.get("cik", 0))
            end = d.get("end", "")
            if cik and (cik not in out or end > out[cik][0]):
                out[cik] = (end, float(d.get("val", 0)))
    return {k: v[1] for k, v in out.items()}


def _frames_shares() -> dict[int, float]:
    """Latest dei:EntityCommonStockSharesOutstanding per CIK from the last 4 quarterly frames."""
    return _frames_latest("EntityCommonStockSharesOutstanding", "shares", 4)


def _frames_public_float() -> dict[int, float]:
    """Latest dei:EntityPublicFloat (USD, measured at the end of the 2nd fiscal quarter, reported in the 10-K)
    per CIK. Six quarters back: in October the latest value of a December filer is still the one of June
    of the PREVIOUS year."""
    return _frames_latest("EntityPublicFloat", "USD", 6)


def public_float_prefilter(cands: list[Candidate], floats: dict[int, float], min_mcap: float) -> int:
    """Excludes US candidates whose public float is far below the market-cap threshold, BEFORE any Yahoo request.

    The float is at most the market cap and can be up to ~15 months old, so the cut is deliberately loose
    (PUBLIC_FLOAT_PREFILTER × threshold): a company is dropped only if even a large rise and a small free float
    could not bring it near the threshold. Missing or non-positive floats are kept (decided later on real
    prices). Returns the number of exclusions."""
    n = 0
    for c in cands:
        if c.source != "SEC" or c.forced or c.exclusion or not c.cik:
            continue
        fl = floats.get(int(c.cik))
        if fl and fl > 0 and fl < PUBLIC_FLOAT_PREFILTER * min_mcap:
            c.exclusion = f"flottante dichiarato alla SEC {_fmt_usd(fl)}: capitalizzazione certamente sotto la soglia"
            n += 1
    return n


def _dedupe(cands: list[Candidate]) -> None:
    """Same company listed twice (ADR + home listing, or US + Canada): keep the SEC-filing
    listing (better data), record the other as an alternate listing."""
    by_key: dict[tuple[str, str], list[Candidate]] = {}
    for c in cands:
        name = normalize_name((c.info or {}).get("longName") or (c.info or {}).get("shortName") or c.sec_name)
        country = (c.info or {}).get("country") or ""
        if not name:
            continue
        by_key.setdefault((name, country), []).append(c)
    for (name, country), group in by_key.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda c: (0 if c.source == "SEC" else 1, -(c.median_dollar_volume_usd or 0)))
        primary = group[0]
        for other in group[1:]:
            other.exclusion = f"doppia quotazione di {primary.ticker} (stessa società)"
            primary.index_membership = sorted(set(primary.index_membership) | set(other.index_membership))
            setattr(primary, "alt_listings", getattr(primary, "alt_listings", []) + [other.ticker])


def candidate_rows(cands: list[Candidate]) -> list[dict[str, Any]]:
    from .db import now_iso

    rows = []
    for c in cands:
        info = c.info or {}
        rows.append({
            "company_id": c.company_id,
            "ticker": c.ticker,
            "name": info.get("longName") or info.get("shortName") or c.sec_name,
            "cik": c.cik,
            "exchange": info.get("fullExchangeName") or c.exchange,
            "country": info.get("country"),
            "region": classify.region_for(info.get("country"), c.ticker),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "price_currency": c.price_currency,
            "other_listings": getattr(c, "alt_listings", []),
            "index_membership": c.index_membership,
            "in_universe": 0 if c.exclusion else 1,
            "exclusion_reason": c.exclusion,
            "description": info.get("longBusinessSummary"),
            "description_source": "Yahoo Finance" if info.get("longBusinessSummary") else None,
            "liquidity_usd": c.median_dollar_volume_usd,
            "market_cap_usd": c.market_cap_usd,
            "forced": 1 if c.forced else 0,
            "updated_at": now_iso(),
        })
    return rows
