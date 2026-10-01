"""SYNTHETIC offline world for the end-to-end pipeline test.

Nothing here is real market data: prices are seeded random walks and financial statements
are generated from simple rules. The goal is to exercise every pipeline stage offline
(parsing, currency handling incl. GBp, TTM, scoring, portfolio) with known structure.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tests.fixtures.builders import FactsBuilder

SECTORS = ["Technology", "Healthcare", "Industrials", "Consumer Defensive", "Financial Services", "Utilities"]
TODAY = pd.Timestamp.today().normalize()


class FakeWorld:
    def __init__(self, n_us=36, seed=7):
        self.rng = np.random.default_rng(seed)
        self.us = []
        for i in range(n_us):
            cik = f"{1000000 + i:010d}"
            sector = SECTORS[i % len(SECTORS)]
            self.us.append({"cik": cik, "ticker": f"US{i:02d}", "name": f"Synthetic US {i} Inc", "sector": sector,
                            "growth": 0.02 + 0.15 * self.rng.random(), "margin": 0.05 + 0.35 * self.rng.random(),
                            "shares": 1e9 * (0.5 + self.rng.random()), "bank": sector == "Financial Services"})
        # one foreign SEC filer (20-F, EUR, ADR ratio 5) and its home listing (duplicate)
        self.us.append({"cik": f"{2000000:010d}", "ticker": "FORX", "name": "Foreign Synthetic NV", "sector": "Technology",
                        "growth": 0.12, "margin": 0.3, "shares": 2e9, "bank": False, "foreign": True})
        self.intl = [
            {"ticker": "ITA1.MI", "name": "Sintetica Italiana SpA", "currency": "EUR", "sector": "Utilities", "country": "Italy"},
            {"ticker": "UKX1.L", "name": "Synthetic British plc", "currency": "GBp", "sector": "Consumer Defensive", "country": "United Kingdom"},
            {"ticker": "JPN1.T", "name": "Synthetic Japan KK", "currency": "JPY", "sector": "Industrials", "country": "Japan"},
            {"ticker": "FORX.AS", "name": "Foreign Synthetic NV", "currency": "EUR", "sector": "Technology", "country": "Netherlands"},
            {"ticker": "DEAD.MI", "name": "Delisted SpA", "currency": "EUR", "sector": "Industrials", "country": "Italy"},
        ]
        self.dates = pd.bdate_range("2013-01-01", TODAY)
        self.px = {}
        for t in [c["ticker"] for c in self.us] + [c["ticker"] for c in self.intl] + ["SWDA.MI", "IWDA.AS", "URTH"]:
            mu, sig = 0.08 / 252, (0.15 + 0.25 * self.rng.random()) / np.sqrt(252)
            r = self.rng.normal(mu, sig, len(self.dates))
            base = 50 + 100 * self.rng.random()
            if t == "UKX1.L":
                base = 2500.0  # pence
            if t == "JPN1.T":
                base = 3000.0
            p = base * np.exp(np.cumsum(r))
            df = pd.DataFrame({"close": p, "adj_close": p, "volume": 2e6 * (1 + self.rng.random(len(p)))}, index=self.dates)
            if t == "DEAD.MI":
                df = df[df.index < TODAY - pd.Timedelta(days=60)]
            self.px[t] = df

    # ------------------------------------------------------------------ SEC
    def company_tickers(self):
        return [{"cik": c["cik"], "name": c["name"], "ticker": c["ticker"], "exchange": "NYSE"} for c in self.us] + [
            {"cik": "0000999999", "name": "Tiny OTC", "ticker": "OTCX", "exchange": "OTC"}]

    def _c(self, cik):
        return next(c for c in self.us if c["cik"] == cik)

    def submissions(self, cik):
        c = self._c(cik)
        foreign = c.get("foreign")
        recent = {"accessionNumber": [], "filingDate": [], "reportDate": [], "form": [], "items": [], "primaryDocument": [],
                  "primaryDocDescription": []}
        for y in range(TODAY.year - 1, TODAY.year - 4, -1):
            recent["accessionNumber"].append(f"0000000000-{str(y)[2:]}-00000{y % 10}")
            recent["filingDate"].append(f"{y}-02-20")
            recent["reportDate"].append(f"{y - 1}-12-31")
            recent["form"].append("20-F" if foreign else "10-K")
            recent["items"].append("")
            recent["primaryDocument"].append(f"doc{y}.htm")
            recent["primaryDocDescription"].append("")
        if c["ticker"] == "US05":
            recent["accessionNumber"].append("0000000000-25-000777")
            recent["filingDate"].append((TODAY - pd.Timedelta(days=100)).strftime("%Y-%m-%d"))
            recent["reportDate"].append("")
            recent["form"].append("8-K")
            recent["items"].append("4.02,9.01")
            recent["primaryDocument"].append("8k.htm")
            recent["primaryDocDescription"].append("")
        sic = "6022" if c["bank"] else "7372" if c["sector"] == "Technology" else "4911" if c["sector"] == "Utilities" else "2834"
        return {"cik": cik, "name": c["name"], "sic": sic, "sicDescription": "synthetic", "filings": {"recent": recent}}

    def companyfacts(self, cik):
        c = self._c(cik)
        foreign = c.get("foreign")
        tax = "ifrs-full" if foreign else "us-gaap"
        cur = "EUR" if foreign else "USD"
        form = "20-F" if foreign else "10-K"
        b = FactsBuilder(name=c["name"], cik=int(cik))
        last_fy = TODAY.year - 1
        years = list(range(last_fy - 8, last_fy + 1))
        rev0 = 5e9 * (1 + self.rng.random())
        rev = {y: rev0 * (1 + c["growth"]) ** (y - years[0]) for y in years}
        m = c["margin"]
        names = {
            "us-gaap": dict(rev="Revenues", cogs="CostOfRevenue", oi="OperatingIncomeLoss", ni="NetIncomeLoss",
                            ocf="NetCashProvidedByUsedInOperatingActivities", capex="PaymentsToAcquirePropertyPlantAndEquipment",
                            da="DepreciationDepletionAndAmortization", ta="Assets", eq="StockholdersEquity",
                            debt="LongTermDebtNoncurrent", cash="CashAndCashEquivalentsAtCarryingValue", ca="AssetsCurrent",
                            cl="LiabilitiesCurrent", tl="Liabilities", re="RetainedEarningsAccumulatedDeficit",
                            sh="WeightedAverageNumberOfDilutedSharesOutstanding", ie="InterestExpense",
                            pt="IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                            tax="IncomeTaxExpenseBenefit", rec="AccountsReceivableNetCurrent", ppe="PropertyPlantAndEquipmentNet"),
            "ifrs-full": dict(rev="Revenue", cogs="CostOfSales", oi="ProfitLossFromOperatingActivities",
                              ni="ProfitLossAttributableToOwnersOfParent", ocf="CashFlowsFromUsedInOperatingActivities",
                              capex="PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                              da="DepreciationAndAmortisationExpense", ta="Assets", eq="EquityAttributableToOwnersOfParent",
                              debt="NoncurrentPortionOfNoncurrentBorrowings", cash="CashAndCashEquivalents", ca="CurrentAssets",
                              cl="CurrentLiabilities", tl="Liabilities", re="RetainedEarnings",
                              sh="AdjustedWeightedAverageShares", ie="FinanceCosts", pt="ProfitLossBeforeTax",
                              tax="IncomeTaxExpenseContinuingOperations", rec="TradeAndOtherCurrentReceivables",
                              ppe="PropertyPlantAndEquipment"),
        }[tax]
        noise = lambda: 1 + 0.03 * self.rng.standard_normal()  # noqa: E731
        vals = {
            "rev": rev, "cogs": {y: v * (1 - m - 0.2) for y, v in rev.items()},
            "oi": {y: v * m * noise() for y, v in rev.items()}, "ni": {y: v * m * 0.75 * noise() for y, v in rev.items()},
            "ocf": {y: v * m * 0.95 * noise() for y, v in rev.items()}, "capex": {y: v * 0.05 for y, v in rev.items()},
            "da": {y: v * 0.04 for y, v in rev.items()}, "ie": {y: v * 0.01 for y, v in rev.items()},
            "pt": {y: v * m * 0.95 for y, v in rev.items()}, "tax": {y: v * m * 0.2 for y, v in rev.items()},
            "sh": {y: c["shares"] * (1 - 0.01 * (y - years[0])) for y in years},
        }
        inst = {"ta": {y: v * 1.5 for y, v in rev.items()}, "eq": {y: v * 0.6 for y, v in rev.items()},
                "debt": {y: v * 0.3 for y, v in rev.items()}, "cash": {y: v * 0.15 for y, v in rev.items()},
                "ca": {y: v * 0.5 for y, v in rev.items()}, "cl": {y: v * 0.3 for y, v in rev.items()},
                "tl": {y: v * 0.9 for y, v in rev.items()}, "re": {y: v * 0.4 for y, v in rev.items()},
                "rec": {y: v * 0.12 for y, v in rev.items()}, "ppe": {y: v * 0.4 for y, v in rev.items()}}
        for k, series in vals.items():
            unit = "shares" if k == "sh" else cur
            b.annual(names[k], series, form=form, unit=unit, tax=tax)
        for k, series in inst.items():
            b.annual(names[k], series, form=form, unit=cur, tax=tax, instant=True)
        if not foreign:
            b.add("EntityCommonStockSharesOutstanding", vals["sh"][last_fy], f"{last_fy + 1}-01-31", form="10-K",
                  filed=f"{last_fy + 1}-02-15", unit="shares", tax="dei")
            # half-year YTD for TTM
            if TODAY.month >= 9:
                for k in ("rev", "ni", "ocf", "capex", "oi", "cogs", "da", "ie", "pt", "tax"):
                    cur_ytd = vals[k][last_fy] * 0.55
                    prev_ytd = vals[k][last_fy] * 0.5
                    b.add(names[k], cur_ytd, f"{last_fy + 1}-06-30", start=f"{last_fy + 1}-01-01", form="10-Q",
                          filed=f"{last_fy + 1}-08-01", unit=cur)
                    b.add(names[k], prev_ytd, f"{last_fy}-06-30", start=f"{last_fy}-01-01", form="10-Q",
                          filed=f"{last_fy + 1}-08-01", unit=cur)
        return b.build(), 0.0

    def frame(self, tax, concept, unit, period):
        return []

    def fetch_document(self, url):
        body = ("<html><body><p>Item 1A. Risk Factors</p>" + "<p>" + " ".join(
            f"Our business may be affected by synthetic risk number {i} which is described in considerable detail here." for i in range(80))
                + ("<p>We identified a material weakness in our internal control over financial reporting.</p>"
                   if "doc" in url and "/1000005/" in url else "")
                + "<p>One customer accounted for 23% of our revenue in the year.</p>"
                + "</p><p>Item 1B. Unresolved Staff Comments</p></body></html>")
        return body

    # ------------------------------------------------------------------ Yahoo
    def download_prices(self, tickers, start="2014-01-01", batch=80, actions=False):
        out = {}
        for t in tickers:
            if t in self.px:
                df = self.px[t][self.px[t].index >= pd.Timestamp(start)].copy()
                if actions:
                    df["splits"] = 0.0
                out[t] = df
        return out

    def info(self, t, ttl_hours=20):
        if t in ("SWDA.MI", "IWDA.AS"):
            return {"quoteType": "ETF", "currency": "EUR"}
        if t == "URTH":
            return {"quoteType": "ETF", "currency": "USD"}
        us = next((c for c in self.us if c["ticker"] == t), None)
        px = self.px[t]["close"].iloc[-1] if t in self.px else None
        if us:
            sh = us["shares"] * (1 - 0.01 * 8)
            if us.get("foreign"):
                sh = sh / 5  # ADR ratio 5
            return {"quoteType": "EQUITY", "currency": "USD", "financialCurrency": "EUR" if us.get("foreign") else "USD",
                    "sector": us["sector"], "industry": "Banks - Regional" if us["bank"] else "Synthetic",
                    "country": "Netherlands" if us.get("foreign") else "United States", "longName": us["name"],
                    "sharesOutstanding": sh, "marketCap": sh * px, "forwardEps": 3.0, "trailingEps": 2.5, "forwardPE": 20}
        it = next((c for c in self.intl if c["ticker"] == t), None)
        if it:
            div = 100 if it["currency"] == "GBp" else 1
            sh = 5e8
            return {"quoteType": "EQUITY", "currency": it["currency"],
                    "financialCurrency": "GBP" if it["currency"] == "GBp" else it["currency"],
                    "sector": it["sector"], "industry": "Synthetic", "country": it["country"], "longName": it["name"],
                    "sharesOutstanding": sh, "marketCap": sh * px / div}
        return None

    def statements(self, t, ttl_days=7):
        it = next((c for c in self.intl if c["ticker"] == t), None)
        us = next((c for c in self.us if c["ticker"] == t), None)
        if not it and not us:
            return None
        years = [TODAY.year - 1 - k for k in range(4)]
        scale = 4e10 if (it and it["currency"] == "JPY") else 4e9
        inc, bal, cfs = {}, {}, {}
        for k, y in enumerate(years):
            d = f"{y}-12-31"
            rev = scale * (1.05 ** -k)
            inc[d] = {"Total Revenue": rev, "Cost Of Revenue": rev * 0.6, "Gross Profit": rev * 0.4,
                      "Operating Income": rev * 0.15, "Net Income Common Stockholders": rev * 0.1,
                      "Diluted Average Shares": 5e8, "Interest Expense": rev * 0.01, "Pretax Income": rev * 0.13,
                      "Tax Provision": rev * 0.03, "Reconciled Depreciation": rev * 0.05}
            bal[d] = {"Total Assets": rev * 2, "Stockholders Equity": rev * 0.8, "Total Debt": rev * 0.5,
                      "Cash And Cash Equivalents": rev * 0.1, "Current Assets": rev * 0.6, "Current Liabilities": rev * 0.4,
                      "Total Liabilities Net Minority Interest": rev * 1.2, "Retained Earnings": rev * 0.3}
            cfs[d] = {"Operating Cash Flow": rev * 0.14, "Capital Expenditure": -rev * 0.05, "Cash Dividends Paid": -rev * 0.05}
        return {"income_annual": inc, "balance_annual": bal, "cashflow_annual": cfs,
                "income_quarterly": {}, "balance_quarterly": {}, "cashflow_quarterly": {}}

    def fx_history(self, pair, start="2014-01-01"):
        return pd.Series(dtype=float)

    # ------------------------------------------------------------------ FX & macro
    def ecb_history(self):
        rows = []
        for d in pd.bdate_range("2013-01-01", TODAY):
            ds = d.strftime("%Y-%m-%d")
            rows += [(ds, "USD", 1.1), (ds, "GBP", 0.85), (ds, "JPY", 160.0)]
        df = pd.DataFrame(rows, columns=["date", "currency", "per_eur"])
        df["source"] = "SYNTHETIC ECB"
        return df

    def fred_series(self, series):
        df = pd.DataFrame({"date": ["2026-01-01"], "value": [4.0 if series == "DGS10" else 2.5]})
        df["series"] = series
        df["source"] = "SYNTHETIC FRED"
        return df

    def international_candidates(self, use_wikipedia=True):
        df = pd.DataFrame({"ticker": [c["ticker"] for c in self.intl], "index": [["SYNTH"]] * len(self.intl),
                           "source": "synthetic"})
        return df, ["synthetic"]
