"""Unit tests of the methodology: sector-relative scoring, classification, valuation verdict and confidence,
reverse DCF, text red flags, portfolio constraints. All numbers are SYNTHETIC test inputs."""
import numpy as np
import pandas as pd
import pytest

import ire.scoring as S
from ire.config import Config
from ire.portfolio import construct_portfolio, enforce_caps
from ire.qualitative import text_red_flags
from ire.valuation import (dcf_value, implied_growth, normalized_fcf, percentile_vs_history, reverse_dcf,
                           valuation_verdict, Signal)


# ================================================================================ scoring
def test_mid_rank_percentiles_are_symmetric():
    v = pd.Series(np.arange(1.0, 11.0))
    p, n = S._percentiles(v, pd.Series(["g"] * 10))
    assert p.min() == pytest.approx(0.05) and p.max() == pytest.approx(0.95)   # never 0 or 100
    lower_better = 1 - p
    assert list(lower_better[::-1]) == pytest.approx(list(p))                  # exact mirror
    assert (n == 10).all()


def _universe(n_tech=12, n_small=3, n_banks=0):
    rows = []
    for i in range(n_tech):
        rows.append({"company_id": f"T{i}", "sector": "Technology", "roic_5y_median": 0.05 + 0.02 * i,
                     "revenue_cagr_5y": 0.01 * i, "net_debt_ebitda": 3.0 - 0.2 * i, "neg_ebitda_with_debt": 0})
    for i in range(n_small):
        rows.append({"company_id": f"U{i}", "sector": "Utilities", "roic_5y_median": 0.04 + 0.01 * i,
                     "revenue_cagr_5y": 0.02, "net_debt_ebitda": 4.0, "neg_ebitda_with_debt": 0})
    for i in range(n_banks):
        rows.append({"company_id": f"B{i}", "sector": "Financial Services", "industry": "Banks - Regional",
                     "is_banklike": 1, "roe_5y_median": 0.08 + 0.005 * i})
    return pd.DataFrame(rows)


def test_small_sector_uses_whole_non_financial_universe():
    out = S.score_universe(_universe(), {"quality": 1}, min_peer=8)
    assert set(out.loc[out.sector == "Technology", "peer_used"]) == {"Technology"}
    assert out.loc[out.sector == "Utilities", "peer_used"].iloc[0].startswith("Universo intero non finanziario")


def test_banks_are_compared_with_finance_only():
    out = S.score_universe(_universe(n_banks=6), {"quality": 1}, min_peer=8)
    assert out.loc[out.company_id == "B0", "peer_used"].iloc[0] == S.FIN_POOL


def test_too_few_observations_give_no_percentile():
    df = _universe(n_tech=4, n_small=0)
    out = S.score_universe(df, {"quality": 1}, min_peer=3)
    det = out["detail_obj"].iloc[0]
    assert "roic_5y_median" not in det["metrics"]        # 4 < MIN_OBS: never imputed
    assert out["quality"].isna().all()


def test_small_sample_is_shrunk_towards_the_middle():
    df = _universe(n_tech=6, n_small=0)
    out = S.score_universe(df, {"quality": 1}, min_peer=3)
    best = out.loc[out.company_id == "T5", "detail_obj"].iloc[0]["metrics"]["roic_5y_median"]["percentile"]
    raw = (6 - 0.5) / 6 * 100
    assert best == pytest.approx(50 + (raw - 50) * 6 / S.FULL_CONF_OBS, abs=0.1)


def test_negative_ebitda_with_debt_is_worst():
    df = _universe()
    df.loc[df.company_id == "T11", "neg_ebitda_with_debt"] = 1     # would otherwise be the least indebted
    out = S.score_universe(df, {"quality": 1}, min_peer=8)
    pcts = {r.company_id: r.detail_obj["metrics"]["net_debt_ebitda"]["percentile"] for r in out.itertuples()
            if r.sector == "Technology"}
    assert min(pcts, key=pcts.get) == "T11"


def test_fallback_metric_is_not_counted_twice():
    df = _universe()
    df["roic"] = df["roic_5y_median"]
    df.loc[df.company_id == "T3", "roic_5y_median"] = np.nan
    out = S.score_universe(df, {"quality": 1}, min_peer=8)
    det = out.loc[out.company_id == "T3", "detail_obj"].iloc[0]
    assert "roic_5y_median→roic" in det["fallbacks"]
    assert det["metrics"]["roic"]["weight"] == pytest.approx(3.0)   # 2 (5y slot) + 1 (own slot), merged once


