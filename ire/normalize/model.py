"""Standardized financials container shared by the SEC and Yahoo normalizers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Financials:
    company_id: str
    currency: str | None
    source: str                              # 'SEC XBRL companyfacts' | 'Yahoo Finance'
    tier: str                                # 'A' | 'B'
    annual: pd.DataFrame                     # index: period_end (Timestamp), columns: items
    ttm: dict[str, float] = field(default_factory=dict)
    ttm_end: pd.Timestamp | None = None
    ttm_derivation: str = ""
    latest: dict[str, tuple[float, pd.Timestamp]] = field(default_factory=dict)   # latest instants
    fact_rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)   # data-quality flags
    last_filed: str | None = None
    fiscal_year_end: str | None = None       # 'MM-DD'
    assumed_zero: set[str] = field(default_factory=set)
    gaap: str = ""                           # 'US GAAP' | 'IFRS' | '' (unknown, e.g. Yahoo)
    debt_has_leases: bool = False            # US GAAP debt concept already includes finance leases
    add_leases: bool = False                 # lease liabilities added to financial debt

    @property
    def years(self) -> int:
        return int(self.annual.shape[0])

    def series(self, item: str) -> pd.Series:
        if item not in self.annual.columns:
            return pd.Series(dtype=float)
        return self.annual[item].dropna()

    def last(self, item: str) -> float | None:
        s = self.series(item)
        return float(s.iloc[-1]) if len(s) else None

    def ttm_or_last(self, item: str) -> float | None:
        v = self.ttm.get(item)
        if v is not None and pd.notna(v):
            return float(v)
        return self.last(item)

    def latest_instant(self, item: str) -> float | None:
        if item in self.latest:
            return float(self.latest[item][0])
        if item not in self.annual.columns or self.annual.empty:     # last fiscal year only, never an older one
            return None
        v = self.annual[item].iloc[-1]
        return float(v) if pd.notna(v) else None

    @property
    def latest_period_end(self) -> pd.Timestamp | None:
        cands = []
        if len(self.annual.index):
            cands.append(self.annual.index.max())
        if self.ttm_end is not None:
            cands.append(self.ttm_end)
        return max(cands) if cands else None
