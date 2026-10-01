import math
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from ire.normalize.sec_facts import normalize_companyfacts  # noqa: E402
from tests.fixtures.builders import FactsBuilder  # noqa: E402


def base_company():
    b = FactsBuilder()
    # revenue: older years tagged SalesRevenueNet, newer Revenues (concept switch)
    b.annual("SalesRevenueNet", {2019: 800, 2020: 900})
    b.annual("Revenues", {2021: 1000, 2022: 1100, 2023: 1210, 2024: 1331, 2025: 1464})
    b.annual("CostOfRevenue", {y: v * 0.4 for y, v in {2019: 800, 2020: 900, 2021: 1000, 2022: 1100, 2023: 1210, 2024: 1331, 2025: 1464}.items()})
    b.annual("OperatingIncomeLoss", {2019: 160, 2020: 180, 2021: 200, 2022: 220, 2023: 242, 2024: 266, 2025: 293})
    b.annual("NetIncomeLoss", {2019: 120, 2020: 135, 2021: 150, 2022: 165, 2023: 180, 2024: 200, 2025: 220})
    b.annual("NetCashProvidedByUsedInOperatingActivities", {y: 250 for y in range(2019, 2026)})
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in range(2019, 2026)})
    b.annual("DepreciationDepletionAndAmortization", {y: 40 for y in range(2019, 2026)})
    b.annual("Assets", {y: 2000 for y in range(2019, 2026)}, instant=True)
    b.annual("StockholdersEquity", {y: 1000 for y in range(2019, 2026)}, instant=True)
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in range(2019, 2026)}, instant=True)
    b.annual("LongTermDebtCurrent", {y: 20 for y in range(2019, 2026)}, instant=True)
    b.annual("CashAndCashEquivalentsAtCarryingValue", {y: 100 for y in range(2019, 2026)}, instant=True)
    b.annual("WeightedAverageNumberOfDilutedSharesOutstanding", {y: 100 for y in range(2019, 2026)}, unit="shares")
    return b


def test_annual_and_concept_switch():
    fin = normalize_companyfacts("T", base_company().build())
    assert fin.currency == "USD"
    rev = fin.series("revenue")
    assert list(rev.index.year) == list(range(2019, 2026))
    assert rev.iloc[0] == 800 and rev.iloc[-1] == 1464
    assert fin.last("gross_profit") == pytest.approx(1464 * 0.6)
    assert fin.last("total_debt") == 320
    assert fin.last("fcf") == 200
    assert fin.last("ebitda") == 333
    # never reported → assumed zero, recorded
    assert "buybacks" in fin.assumed_zero
    assert fin.last("buybacks") == 0


def test_restatement_latest_wins_and_flagged():
    b = base_company()
    # 2024 net income restated in the 2025 10-K from 200 to 190
    b.add("NetIncomeLoss", 190, "2024-12-31", start="2024-01-01", form="10-K", filed="2026-02-15")
    fin = normalize_companyfacts("T", b.build())
    assert fin.annual.loc["2024-12-31", "net_income"] == 190
    row = [r for r in fin.fact_rows if r["item"] == "net_income" and r["period_end"] == "2024-12-31"][0]
    assert row["restated"] == 1 and row["original_value"] == 200


def test_ttm_rollforward():
    b = base_company()
    for concept, ytd_cur, ytd_prev in [
        ("Revenues", 800, 700), ("NetIncomeLoss", 130, 100), ("NetCashProvidedByUsedInOperatingActivities", 140, 120),
        ("PaymentsToAcquirePropertyPlantAndEquipment", 30, 25), ("OperatingIncomeLoss", 160, 140),
        ("CostOfRevenue", 320, 280), ("DepreciationDepletionAndAmortization", 20, 20),
    ]:
        b.add(concept, ytd_cur, "2026-06-30", start="2026-01-01", form="10-Q", filed="2026-08-01")
        b.add(concept, ytd_prev, "2025-06-30", start="2025-01-01", form="10-Q", filed="2026-08-01")
    fin = normalize_companyfacts("T", b.build())
    assert fin.ttm_end == pd.Timestamp("2026-06-30")
    assert fin.ttm["revenue"] == pytest.approx(1464 + 800 - 700)
    assert fin.ttm["net_income"] == pytest.approx(220 + 30)
    assert fin.ttm["fcf"] == pytest.approx((250 + 20) - (50 + 5))


def test_ttm_partial_does_not_mix_periods():
    b = base_company()
    # only revenue has interim data: net income / ocf missing → must not mix periods
    b.add("Revenues", 800, "2026-06-30", start="2026-01-01", form="10-Q", filed="2026-08-01")
    b.add("Revenues", 700, "2025-06-30", start="2025-01-01", form="10-Q", filed="2026-08-01")
    fin = normalize_companyfacts("T", b.build())
    assert fin.ttm["revenue"] == 1464
    assert fin.ttm_end == pd.Timestamp("2025-12-31")