# ================================================================================ classification
def _row(**kw):
    base = {"robust_score": 60.0, "quality": 50.0, "valuation": 50.0, "growth": 50.0}
    base.update(kw)
    return pd.Series(base)


@pytest.mark.parametrize("kw, flags, expected", [
    ({"robust_score": np.nan}, [], S.C_NODATA),
    ({"quality": 85, "valuation": 80}, [{"code": "GOING_CONCERN_TEXT", "severity": "high", "message": "gc"}], S.C_REDFLAG),
    ({"quality": 85, "valuation": 80}, [{"code": "X", "severity": "high", "message": "a"},
                                        {"code": "Y", "severity": "high", "message": "b"}], S.C_REDFLAG),
    ({"quality": 85, "valuation": 70}, [], S.C_QDISC),
    ({"quality": 85, "valuation": 70, "pe_vs_history_pct": 0.2}, [], S.C_TEMP),
    ({"quality": 85, "valuation": 70, "drawdown_from_3y_high": -0.4}, [], S.C_TEMP),
    ({"quality": 85, "valuation": 50, "revenue_cagr_5y": -0.02}, [], S.C_QDET),
    ({"quality": 85, "valuation": 50, "growth": 20}, [], S.C_QDET),
    ({"quality": 30, "valuation": 75}, [], S.C_TRAP),
    ({"quality": 55, "valuation": 80, "revenue_cagr_3y": -0.05}, [], S.C_TRAP),
    ({"quality": 85, "valuation": 30}, [], S.C_QFULL),
    ({"quality": 85, "valuation": 50}, [], S.C_QFAIR),
    ({"quality": 50, "valuation": 20, "growth": 85}, [], S.C_GROWTH),
    ({"quality": 50, "valuation": 75}, [], S.C_CHEAP),
    ({}, [], S.C_AVG),
])
def test_classify_row(kw, flags, expected):
    cls, why = S.classify_row(_row(**kw), flags)
    assert cls == expected, why
    assert why


def test_single_high_flag_blocks_positive_labels():
    cls, why = S.classify_row(_row(quality=85, valuation=70), [{"code": "X", "severity": "high", "message": "debito"}])
    assert cls not in S.QUALITY_DISCOUNT and "debito" in why


def test_multi_year_decline_is_deterioration():
    reasons = S.is_deteriorating(_row(revenue_cagr_5y=-0.01, fcf_ps_cagr_5y=-0.08, op_margin_trend=-0.04), [])
    assert any("5 anni" in r for r in reasons)
    assert any("free cash flow per azione" in r for r in reasons)
    assert any("margine operativo" in r for r in reasons)


# ================================================================================ verdict & confidence
def test_verdict_cheap_with_high_confidence():
    r = _row(valuation_peers=80, pe_vs_history_pct=0.2, ev_ebit_vs_history_pct=0.2, implied_fcf_growth=0.03,
             growth_gap=-0.05, earnings_yield_after_tax=0.09)
    verdict, conf, sig = S.verdict_for(r, 0.04, [])
    assert verdict == "relativamente economica" and conf == "alta" and len(sig) == 4


def test_cyclical_peak_neutralises_cheapness_signals():
    r = _row(valuation_peers=50, pe_vs_history_pct=0.1, implied_fcf_growth=0.02, growth_gap=-0.06)
    verdict, conf, sig = S.verdict_for(r, 0.04, [{"code": "CYCLICAL_PEAK"}])
    assert [s.weight for s in sig if s.name != "Rispetto ai concorrenti"] == [0.0, 0.0]
    assert verdict == "ragionevolmente valutata"


def test_opposite_signals_give_low_confidence():
    v, conf, _ = valuation_verdict([Signal("a", "economica", ""), Signal("b", "costosa", ""), Signal("c", "ragionevole", "")])
    assert v == "ragionevolmente valutata" and conf == "bassa"
    v, conf, _ = valuation_verdict([])
    assert v == "non determinabile" and conf == "nessuna"


def test_confidence_levels():
    good = pd.Series({"coverage": 0.9, "data_tier": "A", "years_of_data": 10, "rank_spread": 0.05})
    assert S.confidence_of(good, []) == "alta"
    assert S.confidence_of(good.copy().replace({"A": "B"}), []) == "media"
    unstable = good.copy()
    unstable["rank_spread"] = 0.4
    assert S.confidence_of(unstable, []) == "media"
    assert S.confidence_of(good, [{"severity": "data"}, {"severity": "data"}]) == "media"
    poor = pd.Series({"coverage": 0.5, "data_tier": "B", "years_of_data": 3})
    assert S.confidence_of(poor, []) == "bassa"


