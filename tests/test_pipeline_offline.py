"""Offline end-to-end test of the whole pipeline on a SYNTHETIC world (no network)."""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_full_pipeline_offline(world):
    from ire.db import connect
    from ire.pipeline import Pipeline
    from ire.scoring import C_REDFLAG

    rid = Pipeline(mode="quick", verbose=False).run()
    con = connect()
    run = con.execute("SELECT * FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert run["status"] == "completed", run["summary"]
    comps = pd.read_sql_query("SELECT * FROM companies", con)
    inu = comps[comps.in_universe == 1]
    # duplicate listing removed, delisted excluded, OTC never included
    assert "FORX" in set(inu.ticker) and "FORX.AS" not in set(inu.ticker)
    assert comps.loc[comps.ticker == "FORX.AS", "exclusion_reason"].str.contains("doppia").all()
    assert comps.loc[comps.ticker == "DEAD.MI", "exclusion_reason"].str.contains("prezzo").all()
    assert "OTCX" not in set(comps.ticker)
    # tiers
    assert set(inu.loc[inu.ticker.str.startswith("US"), "data_tier"]) == {"A"}
    assert inu.loc[inu.ticker == "ITA1.MI", "data_tier"].iloc[0] == "B"
    assert inu.loc[inu.ticker == "FORX", "filer_type"].iloc[0] == "foreign"
    # scores exist for most companies
    sc = pd.read_sql_query("SELECT * FROM scores WHERE run_id=?", con, params=[rid])
    assert sc["robust_score"].notna().sum() >= 25
    # GBp handled: UK market cap in GBP ~ 5e8 shares * ~25 GBP → between 1e9 and 1e11
    m = pd.read_sql_query("SELECT company_id, metric, value FROM metrics WHERE run_id=?", con, params=[rid])
    uk = comps.loc[comps.ticker == "UKX1.L", "company_id"].iloc[0]
    mc = m[(m.company_id == uk) & (m.metric == "market_cap")]["value"].iloc[0]
    ukpx = world.px["UKX1.L"]["close"]
    px = ukpx[ukpx.index < pd.Timestamp.today().normalize()].iloc[-1] / 100     # today's bar is never stored
    assert mc == pytest.approx(px * 5e8, rel=1e-6)
    # ADR: market cap of FORX in EUR = price_usd * ADS shares / 1.1
    fx_cid = comps.loc[comps.ticker == "FORX", "company_id"].iloc[0]
    md = json.loads(con.execute("SELECT raw_json FROM market_data WHERE company_id=?", (fx_cid,)).fetchone()[0])
    assert md.get("adr_ratio") == 5
    # restatement 8-K item 4.02 → red flag classification
    us05 = comps.loc[comps.ticker == "US05", "company_id"].iloc[0]
    cls = sc.loc[sc.company_id == us05, "classification"].iloc[0]
    assert cls == C_REDFLAG
    fl = pd.read_sql_query("SELECT * FROM flags WHERE run_id=? AND company_id=?", con, params=[rid, us05])
    assert "NON_RELIANCE_8K" in set(fl.code)
    assert "MATERIAL_WEAKNESS" in set(fl.code)
    # customer concentration detected in text
    assert "CUSTOMER_CONCENTRATION" in set(pd.read_sql_query("SELECT code FROM flags WHERE run_id=?", con, params=[rid]).code)
    # portfolio built, respects caps
    port = json.loads(con.execute("SELECT payload FROM portfolios WHERE run_id=?", (rid,)).fetchone()[0])
    pos = port["positions"]
    assert len(pos) >= 5
    w = pd.Series({p["ticker"]: p["weight"] for p in pos})
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    # every constraint is either respected or explicitly reported as violated (never silently broken)
    rep = {c["vincolo"]: c for c in port["constraints"]}
    for c in port["constraints"]:
        assert c["rispettato"] == (c["effettivo"] <= c["configurato"] + 1e-6) or c["vincolo"].startswith("peso minimo")
        if not c["rispettato"]:
            assert any("VINCOLO NON RISPETTATO" in l and c["vincolo"] in l for l in port["log"])
    assert rep["peso massimo per titolo"]["effettivo"] == pytest.approx(w.max())
    assert w.max() <= 0.12 + 1e-6
    secw = pd.Series({p["ticker"]: p["weight"] for p in pos}).groupby(pd.Series({p["ticker"]: p["sector"] for p in pos})).sum()
    assert secw.max() == pytest.approx(rep["peso massimo per settore"]["effettivo"], abs=1e-6)
    assert port["status"] in ("proposto", "non proposto", "concentrato")
    if len(pos) < 12:
        assert port["status"] == "non proposto"
    assert all(p["role"] and p["reason"] for p in pos)
    assert "US05" not in set(w.index)
    # facts have provenance
    f = pd.read_sql_query("SELECT * FROM facts WHERE company_id=? AND item='revenue'", con, params=[us05])
    assert (f["source_ref"].notna() | f["source"].str.startswith("Calcolato")).all()

    # second run → diff works and run is completed
    rid2 = Pipeline(mode="quick", verbose=False).run()
    run2 = con.execute("SELECT * FROM runs WHERE run_id=?", (rid2,)).fetchone()
    assert run2["status"] == "completed"
    assert json.loads(run2["summary"])["diff"]["previous_run"] == rid
