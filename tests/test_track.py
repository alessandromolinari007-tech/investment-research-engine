import json

import pandas as pd
import pytest

from ire.track import live_track_record


def test_live_track_record_matches_a_manual_calculation(world):
    from ire.db import connect
    from ire.pipeline import Pipeline
    from ire.prices import PriceStore
    from ire.sources.fx_macro import FxTable

    rid = Pipeline(mode="quick", verbose=False).run()
    con = connect()
    started = (pd.Timestamp.today() - pd.Timedelta(days=120)).normalize()
    con.execute("UPDATE runs SET finished_at=? WHERE run_id=?", (started.isoformat() + "+00:00", rid))
    con.commit()
    tr = live_track_record(con)
    assert len(tr) == 1 and tr[0]["run_id"] == rid and 100 < tr[0]["days"] <= 125

    port = json.loads(con.execute("SELECT payload FROM portfolios WHERE run_id=? AND name='proposto'", (rid,)).fetchone()[0])
    fx = FxTable(pd.read_sql_query("SELECT date, currency, per_eur, source FROM fx", con))
    store = PriceStore(con, progress=lambda m: None)
    exp, tw = 0.0, 0.0
    for p in port["positions"]:
        cur = con.execute("SELECT price_currency FROM companies WHERE company_id=?", (p["company_id"],)).fetchone()[0]
        df = store._read([p["ticker"]], "2013-01-01")[p["ticker"]]
        s = fx.series_to_eur(df["adj_close"] / (100.0 if cur == "GBP" and df["close"].iloc[-1] > 500 else 1.0), cur).dropna()
        s0 = s[s.index > started].iloc[0]
        exp += p["weight"] * (s.iloc[-1] / s0 - 1)
        tw += p["weight"]
    assert tr[0]["portfolio_return"] == pytest.approx(exp / tw, rel=0.02, abs=0.01)
    assert tr[0]["benchmark_return"] is not None and tr[0]["excess"] == pytest.approx(tr[0]["portfolio_return"] - tr[0]["benchmark_return"])


def test_no_portfolio_no_record(world):
    from ire.db import init_db

    assert live_track_record(init_db()) == []
