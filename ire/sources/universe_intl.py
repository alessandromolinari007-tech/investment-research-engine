"""International universe: constituents of major, liquid indices.

Primary source: Wikipedia index pages (refreshed each run, free).
Fallback: a bundled seed list (ire/data/international_seed.csv) compiled from index
compositions known as of mid-2026. EVERY ticker, from either source, is validated
at runtime against Yahoo Finance (must exist, be an equity and have a market cap);
invalid/delisted tickers are reported, never silently kept.

Why indices and not "every listed stock"? No free source offers a clean, complete
list of all European/Asian listings with reliable fundamentals. Large-/mid-cap index
members are (a) buyable at mainstream EU brokers, (b) liquid, (c) covered by Yahoo.
This is a documented bias toward large caps outside the US.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..http import SourceUnavailable, get_client

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "international_seed.csv"


@dataclass
class IndexDef:
    name: str
    url: str
    suffix: str
    expected: int
    kind: str = "alpha"      # alpha | numeric4 | hk


INDICES = [
    IndexDef("FTSE MIB", "https://en.wikipedia.org/wiki/FTSE_MIB", ".MI", 40),
    IndexDef("DAX", "https://en.wikipedia.org/wiki/DAX", ".DE", 40),
    IndexDef("CAC 40", "https://en.wikipedia.org/wiki/CAC_40", ".PA", 40),
    IndexDef("AEX", "https://en.wikipedia.org/wiki/AEX_index", ".AS", 25),
    IndexDef("IBEX 35", "https://en.wikipedia.org/wiki/IBEX_35", ".MC", 35),
    IndexDef("SMI", "https://en.wikipedia.org/wiki/Swiss_Market_Index", ".SW", 20),
    IndexDef("FTSE 100", "https://en.wikipedia.org/wiki/FTSE_100_Index", ".L", 100),
    IndexDef("OMX Stockholm 30", "https://en.wikipedia.org/wiki/OMX_Stockholm_30", ".ST", 30),
    IndexDef("OMX Copenhagen 25", "https://en.wikipedia.org/wiki/OMX_Copenhagen_25", ".CO", 25),
    IndexDef("OMX Helsinki 25", "https://en.wikipedia.org/wiki/OMX_Helsinki_25", ".HE", 25),
    IndexDef("Nikkei 225", "https://en.wikipedia.org/wiki/Nikkei_225", ".T", 225, "numeric4"),
    IndexDef("S&P/TSX 60", "https://en.wikipedia.org/wiki/S%26P/TSX_60", ".TO", 60),
    IndexDef("S&P/ASX 200", "https://en.wikipedia.org/wiki/S%26P/ASX_200", ".AX", 200),
    IndexDef("Hang Seng", "https://en.wikipedia.org/wiki/Hang_Seng_Index", ".HK", 80, "hk"),
]

SYMBOL_COL = re.compile(r"(ticker|symbol|epic|code|stock\s*code|securities\s*code)", re.I)


NORDIC_SUFFIXES = (".CO", ".ST", ".HE", ".OL")


def _clean_symbol(raw: str, idx: IndexDef) -> str | None:
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    s = re.sub(r"\[.*?\]", "", s)             # footnotes
    s = s.split(":")[-1].strip()              # 'XETRA: SAP' -> 'SAP'
    parts = s.split()
    if idx.suffix in NORDIC_SUFFIXES and len(parts) >= 2 and re.fullmatch(r"[A-Z]{1,3}", parts[1].upper()):
        s = f"{parts[0]}-{parts[1]}"           # Nordic share class: 'NOVO B' → 'NOVO-B' (Yahoo NOVO-B.CO)
    else:
        s = parts[0] if parts else s
    if idx.kind == "numeric4":
        # TSE codes: 4 digits, or (since 2024) alphanumeric like "285A" (letters B E I O Q V Z are not used)
        m = re.search(r"(?<![0-9A-Z])[0-9][0-9ACDFGHJKLMNPRSTUWXY][0-9][0-9ACDFGHJKLMNPRSTUWXY](?![0-9A-Z])", s.upper())
        return f"{m.group(0)}{idx.suffix}" if m else None
    if idx.kind == "hk":
        m = re.search(r"\d{1,5}", s)
        return f"{int(m.group(0)):04d}{idx.suffix}" if m else None
    up = s.upper()
    if up.endswith(idx.suffix.upper()):
        base = up[: -len(idx.suffix)]
    else:
        # strip any other trailing exchange suffix like '.L' when idx suffix is '.L'
        base = up
    base = base.replace("/", "-")
    if idx.suffix in (".L", ".TO", ".ST", ".CO", ".HE"):
        base = base.replace(".", "-")        # BT.A -> BT-A (Yahoo notation)
    base = base.strip(".-")
    if not re.fullmatch(r"[A-Z0-9\-&]{1,12}", base):
        return None
    return f"{base}{idx.suffix}"


def scrape_index(idx: IndexDef) -> list[str]:
    try:
        html = get_client().get(idx.url, ttl_seconds=7 * 86400).text
        tables = pd.read_html(io.StringIO(html))
    except (SourceUnavailable, ValueError, ImportError):
        return []
    best: list[str] = []
    for t in tables:
        cols = [str(c if not isinstance(c, tuple) else c[-1]) for c in t.columns]
        for i, c in enumerate(cols):
            if SYMBOL_COL.search(c):
                syms = [_clean_symbol(v, idx) for v in t.iloc[:, i].tolist()]
                syms = [s for s in syms if s]
                if len(syms) > len(best):
                    best = syms
    # sanity: accept only if close to the expected size
    if len(best) < 0.6 * idx.expected or len(best) > 1.6 * idx.expected:
        return []
    return list(dict.fromkeys(best))


def load_seed() -> pd.DataFrame:
    df = pd.read_csv(SEED_PATH)
    df["source"] = "seed list (index compositions as of mid-2026, validated at runtime)"
    return df


def international_candidates(use_wikipedia: bool = True) -> tuple[pd.DataFrame, list[str]]:
    """Returns (DataFrame[ticker, index, source], notes)."""
    notes: list[str] = []
    frames = []
    if SEED_PATH.exists():
        frames.append(load_seed())
    else:
        notes.append(f"ATTENZIONE: lista seed {SEED_PATH.name} non trovata: titoli internazionali solo dagli indici "
                     "di Wikipedia (gli indici non raggiungibili restano scoperti)")
    if use_wikipedia:
        for idx in INDICES:
            syms = scrape_index(idx)
            if syms:
                frames.append(pd.DataFrame({"ticker": syms, "index": idx.name, "source": f"Wikipedia: {idx.url}"}))
                notes.append(f"{idx.name}: {len(syms)} titoli da Wikipedia")
            else:
                notes.append(f"{idx.name}: Wikipedia non utilizzabile, uso lista seed"
                             + ("" if SEED_PATH.exists() else " (NON disponibile: indice scoperto)"))
    if not frames:
        return pd.DataFrame(columns=["ticker", "index", "source"]), notes
    df = pd.concat(frames, ignore_index=True)
    # keep one row per ticker; prefer Wikipedia label (fresher) over seed
    df["prio"] = df["source"].str.startswith("Wikipedia").map({True: 0, False: 1})
    df = df.sort_values("prio").groupby("ticker", as_index=False).agg(
        index=("index", lambda s: sorted(set(s))), source=("source", "first")
    )
    return df, notes
