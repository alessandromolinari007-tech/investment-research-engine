"""SEC EDGAR (free, official, no API key). Requires a User-Agent with contact email.

Endpoints used
--------------
* https://www.sec.gov/files/company_tickers_exchange.json   ticker ↔ CIK ↔ exchange
* https://data.sec.gov/submissions/CIK##########.json         company profile + filings index
* https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json  all XBRL facts ever filed
* https://data.sec.gov/api/xbrl/frames/...                      one concept, all companies, one period
* https://www.sec.gov/Archives/edgar/data/<cik>/<acc>/<doc>     filing documents (10-K text)
"""
from __future__ import annotations

from typing import Any

from ..config import load_config
from ..http import SourceUnavailable, get_client

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{unit}/{period}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"

ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
FOREIGN_ANNUAL = {"20-F", "20-F/A", "40-F", "40-F/A"}


def cik10(cik: Any) -> str:
    return str(int(cik)).zfill(10)


def _ttl_days(key: str, default: float) -> float:
    return float(load_config().get(key, default)) * 86400


def company_tickers() -> list[dict[str, Any]]:
    """All SEC-registered tickers with exchange. Returns list of dicts."""
    r = get_client().get(TICKERS_URL, ttl_seconds=86400)
    data = r.json()
    fields = data["fields"]
    out = []
    for row in data["data"]:
        d = dict(zip(fields, row))
        d["cik"] = cik10(d["cik"])
        out.append(d)
    return out


def submissions(cik: str) -> dict[str, Any]:
    r = get_client().get(SUBMISSIONS_URL.format(cik=cik10(cik)), ttl_seconds=_ttl_days("cache.filings_ttl_days", 7))
    return r.json()


def companyfacts(cik: str) -> tuple[dict[str, Any], float]:
    """Returns (json, fetched_at epoch)."""
    r = get_client().get(
        COMPANYFACTS_URL.format(cik=cik10(cik)), ttl_seconds=_ttl_days("cache.fundamentals_ttl_days", 7), timeout=120
    )
    return r.json(), r.fetched_at


def frame(tax: str, concept: str, unit: str, period: str) -> list[dict[str, Any]]:
    """e.g. frame('dei','EntityCommonStockSharesOutstanding','shares','CY2026Q2I')"""
    try:
        r = get_client().get(FRAMES_URL.format(tax=tax, concept=concept, unit=unit, period=period), ttl_seconds=86400 * 3)
    except SourceUnavailable:
        return []
    return r.json().get("data", [])


def recent_filings(sub: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the 'recent' filings block of a submissions payload."""
    rec = sub.get("filings", {}).get("recent", {})
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "items", "primaryDocument", "primaryDocDescription"]
    n = len(rec.get("accessionNumber", []))
    out = []
    for i in range(n):
        out.append({k: (rec.get(k) or [None] * n)[i] if i < len(rec.get(k) or []) else None for k in keys})
    return out


def filing_url(cik: str, accession: str, doc: str) -> str:
    return ARCHIVE_URL.format(cik_int=int(cik), acc_nodash=accession.replace("-", ""), doc=doc)


def filing_index_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


def fetch_document(url: str) -> str:
    r = get_client().get(url, ttl_seconds=86400 * 365, timeout=120)  # filed documents never change
    return r.text
