"""Historical test (ire/backtest.py): no information from the future, correct statistics, end-to-end on the synthetic world."""
import copy

import numpy as np
import pandas as pd
import pytest

import ire.backtest as B
from ire import clock


# ------------------------------------------------------------------------------------------------ point in time
def _cf(world, i=0):
    return world.companyfacts(world.us[i]["cik"])[0]


def test_truncate_drops_facts_filed_after_the_date(world):
    cf = _cf(world)
    before = copy.deepcopy(cf)
    cut = pd.Timestamp.today().year - 3
    t = B.truncate_companyfacts(cf, f"{cut}-06-30")
    filed = [e["filed"] for node in t["facts"]["us-gaap"].values() for arr in node["units"].values() for e in arr]
    assert filed and max(filed) <= f"{cut}-06-30"
    assert cf == before                                            # the input is not modified
    all_filed = [e["filed"] for node in cf["facts"]["us-gaap"].values() for arr in node["units"].values() for e in arr]
    assert max(all_filed) > f"{cut}-06-30"                         # (the full payload does contain later filings)


def test_fact_without_filing_date_is_not_public():
    cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"end": "2020-12-31", "val": 1}]}}}}}
    assert B.truncate_companyfacts(cf, "2030-01-01")["facts"] == {}


def test_clock_changes_data_age(world):
    from ire.normalize.sec_facts import normalize_companyfacts
    from ire.metrics import compute_fundamental_metrics

    cf = _cf(world)
    fin = normalize_companyfacts("X", cf, splits=[], apply_splits=True, known_splits=[], fetched_at=None)
    now = compute_fundamental_metrics(fin, 1e10, 100.0, banklike=False, reit=False, yahoo={}).val("data_age_days")
    with clock.as_of(pd.Timestamp.today() - pd.Timedelta(days=365)):
        past = compute_fundamental_metrics(fin, 1e10, 100.0, banklike=False, reit=False, yahoo={}).val("data_age_days")
    assert now - past == pytest.approx(365, abs=1)


def _snap(world, cf, asof):
    from ire.config import load_config
    from ire.db import init_db
    from ire.prices import PriceStore
    from ire.sources.fx_macro import FxTable

    c = {"company_id": "CIK1", "ticker": "US00", "is_banklike": 0, "is_reit": 0, "price_currency": "USD",
         "filer_type": "domestic"}
    df = world.px["US00"]
    fx = FxTable(pd.DataFrame({"date": [], "currency": [], "per_eur": [], "source": []}))
    return B.snapshot_company(c, cf, pd.Timestamp(asof), df, [], fx, None, {}, load_config())


def test_snapshot_ignores_later_filings(world):
    """The score of a past date must not change if what was filed AFTER that date changes."""
    cf = _cf(world)
    asof = f"{pd.Timestamp.today().year - 3}-09-30"
    base = _snap(world, cf, asof)
    assert base is not None and base["metrics"].get("market_cap")
    tampered = copy.deepcopy(cf)
    for node in tampered["facts"]["us-gaap"].values():
        for arr in node["units"].values():
            for e in arr:
                if e["filed"] > asof:
                    e["val"] = e["val"] * 100                      # future restatement / absurd later value
            arr.append({"end": "2031-12-31", "val": 1e15, "accn": "x", "fy": 2031, "fp": "FY", "form": "10-K",
                        "filed": "2032-02-15", "start": "2031-01-01"})
    again = _snap(world, tampered, asof)
    assert again is not None
    for k, v in base["metrics"].items():
        w = again["metrics"][k]
        assert (v is None and w is None) or v == pytest.approx(w, rel=1e-9, abs=1e-12) or (pd.isna(v) and pd.isna(w)), k


def test_data_age_is_measured_at_the_date_not_today(world):
    cf = _cf(world)
    asof = f"{pd.Timestamp.today().year - 3}-09-30"
    snap = _snap(world, cf, asof)
    assert snap["metrics"]["data_age_days"] < 550                  # today it would be > 1000 days