# ================================================================================ reverse DCF
def test_implied_growth_inverts_dcf():
    v = dcf_value(100.0, 0.07, 0.09, 0.025, 10)
    g, status = implied_growth(v, 100.0, 0.09, 0.025, 10)
    assert status == "ok" and g == pytest.approx(0.07, abs=1e-6)


def test_implied_growth_out_of_range_and_invalid_inputs():
    g, status = implied_growth(1e12, 1.0, 0.09, 0.025, 10)
    assert g is None and "fuori dall'intervallo" in status
    g, status = implied_growth(1000.0, 10.0, 0.02, 0.025, 10)
    assert g is None and "tasso di sconto" in status
    g, status = implied_growth(1000.0, -5.0, 0.09, 0.025, 10)
    assert g is None


def test_fcf_base_requires_all_recent_years_positive():
    idx = pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31"])
    base, method = normalized_fcf(pd.Series([10.0, -2.0, 12.0], index=idx), None, None)
    assert base is None and "negativo" in method
    base, method = normalized_fcf(pd.Series([10.0, 11.0, 12.0], index=idx), 13.0, pd.Timestamp("2025-06-30"))
    assert base == pytest.approx(np.mean([10.0, 11.0, 13.0])) and "12 mesi" in method


def test_terminal_growth_capped_at_risk_free():
    idx = pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31"])
    rd = reverse_dcf(1000.0, pd.Series([50.0, 52.0, 55.0], index=idx), None, None, 0.01, "test", 0.05, 0.025, 10)
    assert rd.terminal_growth == pytest.approx(0.01) and rd.discount_rate == pytest.approx(0.06)
    rd = reverse_dcf(1000.0, pd.Series([50.0, 52.0, 55.0], index=idx), None, None, None, "", 0.05, 0.025, 10)
    assert "ASSUNZIONE" in rd.risk_free_source


def test_percentile_vs_history_needs_enough_points():
    assert percentile_vs_history(15.0, pd.Series([10.0, 20.0, 30.0])) is None
    assert percentile_vs_history(15.0, pd.Series([10.0, 12.0, 20.0, 25.0, 30.0])) == pytest.approx(0.4)


# ================================================================================ text red flags
# Sentences written in the style of US 10-K filings (factual vs hypothetical vs negated).
GC_FACT = ("These conditions raise substantial doubt about the Company’s ability to continue as a going concern "
           "within one year after the date that the financial statements are issued.")
GC_HYPO = "If we are unable to obtain additional financing, there could be substantial doubt about our ability to continue as a going concern."
GC_NEG = "Management’s plans have alleviated the substantial doubt about the Company's ability to continue as a going concern."
MW_FACT = ("Based on this evaluation, management concluded that our internal control over financial reporting was not "
           "effective as of December 31, 2025 because of the material weakness described below.")
MW_HYPO = "We may identify material weaknesses in the future that could cause us to fail to meet our reporting obligations."
MW_NEG = "Management did not identify any material weakness in our internal control over financial reporting."
RS_FACT = "As a result, we restated our previously issued consolidated financial statements for the fiscal years 2023 and 2024."
RS_NEG = "We have not restated any of our previously issued financial statements."
IMP_FACT = "During the fourth quarter we recorded a goodwill impairment charge of $412 million in the Industrial segment."


@pytest.mark.parametrize("text, code, expected", [
    (GC_FACT, "GOING_CONCERN_TEXT", True), (GC_HYPO, "GOING_CONCERN_TEXT", False), (GC_NEG, "GOING_CONCERN_TEXT", False),
    (MW_FACT, "MATERIAL_WEAKNESS", True), (MW_HYPO, "MATERIAL_WEAKNESS", False), (MW_NEG, "MATERIAL_WEAKNESS", False),
    (RS_FACT, "RESTATEMENT_TEXT", True), (RS_NEG, "RESTATEMENT_TEXT", False),
    (IMP_FACT, "IMPAIRMENT_TEXT", True),
])
def test_text_red_flags(text, code, expected):
    filler = "The Company designs and sells products in several markets around the world. "
    flags = text_red_flags(filler + text + " " + filler, "10-K test", "https://example.invalid/doc")
    assert (code in {f["code"] for f in flags}) is expected
    for f in flags:
        assert f["evidence"]["snippet"] and f["evidence"]["url"]