def test_split_adjustment():
    b = FactsBuilder()
    b.annual("Revenues", {2022: 100, 2023: 110, 2024: 120})
    b.annual("NetIncomeLoss", {2022: 10, 2023: 11, 2024: 12})
    # 2022 and 2023 share counts filed before a 10:1 split on 2024-06-10, 2024 after
    b.add("WeightedAverageNumberOfDilutedSharesOutstanding", 100, "2022-12-31", start="2022-01-01", filed="2023-02-15", unit="shares")
    b.add("WeightedAverageNumberOfDilutedSharesOutstanding", 101, "2023-12-31", start="2023-01-01", filed="2024-02-15", unit="shares")
    b.add("WeightedAverageNumberOfDilutedSharesOutstanding", 1020, "2024-12-31", start="2024-01-01", filed="2025-02-15", unit="shares")
    b.add("EarningsPerShareDiluted", 0.1, "2022-12-31", start="2022-01-01", filed="2023-02-15", unit="USD/shares")
    fin = normalize_companyfacts("T", b.build(), splits=[("2024-06-10", 10.0)])
    sh = fin.series("shares_diluted")
    assert sh.iloc[0] == 1000 and sh.iloc[1] == 1010 and sh.iloc[2] == 1020
    assert fin.series("eps_diluted").iloc[0] == pytest.approx(0.01)
    assert not [f for f in fin.flags if f["code"] == "POSSIBLE_UNADJUSTED_SPLIT"]


def test_unadjusted_split_flagged():
    b = FactsBuilder()
    b.annual("Revenues", {2022: 100, 2023: 110})
    b.annual("NetIncomeLoss", {2022: 10, 2023: 11})
    b.add("WeightedAverageNumberOfDilutedSharesOutstanding", 100, "2022-12-31", start="2022-01-01", filed="2023-02-15", unit="shares")
    b.add("WeightedAverageNumberOfDilutedSharesOutstanding", 400, "2023-12-31", start="2023-01-01", filed="2024-02-15", unit="shares")
    fin = normalize_companyfacts("T", b.build(), splits=[])
    assert [f for f in fin.flags if f["code"] == "POSSIBLE_UNADJUSTED_SPLIT"]


def test_share_classes_summed_on_cover():
    b = base_company()
    accn = "0000000000-26-999999"
    b.add("EntityCommonStockSharesOutstanding", 60, "2026-01-31", form="10-K", filed="2026-02-15", unit="shares", tax="dei", accn=accn)
    b.add("EntityCommonStockSharesOutstanding", 40, "2026-01-31", form="10-K", filed="2026-02-15", unit="shares", tax="dei", accn=accn)
    fin = normalize_companyfacts("T", b.build())
    assert fin.latest["shares_outstanding"][0] == 100


def test_ifrs_20f_no_interim():
    b = FactsBuilder()
    b.annual("Revenue", {2023: 5000, 2024: 5500, 2025: 6000}, form="20-F", unit="EUR", tax="ifrs-full")
    b.annual("ProfitLossAttributableToOwnersOfParent", {2023: 500, 2024: 550, 2025: 600}, form="20-F", unit="EUR", tax="ifrs-full")
    b.annual("CashFlowsFromUsedInOperatingActivities", {2023: 700, 2024: 750, 2025: 800}, form="20-F", unit="EUR", tax="ifrs-full")
    b.annual("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", {2023: 100, 2024: 100, 2025: 100}, form="20-F", unit="EUR", tax="ifrs-full")
    fin = normalize_companyfacts("T", b.build())
    assert fin.currency == "EUR"
    assert fin.ttm["revenue"] == 6000
    assert fin.ttm["fcf"] == 700
    assert "ultimo anno fiscale" in fin.ttm_derivation
    # no debt concept and no balance sheet to support "zero debt" → debt unknown, never a silent 0
    assert "total_debt" not in fin.assumed_zero
    assert any(f["code"] == "DEBT_UNKNOWN" for f in fin.flags)
    assert fin.annual["total_debt"].isna().all()


def test_zero_debt_assumed_only_with_balance_sheet_support():
    b = FactsBuilder()
    for tag, vals in (("Revenues", {2023: 1000, 2024: 1100, 2025: 1200}),
                      ("NetIncomeLoss", {2023: 100, 2024: 110, 2025: 120}),
                      ("NetCashProvidedByUsedInOperatingActivities", {2023: 150, 2024: 160, 2025: 170}),
                      ("PaymentsToAcquirePropertyPlantAndEquipment", {2023: 20, 2024: 20, 2025: 20})):
        b.annual(tag, vals)
    b.annual("Assets", {2023: 2000, 2024: 2100, 2025: 2200}, instant=True)
    b.annual("Liabilities", {2023: 400, 2024: 420, 2025: 440}, instant=True)
    b.annual("LiabilitiesCurrent", {2023: 350, 2024: 380, 2025: 400}, instant=True)
    fin = normalize_companyfacts("Z", b.build())
    assert "total_debt" in fin.assumed_zero
    assert any(f["code"] == "ASSUMED_ZERO_DEBT" and f["severity"] == "data" for f in fin.flags)


def test_unmapped_capex_is_not_zero_and_convertible_debt_is_found():
    b = FactsBuilder()
    for tag, vals in (("Revenues", {2023: 1000, 2024: 1100, 2025: 1200}),
                      ("NetIncomeLoss", {2023: 100, 2024: 110, 2025: 120}),
                      ("NetCashProvidedByUsedInOperatingActivities", {2023: 150, 2024: 160, 2025: 170}),
                      ("InterestExpense", {2023: 30, 2024: 30, 2025: 30})):
        b.annual(tag, vals)
    b.annual("Assets", {2023: 3000, 2024: 3100, 2025: 3200}, instant=True)
    b.annual("PropertyPlantAndEquipmentNet", {2023: 900, 2024: 950, 2025: 1000}, instant=True)
    b.annual("ConvertibleNotesPayable", {2023: 2000, 2024: 2000, 2025: 2000}, instant=True)
    fin = normalize_companyfacts("C", b.build())
    assert fin.annual["total_debt"].iloc[-1] == 2000
    assert "capex" not in fin.assumed_zero
    assert fin.annual["fcf"].isna().all()
    assert any(f["code"] == "CAPEX_UNKNOWN" for f in fin.flags)