# ------------------------------------------------------------------------------------------------ returns, dates
def test_forward_return_enters_after_the_date_and_needs_the_full_window():
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    s = pd.Series(np.arange(len(idx), dtype=float) + 100, index=idx)
    asof = pd.Timestamp("2020-06-30")                              # a Tuesday
    r = B.forward_return(s, asof, 6)
    entry = s[s.index > asof].iloc[0]
    exit_ = s[s.index <= pd.Timestamp("2020-07-01") + pd.DateOffset(months=6)].iloc[-1]
    assert r == pytest.approx(exit_ / entry - 1)
    assert B.forward_return(s, pd.Timestamp("2021-10-01"), 6) is None      # window not over (or delisted)
    assert B.forward_return(s, pd.Timestamp("2019-12-01"), 6) is None      # no price right after the date


def test_rebalance_dates():
    d = B.rebalance_dates("2017-06-30", pd.Timestamp("2019-12-31"), 6)
    assert [x.strftime("%Y-%m") for x in d] == ["2017-06", "2017-12", "2018-06", "2018-12", "2019-06", "2019-12"]


# ------------------------------------------------------------------------------------------------ statistics
def _obs(signal: float, n_dates=10, n=120, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_dates):
        d = (pd.Timestamp("2018-06-30") + pd.DateOffset(months=6 * k)).strftime("%Y-%m-%d")
        score = rng.normal(size=n)
        noise = rng.normal(scale=0.2, size=n)
        f12 = signal * score * 0.1 + noise
        rows.append(pd.DataFrame({"asof": d, "robust_score": score, "classification": np.where(score > 0, "A", "B"),
                                  "fwd_6m": f12 / 2, "fwd_12m": f12}))
    return pd.concat(rows, ignore_index=True)


def test_evaluate_finds_a_real_signal():
    st = B.evaluate(_obs(+1.0), {})["12"]
    assert st["ic_mean"] > 0.2 and st["ic_t_non_overlapping"] >= 2
    assert st["q1_minus_q5"] > 0 and st["random_percentile_mean"] > 0.9
    assert B.reading(st)[0] == "favorevole"


def test_evaluate_reports_nothing_for_noise():
    st = B.evaluate(_obs(0.0, seed=5), {})["12"]
    assert abs(st["ic_mean"]) < 0.1
    assert B.reading(st)[0] == "non dimostrato"


def test_evaluate_flags_an_inverted_signal():
    st = B.evaluate(_obs(-1.0), {})["12"]
    assert st["ic_mean"] < -0.2 and B.reading(st)[0] == "contrario"


def test_non_overlapping_dates_for_the_t_statistic():
    d = [pd.Timestamp("2018-06-30") + pd.DateOffset(months=6 * k) for k in range(6)]
    assert len(B._non_overlapping(d, 12)) == 3 and len(B._non_overlapping(d, 6)) == 6


def test_small_dates_are_skipped():
    st = B.evaluate(_obs(1.0, n=B.MIN_UNIVERSE - 1), {})["12"]
    assert st["n_dates"] == 0


def test_benchmark_comparison():
    obs = _obs(1.0)
    bench = {d: {12: 0.0, 6: 0.0} for d in obs["asof"].unique()}
    st = B.evaluate(obs, bench)["12"]
    assert st["bench_total"] == 0.0 and st["top_excess_vs_bench"] == pytest.approx(st["top_total"], abs=1e-3)


# ------------------------------------------------------------------------------------------------ end to end
def test_backtest_end_to_end_on_the_synthetic_world(world, monkeypatch):
    import json

    from ire.db import connect
    from ire.pipeline import Pipeline

    Pipeline(mode="quick", verbose=False).run()
    monkeypatch.setattr(B, "MIN_UNIVERSE", 12)
    start = f"{pd.Timestamp.today().year - 5}-06-30"
    msgs = []
    bt = B.run_backtest(progress=msgs.append, start=start, step_months=6, min_cap_usd=1e9)
    con = connect()
    row = con.execute("SELECT * FROM backtests WHERE bt_id=?", (bt,)).fetchone()
    summ = json.loads(row["summary"])
    assert set(summ["stats"]) == {"6", "12"} and summ["caveats"]
    assert summ["stats"]["12"]["n_dates"] >= 2
    n = con.execute("SELECT COUNT(*) FROM backtest_obs WHERE bt_id=?", (bt,)).fetchone()[0]
    assert n > 40
    assert all(r["robust_score"] is not None for r in con.execute("SELECT robust_score FROM backtest_obs WHERE bt_id=?", (bt,)))
    # the synthetic prices are random walks, unrelated to the statements: no skill must be claimed
    assert summ["readings"]["12"]["level"] != "favorevole"