# ================================================================================ portfolio constraints
def test_caps_respected_when_feasible():
    idx = [f"C{i}" for i in range(20)]
    w = pd.Series(np.linspace(1, 3, 20), index=idx)
    w = w / w.sum()
    sectors = pd.Series(["A", "B", "C", "D"] * 5, index=idx)
    regions = pd.Series(["Nord America"] * 10 + ["Europa"] * 10, index=idx)
    out, rep = enforce_caps(w, sectors, regions, 0.025, 0.08, 0.30, {"Nord America": 0.65, "Europa": 0.55})
    assert out.sum() == pytest.approx(1.0)
    assert all(c["rispettato"] for c in rep), rep
    assert out.max() <= 0.08 + 1e-6 and out.groupby(sectors).sum().max() <= 0.30 + 1e-6
    assert out.groupby(regions).sum()["Nord America"] <= 0.65 + 1e-6


def test_infeasible_caps_are_reported_not_hidden():
    idx = [f"C{i}" for i in range(6)]
    w = pd.Series(1 / 6, index=idx)
    sectors = pd.Series(["A"] * 6, index=idx)
    regions = pd.Series(["Nord America"] * 6, index=idx)
    log = []
    out, rep = enforce_caps(w, sectors, regions, 0.025, 0.08, 0.25, {"Nord America": 0.65}, log)
    assert out.sum() == pytest.approx(1.0)
    bad = {c["vincolo"] for c in rep if not c["rispettato"]}
    assert {"peso massimo per titolo", "peso massimo per settore", "peso massimo area Nord America"} <= bad
    assert sum("VINCOLO NON RISPETTATO" in line for line in log) == len(bad)


def _scores(n):
    return pd.DataFrame({
        "company_id": [f"C{i}" for i in range(n)], "ticker": [f"T{i}" for i in range(n)], "name": "x",
        "robust_score": np.linspace(90, 60, n), "robust_percentile": 90.0, "classification": S.C_QFAIR,
        "confidence": "alta", "sector": [["A", "B", "C", "D", "E"][i % 5] for i in range(n)],
        "region": [["Nord America", "Europa"][i % 2] for i in range(n)], "quality": 80.0, "valuation": 60.0,
        "growth": 60.0, "financial_strength": 60.0, "deep_analysis": True, "price_currency": "EUR"})


def _weekly(n, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=160, freq="W-FRI")
    return pd.DataFrame(rng.normal(0.002, 0.03, size=(160, n)), index=idx, columns=[f"C{i}" for i in range(n)])


def _cfg(**kw):
    raw = {"portfolio": {"target_positions": 20, "min_positions": 12, "max_weight": 0.08, "min_weight": 0.025,
                         "max_sector_weight": 0.25, "max_pair_correlation": 0.8, "min_robust_percentile": 70,
                         "min_weeks_3y": 130, "max_region_weight": {"Nord America": 0.65, "Europa": 0.45}}}
    raw["portfolio"].update(kw)
    return Config(raw=raw)


def test_portfolio_not_proposed_below_min_positions():
    port = construct_portfolio(_scores(6), _weekly(6), _cfg())
    assert port["status"] == "non proposto"
    assert any("NON proposto" in line for line in port["log"])


def test_portfolio_proposed_and_region_caps_reported():
    port = construct_portfolio(_scores(30), _weekly(30), _cfg())
    assert port["status"] == "proposto" and len(port["positions"]) >= 12
    regions = {c["vincolo"] for c in port["constraints"]}
    assert "peso massimo area Nord America" in regions and "peso massimo area Europa" in regions
    for c in port["constraints"]:
        if c["vincolo"] != "peso minimo per titolo":
            assert c["rispettato"] == (c["effettivo"] <= c["configurato"] + 1e-6)


def test_excluded_classes_never_enter_portfolio():
    sc = _scores(30)
    sc.loc[sc.company_id == "C0", "classification"] = S.C_REDFLAG
    sc.loc[sc.company_id == "C1", "classification"] = S.C_TRAP
    sc.loc[sc.company_id == "C2", "confidence"] = "bassa"
    port = construct_portfolio(sc, _weekly(30), _cfg())
    assert not {"C0", "C1", "C2"} & {p["company_id"] for p in port["positions"]}
