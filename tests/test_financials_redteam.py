"""Regression tests for the accounting findings of the second red team. All companyfacts are SYNTHETIC
(tests/fixtures/builders.py), with hand-checkable numbers."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from ire.metrics import compute_fundamental_metrics  # noqa: E402
from ire.normalize.sec_facts import normalize_companyfacts  # noqa: E402
from ire.normalize.yahoo_facts import normalize_yahoo  # noqa: E402
from tests.fixtures.builders import FactsBuilder  # noqa: E402

YEARS = range(2020, 2026)


def core(b=None, op_income=True):
    b = b or FactsBuilder()
    b.annual("Revenues", {y: 1000 for y in YEARS})
    if op_income:
        b.annual("OperatingIncomeLoss", {y: 200 for y in YEARS})
    b.annual("NetIncomeLoss", {y: 150 for y in YEARS})
    b.annual("NetCashProvidedByUsedInOperatingActivities", {y: 500 for y in YEARS})
    b.annual("DepreciationDepletionAndAmortization", {y: 100 for y in YEARS})
    b.annual("Assets", {y: 3000 for y in YEARS}, instant=True)
    b.annual("Liabilities", {y: 1500 for y in YEARS}, instant=True)
    b.annual("LiabilitiesCurrent", {y: 400 for y in YEARS}, instant=True)
    b.annual("StockholdersEquity", {y: 1500 for y in YEARS}, instant=True)
    b.annual("CashAndCashEquivalentsAtCarryingValue", {y: 100 for y in YEARS}, instant=True)
    b.annual("WeightedAverageNumberOfDilutedSharesOutstanding", {y: 100 for y in YEARS}, unit="shares")
    return b


def test_capex_components_are_summed():
    b = core()
    b.annual("PaymentsToAcquireOilAndGasPropertyAndEquipment", {y: 400 for y in YEARS})
    b.annual("PaymentsToAcquireOtherPropertyPlantAndEquipment", {y: 10 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("capex") == pytest.approx(410)
    assert fin.last("fcf") == pytest.approx(90)


def test_inclusive_ppe_total_not_double_counted():
    b = core()
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 450 for y in YEARS})
    b.annual("PaymentsToAcquireOilAndGasPropertyAndEquipment", {y: 400 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("capex") == pytest.approx(450)


@pytest.mark.parametrize("concepts, expected", [
    # current maturities only (not all current debt) + commercial paper
    ({"LongTermDebtNoncurrent": 1000, "LongTermDebtAndCapitalLeaseObligationsCurrent": 100, "CommercialPaper": 400}, 1500),
    # LongTermDebt includes its current portion, DebtCurrent too → no double counting
    ({"LongTermDebt": 1100, "DebtCurrent": 100}, 1100),
])
def test_debt_components(concepts, expected):
    b = core()
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    for c, v in concepts.items():
        b.annual(c, {y: v for y in YEARS}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("total_debt") == pytest.approx(expected)


def test_ifrs_current_portion_not_lost():
    b = FactsBuilder()
    for c, v in {"Revenue": 1000, "ProfitLoss": 150, "CashFlowsFromUsedInOperatingActivities": 500,
                 "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": 50}.items():
        b.annual(c, {y: v for y in YEARS}, tax="ifrs-full", unit="EUR", form="20-F")
    for c, v in {"Assets": 3000, "NoncurrentPortionOfNoncurrentBorrowings": 1000, "ShorttermBorrowings": 200,
                 "CurrentPortionOfLongtermBorrowings": 150}.items():
        b.annual(c, {y: v for y in YEARS}, tax="ifrs-full", unit="EUR", form="20-F", instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("total_debt") == pytest.approx(1350)


def test_items_no_longer_reported_are_not_stale():
    b = core()
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {2020: 900, 2021: 900, 2022: 900}, instant=True)   # repaid, line disappears
    b.annual("ShortTermInvestments", {2020: 500}, instant=True)
    b.annual("Goodwill", {2020: 300, 2021: 300}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.latest_instant("short_term_investments") == 0.0
    assert fin.latest_instant("goodwill") == 0.0
    assert fin.latest_instant("total_debt") in (None, 0.0)
    assert fin.latest_instant("total_debt") != 900


def test_ebitda_and_ttm_ebit_without_operating_income():
    b = core(op_income=False)
    b.annual("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             {y: 120 for y in YEARS})
    b.annual("InterestExpense", {y: 30 for y in YEARS})
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("ebit") == pytest.approx(150)
    assert fin.last("ebitda") == pytest.approx(250)
    m = compute_fundamental_metrics(fin, 2000.0)
    assert m.val("operating_margin") == pytest.approx(0.15)
    assert m.val("net_debt_ebitda") is not None


def test_three_for_two_split_is_detected():
    b = core()
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    b.facts["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"].clear()
    b.annual("WeightedAverageNumberOfDilutedSharesOutstanding", {2020: 100, 2021: 100, 2022: 100, 2023: 150, 2024: 150,
                                                                 2025: 150}, unit="shares")
    fin = normalize_companyfacts("T", b.build(), apply_splits=False)
    assert any(f["code"] == "POSSIBLE_UNADJUSTED_SPLIT" for f in fin.flags)
    m = compute_fundamental_metrics(fin, 2000.0)
    assert m.val("share_change_cagr_5y") is None              # no false HEAVY_DILUTION from a split


def test_us_gaap_finance_leases_in_debt_and_fcf():
    b = core()
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    b.annual("FinanceLeaseLiability", {y: 200 for y in YEARS}, instant=True)
    b.annual("FinanceLeasePrincipalPayments", {y: 40 for y in YEARS})
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("total_debt") == pytest.approx(500)
    assert fin.last("fcf") == pytest.approx(500 - 50 - 40)


def test_reporting_currency_switch_keeps_recent_years():
    b = FactsBuilder()
    for c, v in {"Revenue": 1000, "ProfitLoss": 100, "CashFlowsFromUsedInOperatingActivities": 300}.items():
        b.annual(c, {y: v for y in range(2010, 2020)}, tax="ifrs-full", unit="USD", form="20-F")
        b.annual(c, {y: v for y in range(2020, 2026)}, tax="ifrs-full", unit="EUR", form="20-F")
    fin = normalize_companyfacts("T", b.build())
    assert fin.currency == "EUR" and fin.annual.index.max().year == 2025


def test_total_liabilities_derived_when_not_tagged():
    b = core()
    del b.facts["us-gaap"]["Liabilities"]
    b.annual("LiabilitiesAndStockholdersEquity", {y: 3000 for y in YEARS}, instant=True)
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    assert fin.last("total_liabilities") == pytest.approx(1500)


def test_preferred_in_enterprise_value():
    b = core()
    b.annual("PaymentsToAcquirePropertyPlantAndEquipment", {y: 50 for y in YEARS})
    b.annual("LongTermDebtNoncurrent", {y: 300 for y in YEARS}, instant=True)
    b.annual("PreferredStockValue", {y: 250 for y in YEARS}, instant=True)
    fin = normalize_companyfacts("T", b.build())
    m = compute_fundamental_metrics(fin, 2000.0)
    assert m.val("enterprise_value") == pytest.approx(2000 + 300 + 250 - 100)


def test_yahoo_shareholder_yield_counts_issuance():
    q = {d: None for d in ("2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31")}    # TTM newer than last FY
    annual = {f"{y}-12-31": {"Total Revenue": 1000.0, "Net Income": 100.0} for y in range(2022, 2026)}
    cash = {f"{y}-12-31": {"Operating Cash Flow": 300.0, "Capital Expenditure": -50.0, "Cash Dividends Paid": -50.0,
                           "Repurchase Of Capital Stock": -30.0, "Issuance Of Capital Stock": 160.0}
            for y in range(2022, 2026)}
    qi = {d: {"Total Revenue": 250.0, "Net Income": 25.0} for d in q}
    qc = {d: {"Operating Cash Flow": 75.0, "Capital Expenditure": -12.5, "Cash Dividends Paid": -12.5,
              "Repurchase Of Capital Stock": -7.5, "Issuance Of Capital Stock": 40.0} for d in q}
    st = {"income_annual": annual, "balance_annual": {}, "cashflow_annual": cash,
          "income_quarterly": qi, "balance_quarterly": {}, "cashflow_quarterly": qc}
    fin = normalize_yahoo("YF:T", "T", st, "EUR")
    assert "share_issuance" in fin.ttm                       # issuance rolled into TTM like dividends/buybacks
    m = compute_fundamental_metrics(fin, 1000.0)
    assert m.val("shareholder_yield") == pytest.approx((50 + 30 - 160) / 1000)
