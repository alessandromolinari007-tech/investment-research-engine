"""Classification helpers: sectors, regions, bank-like / REIT detection."""
from __future__ import annotations

SECTORS = [
    "Technology", "Communication Services", "Healthcare", "Financial Services", "Consumer Cyclical",
    "Consumer Defensive", "Industrials", "Energy", "Utilities", "Real Estate", "Basic Materials",
]

SECTOR_IT = {
    "Technology": "Tecnologia", "Communication Services": "Comunicazioni", "Healthcare": "Salute",
    "Financial Services": "Finanza", "Consumer Cyclical": "Consumi discrezionali", "Consumer Defensive": "Beni di prima necessità",
    "Industrials": "Industria", "Energy": "Energia", "Utilities": "Utility", "Real Estate": "Immobiliare",
    "Basic Materials": "Materiali", "Unknown": "Sconosciuto",
}

# SIC → sector (fallback when Yahoo has no sector). Ranges are inclusive.
SIC_RANGES = [
    (100, 999, "Consumer Defensive"), (1000, 1499, "Basic Materials"), (1311, 1389, "Energy"),
    (1500, 1799, "Industrials"), (2000, 2199, "Consumer Defensive"), (2200, 2399, "Consumer Cyclical"),
    (2400, 2699, "Basic Materials"), (2700, 2799, "Communication Services"), (2800, 2829, "Basic Materials"),
    (2830, 2836, "Healthcare"), (2840, 2844, "Consumer Defensive"), (2850, 2899, "Basic Materials"),
    (2900, 2999, "Energy"), (3000, 3299, "Basic Materials"), (3300, 3399, "Basic Materials"),
    (3400, 3569, "Industrials"), (3570, 3579, "Technology"), (3580, 3659, "Industrials"),
    (3660, 3699, "Technology"), (3700, 3719, "Consumer Cyclical"), (3720, 3799, "Industrials"),
    (3800, 3839, "Technology"), (3840, 3851, "Healthcare"), (3852, 3999, "Industrials"),
    (4000, 4799, "Industrials"), (4800, 4899, "Communication Services"), (4900, 4999, "Utilities"),
    (5000, 5199, "Industrials"), (5200, 5999, "Consumer Cyclical"), (5400, 5499, "Consumer Defensive"),
    (5912, 5912, "Consumer Defensive"), (6000, 6499, "Financial Services"), (6500, 6599, "Real Estate"),
    (6798, 6798, "Real Estate"), (6700, 6797, "Financial Services"), (6799, 6799, "Financial Services"),
    (7000, 7299, "Consumer Cyclical"), (7370, 7379, "Technology"), (7300, 7369, "Industrials"),
    (7380, 7399, "Industrials"), (7800, 7999, "Communication Services"), (8000, 8099, "Healthcare"),
    (8700, 8799, "Industrials"), (8200, 8299, "Consumer Defensive"),
]


def sector_from_sic(sic: str | int | None) -> str:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Unknown"
    best = None
    for lo, hi, sec in SIC_RANGES:
        if lo <= code <= hi:
            width = hi - lo
            if best is None or width < best[0]:
                best = (width, sec)
    return best[1] if best else "Unknown"


BANKLIKE_INDUSTRIES = {
    "Banks - Diversified", "Banks - Regional", "Insurance - Life", "Insurance - Property & Casualty",
    "Insurance - Diversified", "Insurance - Reinsurance", "Insurance - Specialty", "Capital Markets",
    "Mortgage Finance", "Financial Conglomerates",
    "REIT - Mortgage",   # mortgage REITs are balance-sheet lenders: bank-like metrics, not FFO/EBITDA
}


def is_banklike(sic: str | int | None, industry: str | None, liab_to_assets: float | None = None) -> bool:
    try:
        code = int(sic) if sic is not None else None
    except (TypeError, ValueError):
        code = None
    if code is not None:
        if 6000 <= code <= 6199 or code == 6211 or 6300 <= code <= 6399:
            return True
    if industry in BANKLIKE_INDUSTRIES:
        return True
    if industry == "Credit Services" and liab_to_assets is not None and liab_to_assets > 0.8:
        return True   # consumer lenders are bank-like; payment networks (Visa, Mastercard) are not
    return False


def is_reit(sic: str | int | None, industry: str | None) -> bool:
    if str(sic) == "6798":
        return True
    return bool(industry and industry.startswith("REIT"))


EUROPE = {"Italy", "Germany", "France", "Netherlands", "Spain", "Switzerland", "Sweden", "Denmark", "Finland",
          "Norway", "Belgium", "Austria", "Portugal", "Ireland", "Luxembourg", "Greece", "Poland", "Czech Republic"}
APAC = {"Australia", "Hong Kong", "Singapore", "New Zealand", "Taiwan", "South Korea", "China", "Macau"}
NA = {"United States", "Canada"}

SUFFIX_REGION = {
    ".MI": "Europa", ".DE": "Europa", ".PA": "Europa", ".AS": "Europa", ".MC": "Europa", ".SW": "Europa",
    ".ST": "Europa", ".CO": "Europa", ".HE": "Europa", ".OL": "Europa", ".BR": "Europa", ".LS": "Europa",
    ".VI": "Europa", ".IR": "Europa", ".L": "Regno Unito", ".T": "Giappone", ".TO": "Nord America",
    ".AX": "Asia-Pacifico", ".HK": "Asia-Pacifico", ".SI": "Asia-Pacifico",
}


def region_for(country: str | None, ticker: str | None = None) -> str:
    if country:
        if country in NA:
            return "Nord America"
        if country == "United Kingdom" or country in ("Jersey", "Guernsey", "Isle of Man"):
            return "Regno Unito"
        if country in EUROPE:
            return "Europa"
        if country == "Japan":
            return "Giappone"
        if country in APAC:
            return "Asia-Pacifico"
        return "Altro"
    if ticker:
        for suf, reg in SUFFIX_REGION.items():
            if ticker.upper().endswith(suf.upper()):
                return reg
        if "." not in ticker:
            return "Nord America"
    return "Altro"
