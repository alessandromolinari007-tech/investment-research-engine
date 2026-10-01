"""Standardized line items and the source concepts that map to them.

For each item the lists are in PRIORITY order: for every fiscal period the first
concept that has a value wins. `companyfacts` only contains non-dimensional
(consolidated total) facts, so all candidates represent company-wide totals.
Yahoo row names are the labels used by yfinance's statement DataFrames.
"""
from __future__ import annotations

DURATION = "duration"
INSTANT = "instant"

ITEMS: dict[str, dict] = {
    # ---------------------------------------------------------------- income statement
    "revenue": {
        "type": DURATION,
        "us-gaap": [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "SalesRevenueServicesNet",
            "RevenuesNetOfInterestExpense",
            "InterestAndDividendIncomeOperating",
        ],
        "ifrs-full": ["Revenue", "RevenueFromContractsWithCustomers"],
        "yahoo": ["Total Revenue", "Operating Revenue"],
    },
    "cost_of_revenue": {
        "type": DURATION,
        "us-gaap": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
                     "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
        "ifrs-full": ["CostOfSales"],
        "yahoo": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
    },
    "gross_profit": {
        "type": DURATION,
        "us-gaap": ["GrossProfit"],
        "ifrs-full": ["GrossProfit"],
        "yahoo": ["Gross Profit"],
    },
    "operating_income": {
        "type": DURATION,
        "us-gaap": ["OperatingIncomeLoss"],
        "ifrs-full": ["ProfitLossFromOperatingActivities"],
        "yahoo": ["Operating Income", "Total Operating Income As Reported"],
    },
    "pretax_income": {
        "type": DURATION,
        "us-gaap": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ],
        "ifrs-full": ["ProfitLossBeforeTax"],
        "yahoo": ["Pretax Income"],
    },
    "income_tax": {
        "type": DURATION,
        "us-gaap": ["IncomeTaxExpenseBenefit"],
        "ifrs-full": ["IncomeTaxExpenseContinuingOperations"],
        "yahoo": ["Tax Provision"],
    },
    "net_income": {  # attributable to parent shareholders
        "type": DURATION,
        "us-gaap": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"],
        "ifrs-full": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
        "yahoo": ["Net Income Common Stockholders", "Net Income", "Net Income From Continuing Operation Net Minority Interest"],
    },
    "interest_expense": {
        "type": DURATION,
        "us-gaap": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                     "InterestAndDebtExpense"],
        "ifrs-full": ["FinanceCosts", "InterestExpense"],
        "yahoo": ["Interest Expense", "Interest Expense Non Operating"],
    },
    "da": {
        "type": DURATION,
        "us-gaap": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                     "DepreciationAmortizationAndAccretionNet", "Depreciation"],
        "ifrs-full": ["DepreciationAndAmortisationExpense", "AdjustmentsForDepreciationAndAmortisationExpense"],
        "yahoo": ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"],
    },
    "sga": {
        "type": DURATION,
        "us-gaap": ["SellingGeneralAndAdministrativeExpense"],
        "ifrs-full": ["SellingGeneralAndAdministrativeExpense", "AdministrativeExpense"],
        "yahoo": ["Selling General And Administration"],
    },
    "rnd": {
        "type": DURATION,
        "us-gaap": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
        "ifrs-full": ["ResearchAndDevelopmentExpense"],
        "yahoo": ["Research And Development"],
    },
    "eps_diluted": {
        "type": DURATION,
        "per_share": True,
        "us-gaap": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
        "ifrs-full": ["DilutedEarningsLossPerShare", "BasicAndDilutedEarningsLossPerShare"],
        "yahoo": ["Diluted EPS"],
    },
    "shares_diluted": {  # weighted average diluted shares for the period
        "type": DURATION,
        "shares": True,
        "us-gaap": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"],
        "ifrs-full": ["AdjustedWeightedAverageShares", "WeightedAverageShares"],
        "yahoo": ["Diluted Average Shares"],
    },
    # ---------------------------------------------------------------- cash flow
    "ocf": {
        "type": DURATION,
        "us-gaap": ["NetCashProvidedByUsedInOperatingActivities",
                     "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
        "ifrs-full": ["CashFlowsFromUsedInOperatingActivities"],
        "yahoo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    },
    "capex": {  # reported as positive outflow
        "type": DURATION,
        "us-gaap": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
                     "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment",
                     "PaymentsToAcquireOilAndGasPropertyAndEquipment", "PaymentsToAcquireOilAndGasProperty",
                     "PaymentsToAcquireRealEstate", "PaymentsToDevelopRealEstateAssets",
                     "PaymentsToAcquireMachineryAndEquipment"],
        "ifrs-full": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                       "PurchaseOfPropertyPlantAndEquipment"],
        "yahoo": ["Capital Expenditure"],  # negative in Yahoo → sign fixed in normalizer
    },
    "sbc": {
        "type": DURATION,
        "us-gaap": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
        "ifrs-full": ["AdjustmentsForSharebasedPayments", "ExpenseFromSharebasedPaymentTransactionsWithEmployees"],
        "yahoo": ["Stock Based Compensation"],
    },
    "dividends_paid": {
        "type": DURATION,
        "us-gaap": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
        "ifrs-full": ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid",
                       "DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities"],
        "yahoo": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    },
    "buybacks": {
        "type": DURATION,
        "us-gaap": ["PaymentsForRepurchaseOfCommonStock"],
        "ifrs-full": ["PaymentsToAcquireOrRedeemEntitysShares"],
        "yahoo": ["Repurchase Of Capital Stock", "Common Stock Payments"],
    },
    "share_issuance": {
        "type": DURATION,
        "us-gaap": ["ProceedsFromIssuanceOfCommonStock", "ProceedsFromStockOptionsExercised"],
        "ifrs-full": ["ProceedsFromIssuingShares"],
        "yahoo": ["Issuance Of Capital Stock", "Common Stock Issuance"],
    },
    "acquisitions": {
        "type": DURATION,
        "us-gaap": ["PaymentsToAcquireBusinessesNetOfCashAcquired", "PaymentsToAcquireBusinessesGross"],
        "ifrs-full": ["CashFlowsUsedInObtainingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities"],
        "yahoo": ["Purchase Of Business", "Net Business Purchase And Sale"],   # only outflows kept (see normalizer)
    },
    # ---------------------------------------------------------------- balance sheet
    "cash": {
        "type": INSTANT,
        "us-gaap": ["CashAndCashEquivalentsAtCarryingValue", "CashAndDueFromBanks",
                     "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
        "ifrs-full": ["CashAndCashEquivalents"],
        "yahoo": ["Cash And Cash Equivalents", "Cash Financial"],
    },
    "short_term_investments": {
        "type": INSTANT,
        "us-gaap": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
        "ifrs-full": ["ShorttermInvestments", "CurrentInvestments"],
        "yahoo": ["Other Short Term Investments"],
    },
    "receivables": {
        "type": INSTANT,
        "us-gaap": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
        "ifrs-full": ["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"],
        "yahoo": ["Accounts Receivable", "Receivables"],
    },
    "inventory": {
        "type": INSTANT,
        "us-gaap": ["InventoryNet"],
        "ifrs-full": ["Inventories"],
        "yahoo": ["Inventory"],
    },
    "current_assets": {
        "type": INSTANT,
        "us-gaap": ["AssetsCurrent"],
        "ifrs-full": ["CurrentAssets"],
        "yahoo": ["Current Assets"],
    },
    "ppe_net": {
        "type": INSTANT,
        "us-gaap": ["PropertyPlantAndEquipmentNet",
                     "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
        "ifrs-full": ["PropertyPlantAndEquipment"],
        "yahoo": ["Net PPE"],
    },
    "goodwill": {
        "type": INSTANT,
        "us-gaap": ["Goodwill"],
        "ifrs-full": ["Goodwill"],
        "yahoo": ["Goodwill"],
    },
    "intangibles": {
        "type": INSTANT,
        "us-gaap": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
        "ifrs-full": ["IntangibleAssetsOtherThanGoodwill"],
        "yahoo": ["Other Intangible Assets"],
    },
    "total_assets": {
        "type": INSTANT,
        "us-gaap": ["Assets"],
        "ifrs-full": ["Assets"],
        "yahoo": ["Total Assets"],
    },
    "current_liabilities": {
        "type": INSTANT,
        "us-gaap": ["LiabilitiesCurrent"],
        "ifrs-full": ["CurrentLiabilities"],
        "yahoo": ["Current Liabilities"],
    },
    "total_liabilities": {
        "type": INSTANT,
        "us-gaap": ["Liabilities"],
        "ifrs-full": ["Liabilities"],
        "yahoo": ["Total Liabilities Net Minority Interest"],
    },
    "equity": {  # attributable to parent
        "type": INSTANT,
        "us-gaap": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "ifrs-full": ["EquityAttributableToOwnersOfParent", "Equity"],
        "yahoo": ["Stockholders Equity", "Common Stock Equity"],
    },
    "minority_interest": {
        "type": INSTANT,
        "us-gaap": ["MinorityInterest"],
        "ifrs-full": ["NoncontrollingInterests"],
        "yahoo": ["Minority Interest"],
    },
    "retained_earnings": {
        "type": INSTANT,
        "us-gaap": ["RetainedEarningsAccumulatedDeficit"],
        "ifrs-full": ["RetainedEarnings"],
        "yahoo": ["Retained Earnings"],
    },
    # ---- debt components (combined in the normalizer, see debt rules) ----
    "debt_total_reported": {
        "type": INSTANT,
        "us-gaap": ["DebtAndCapitalLeaseObligations", "DebtLongtermAndShorttermCombinedAmount"],
        "ifrs-full": ["Borrowings"],
        "yahoo": ["Total Debt"],
    },
    "debt_lt_total": {  # long-term debt INCLUDING current maturities (excludes commercial paper / ST borrowings)
        "type": INSTANT,
        "us-gaap": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                     "NotesPayable", "SeniorNotes", "ConvertibleNotesPayable", "SecuredDebt", "UnsecuredDebt",
                     "OtherLongTermDebt"],
        "ifrs-full": ["LongtermBorrowings"],
    },
    "debt_noncurrent": {
        "type": INSTANT,
        "us-gaap": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable",
                     "SeniorLongTermNotes", "ConvertibleLongTermNotesPayable", "LongTermLineOfCredit",
                     "OtherLongTermDebtNoncurrent", "SecuredLongTermDebt", "UnsecuredLongTermDebt"],
        "ifrs-full": ["NoncurrentPortionOfNoncurrentBorrowings", "NoncurrentBorrowings",
                       "NoncurrentPortionOfLongtermBorrowings"],
        "yahoo": ["Long Term Debt"],
    },
    "debt_current": {  # all short-term debt incl. current maturities
        "type": INSTANT,
        "us-gaap": ["DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
        "ifrs-full": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "ShorttermBorrowings"],
        "yahoo": ["Current Debt"],
    },
    "debt_lt_current": {
        "type": INSTANT,
        "us-gaap": ["LongTermDebtCurrent"],
        "ifrs-full": ["CurrentPortionOfLongtermBorrowings"],
    },
    "st_borrowings": {
        "type": INSTANT,
        "us-gaap": ["ShortTermBorrowings", "CommercialPaper", "LinesOfCreditCurrent", "NotesPayableCurrent",
                     "ConvertibleNotesPayableCurrent"],
        "ifrs-full": [],
    },
    # ---- leases (IFRS 16: lease liabilities are financial debt; EBITDA excludes lease costs) ----
    "lease_liab_total": {
        "type": INSTANT,
        "us-gaap": [],
        "ifrs-full": ["LeaseLiabilities"],
    },
    "lease_liab_noncurrent": {
        "type": INSTANT,
        "us-gaap": [],
        "ifrs-full": ["NoncurrentLeaseLiabilities"],
    },
    "lease_liab_current": {
        "type": INSTANT,
        "us-gaap": [],
        "ifrs-full": ["CurrentLeaseLiabilities"],
    },
    "lease_payments": {  # principal repayments of lease liabilities (financing cash flow under IFRS 16)
        "type": DURATION,
        "us-gaap": [],
        "ifrs-full": ["PaymentsOfLeaseLiabilitiesClassifiedAsFinancingActivities", "PaymentsOfLeaseLiabilities"],
    },
    "preferred_equity": {
        "type": INSTANT,
        "us-gaap": ["PreferredStockValue", "PreferredStockValueOutstanding"],
        "ifrs-full": [],
        "yahoo": ["Preferred Stock Equity", "Preferred Stock"],
    },
    "shares_outstanding": {  # point-in-time count (cover page or balance sheet)
        "type": INSTANT,
        "shares": True,
        "dei": ["EntityCommonStockSharesOutstanding"],
        "us-gaap": ["CommonStockSharesOutstanding"],
        "ifrs-full": ["NumberOfSharesOutstanding"],
        "yahoo": ["Ordinary Shares Number", "Share Issued"],
    },
}

# Items whose Yahoo values are reported as negatives for outflows → store as positive outflows
YAHOO_NEGATIVE_OUTFLOWS = {"capex", "dividends_paid", "buybacks", "acquisitions"}

INTEREST_BEARING_DEBT_NOTE = (
    "Debito finanziario = debito a lungo termine (incluse quote correnti) + debito a breve. "
    "Per i bilanci IFRS (IFRS 16) si aggiungono le passività per leasing, perché l'EBITDA IFRS esclude il costo "
    "dei leasing; per i bilanci US GAAP i leasing operativi restano esclusi (il loro costo è già nell'EBITDA). "
    "Per le banche il debito non è una misura significativa."
)
