"""`python -m ire check`: verify configuration and reachability of every data source."""
from __future__ import annotations

import sys

from .config import load_config


def run_checks() -> bool:
    cfg = load_config()
    ok = True
    print(f"Python {sys.version.split()[0]}")
    ua = cfg.sec_user_agent
    if not ua or "@" not in ua:
        print("✗ SEC user_agent mancante in config.toml ([sec] user_agent = \"Nome Cognome email@dominio\")")
        ok = False
    else:
        print("✓ SEC user_agent configurato")
    checks = []

    def sec_check():
        from .sources import sec
        rows = sec.company_tickers()
        return f"{len(rows)} ticker SEC"

    def sec_facts():
        from .sources import sec
        cf, _ = sec.companyfacts("0000320193")
        return f"companyfacts Apple: {len(cf.get('facts', {}).get('us-gaap', {}))} concetti"

    def ecb():
        from .sources.fx_macro import ecb_history
        df = ecb_history()
        return f"BCE: {df['currency'].nunique()} valute, ultimo {df['date'].max()}"

    def fred():
        from .sources.fx_macro import fred_series
        df = fred_series("DGS10")
        return f"FRED DGS10 ultimo {df['date'].max()} = {df['value'].iloc[-1]}%"

    def yahoo_px():
        from .sources import yahoo
        d = yahoo.download_prices(["AAPL", "ENI.MI"], start="2025-01-01")
        return f"Yahoo prezzi: {', '.join(f'{k}:{len(v)} giorni' for k, v in d.items())}"

    def yahoo_info():
        from .sources import yahoo
        i = yahoo.info("ENI.MI", ttl_hours=0)
        return f"Yahoo info ENI.MI: settore={i.get('sector') if i else None}, valuta={i.get('currency') if i else None}"

    def yahoo_st():
        from .sources import yahoo
        st = yahoo.statements("ENI.MI", ttl_days=0)
        return f"Yahoo bilanci ENI.MI: {len((st or {}).get('income_annual', {}))} anni"

    def wiki():
        from .sources.universe_intl import INDICES, scrape_index
        syms = scrape_index(INDICES[0])
        return f"Wikipedia FTSE MIB: {len(syms)} titoli"

    for name, fn, critical in (("SEC ticker", sec_check, True), ("SEC XBRL", sec_facts, True), ("BCE cambi", ecb, True),
                               ("FRED tassi", fred, False), ("Yahoo prezzi", yahoo_px, True),
                               ("Yahoo metadati", yahoo_info, True), ("Yahoo bilanci", yahoo_st, False),
                               ("Wikipedia indici", wiki, False)):
        try:
            print(f"✓ {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            print(f"{'✗' if critical else '!'} {name}: {e}")
            if critical:
                ok = False
    print("Tutto pronto." if ok else "Alcuni controlli critici sono falliti: vedi sopra.")
    return ok
