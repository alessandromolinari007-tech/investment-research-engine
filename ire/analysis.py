"""Per-company analysis: market cap (currency/ADR aware), metrics, valuation, risk, red flags."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .metrics import MetricSet, compute_fundamental_metrics
from .normalize.model import Financials
from .risk import price_risk_metrics
from .sources.fx_macro import FxTable
from .valuation import ReverseDCF, historical_multiples, percentile_vs_history, reverse_dcf

CYCLICAL_SECTORS = {"Energy", "Basic Materials", "Industrials", "Consumer Cyclical"}
COMMON_ADR_RATIOS = [0.1, 0.2, 0.25, 1 / 3, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100]


@dataclass
class CompanyAnalysis:
    company_id: str
    metrics: MetricSet
    flags: list[dict[str, Any]] = field(default_factory=list)
    rdcf: ReverseDCF | None = None
    hist_multiples: pd.DataFrame | None = None
    market: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def _flag(code, severity, message, evidence=None, source="analisi interna"):
    return {"code": code, "severity": severity, "message": message, "evidence": evidence, "source": source}


def market_cap_fin_ccy(company: dict[str, Any], fin: Financials, info: dict[str, Any], price_main: float | None,
                       fx: FxTable) -> tuple[float | None, dict[str, Any], list[dict], list[dict]]:
    """Returns (market cap in financial-statement currency, details, flags, conflicts)."""
    flags, conflicts = [], []
    pcur = company.get("price_currency")
    fcur = fin.currency
    det: dict[str, Any] = {"price": price_main, "price_currency": pcur, "fin_currency": fcur}
    if price_main is None or pcur is None or fcur is None:
        return None, det, [_flag("NO_PRICE", "data", "Prezzo o valuta non disponibili: valutazione impossibile")], conflicts
    y_sh = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
    mcap_y = price_main * y_sh if y_sh else None
    det["shares_yahoo"] = y_sh
    mcap_p = None
    method = ""
    if fin.tier == "A" and company.get("filer_type") == "domestic":
        sh = fin.latest_instant("shares_outstanding") or fin.last("shares_diluted")
        det["shares_sec"] = sh
        if sh:
            mcap_sec = price_main * sh
            if mcap_y and abs(mcap_sec / mcap_y - 1) > 0.15:
                conflicts.append({"item": "market_cap", "period_end": None, "value_a": mcap_sec, "source_a": "prezzo × azioni SEC",
                                  "value_b": mcap_y, "source_b": "prezzo × azioni Yahoo",
                                  "pct_diff": mcap_sec / mcap_y - 1,
                                  "likely_reason": "più classi di azioni con prezzi diversi, conteggio azioni non aggiornato o ticker di una sola classe"})
                mcap_p, method = mcap_y, "prezzo × azioni implicite Yahoo (discrepanza con conteggio SEC >15%)"
            else:
                mcap_p, method = mcap_sec, "prezzo × azioni in circolazione (copertina 10-K/10-Q SEC)"
        else:
            mcap_p, method = mcap_y, "prezzo × azioni Yahoo (conteggio SEC non disponibile)"
    else:
        mcap_p, method = mcap_y, "prezzo × azioni Yahoo"
        if fin.tier == "A":  # foreign filer: infer ADR ratio from ordinary shares in filings
            sh_ord = fin.latest_instant("shares_outstanding") or fin.last("shares_diluted")
            if sh_ord and y_sh:
                ratio = sh_ord / y_sh
                nearest = min(COMMON_ADR_RATIOS, key=lambda r: abs(math.log(ratio / r)))
                det["adr_ratio_inferred"] = ratio
                det["adr_ratio"] = nearest if abs(math.log(ratio / nearest)) < 0.06 else ratio
                if abs(math.log(ratio / nearest)) >= 0.06:
                    flags.append(_flag("ADR_RATIO_UNCERTAIN", "data",
                                       f"Rapporto tra azioni ordinarie (filing) e azioni Yahoo = {ratio:.3f}: non corrisponde a un "
                                       "rapporto ADR standard; capitalizzazione da verificare"))
    if mcap_p is None:
        return None, det, [_flag("NO_MARKET_CAP", "data", "Capitalizzazione non determinabile")], conflicts
    det["market_cap_price_ccy"] = mcap_p
    det["market_cap_method"] = method
    if pcur != fcur:
        mc = fx.convert(mcap_p, pcur, fcur)
        if mc is None:
            return None, det, [_flag("NO_FX", "data", f"Cambio {pcur}/{fcur} non disponibile: valutazione impossibile")], conflicts
        det["fx_note"] = f"convertita da {pcur} a {fcur} al cambio più recente (BCE o Yahoo)"
        mcap_p = mc
    return mcap_p, det, flags, conflicts


def analyze_company(company: dict[str, Any], fin: Financials, info: dict[str, Any], price_main: float | None,
                    close_local: pd.Series | None, adj_local: pd.Series | None, fx: FxTable,
                    bench_eur: pd.Series | None, rates: dict[str, tuple[float, str]], cfg) -> CompanyAnalysis:
    banklike = bool(company.get("is_banklike"))
    reit = bool(company.get("is_reit"))
    mcap, mdet, mflags, conflicts = market_cap_fin_ccy(company, fin, info, price_main, fx)
    m = compute_fundamental_metrics(fin, mcap, price_main, banklike=banklike, reit=reit, yahoo=info)
    res = CompanyAnalysis(company["company_id"], m, list(fin.flags) + mflags, market=mdet, conflicts=conflicts)

    # --------------------------------------------------------------- price risk
    if close_local is not None and adj_local is not None and len(close_local):
        pcur = company.get("price_currency") or "USD"
        adj_eur = fx.series_to_eur(adj_local, pcur)
        pr = price_risk_metrics(close_local, adj_local, adj_eur, bench_eur)
        method = {"vol_1y": "deviazione standard annualizzata dei rendimenti giornalieri, 1 anno, in EUR",
                  "vol_3y": "come sopra, 3 anni", "vol_1y_local": "volatilità 1 anno in valuta locale",
                  "max_drawdown_5y": "massima perdita dal picco negli ultimi 5 anni (prezzi rettificati per dividendi, EUR)",
                  "max_drawdown_10y": "massima perdita dal picco negli ultimi 10 anni",
                  "beta_world": "beta dei rendimenti settimanali (3 anni, EUR) rispetto a MSCI World",
                  "drawdown_from_52w_high": "distanza del prezzo dal massimo delle ultime 52 settimane",
                  "drawdown_from_3y_high": "distanza del prezzo dal massimo degli ultimi 3 anni",
                  "momentum_12_1": "rendimento 12 mesi escluso l'ultimo mese (fattore momentum)",
                  "return_1y": "rendimento totale 1 anno in EUR", "return_5y_ann": "rendimento totale annuo 5 anni in EUR"}
        for k, v in pr.items():
            m.add(k, v, "calculated", "prezzi fino a " + str(close_local.index.max().date()), method.get(k, k))
        last = close_local.index.max()
        if (pd.Timestamp.today() - last).days > 7:
            res.flags.append(_flag("STALE_PRICE", "data", f"Ultimo prezzo del {last.date()}"))

    # --------------------------------------------------------------- reverse DCF
    fcur = fin.currency
    rf, rf_src = rates.get(fcur, (None, ""))
    if not banklike and mcap:
        rd = reverse_dcf(mcap, fin.series("fcf"), fin.ttm.get("fcf"), fin.ttm_end, rf, rf_src,
                         float(cfg.get("valuation.equity_risk_premium", 0.05)),
                         float(cfg.get("valuation.terminal_growth", 0.025)),
                         int(cfg.get("valuation.dcf_years", 10)), fin.ttm.get("sbc"))
        res.rdcf = rd
        m.add("implied_fcf_growth", rd.implied_g, "estimate", f"{rd.years} anni",
              f"crescita annua del FCF implicita nel prezzo (reverse DCF): tasso di sconto {rd.discount_rate:.1%} "
              f"(risk-free {rd.risk_free:.1%} + premio {rd.erp:.1%}), crescita perpetua {rd.terminal_growth:.1%}, "
              f"FCF base = {rd.fcf_base_method}. {rd.status}",
              fcf_base=rd.fcf_base, discount_rate=rd.discount_rate, risk_free_source=rd.risk_free_source)
        # compare like with like: implied growth of TOTAL FCF vs historical growth of TOTAL FCF (or revenue)
        hist_g = m.val("fcf_cagr_5y") if m.val("fcf_cagr_5y") is not None else m.val("revenue_cagr_5y")
        if rd.implied_g is not None and hist_g is not None:
            m.add("growth_gap", rd.implied_g - hist_g, "estimate", "",
                  "crescita implicita nel prezzo − crescita storica 5 anni (FCF totale o ricavi). "
                  "Negativo = il prezzo sembra scontare meno crescita di quella già realizzata (stima di modello)",
                  implied=rd.implied_g, historical=hist_g)

    # --------------------------------------------------------------- valuation vs own history
    if close_local is not None and len(close_local) and mcap:
        mcap_hist = _historical_mcaps(fin, close_local, company, mdet, fx)
        if mcap_hist:
            hm = historical_multiples(fin.annual, mcap_hist)
            res.hist_multiples = hm
            if not hm.empty:
                for key, cur_metric, label in (("pe", "pe", "P/E"), ("p_fcf", None, "P/FCF"), ("ev_ebit", "ev_ebit", "EV/EBIT"),
                                               ("ps", "ps", "P/S")):
                    cur = m.val(cur_metric) if cur_metric else _div(mcap, fin.ttm.get("fcf"))
                    if key not in hm.columns:
                        continue
                    pct = percentile_vs_history(cur, hm[key])
                    med = hm[key].dropna()
                    med = med[(med > 0) & (med < 1000)]
                    m.add(f"{key}_hist_median", float(med.median()) if len(med) >= 5 else None, "calculated",
                          f"{len(med)} fine anno fiscale", f"mediana storica del {label} a fine anno fiscale")
                    m.add(f"{key}_vs_history_pct", pct, "calculated", f"{len(med)} anni",
                          f"percentile del {label} attuale rispetto alla propria storia (0 = mai stato così basso, "
                          "1 = mai così alto)")

    # --------------------------------------------------------------- fundamental red flags
    res.flags.extend(fundamental_flags(m, fin, company))
    return res


def _div(a, b):
    try:
        return a / b if a is not None and b is not None and b > 0 else None
    except TypeError:
        return None


def _historical_mcaps(fin: Financials, close_local: pd.Series, company: dict[str, Any], mdet: dict[str, Any],
                      fx: FxTable) -> dict[pd.Timestamp, float]:
    """Market cap at each fiscal-year end in financial currency, using split-adjusted close
    and split-adjusted weighted diluted shares. Validated against the current market cap."""
    sh = fin.series("shares_diluted")
    if sh.empty or any(f["code"] == "POSSIBLE_UNADJUSTED_SPLIT" for f in fin.flags):
        return {}
    ratio = float(mdet.get("adr_ratio") or 1.0)
    pcur, fcur = company.get("price_currency"), fin.currency
    out = {}
    c = close_local.dropna()
    for dt, n in sh.items():
        px = c.loc[:dt]
        if px.empty or (dt - px.index[-1]).days > 7:
            continue
        mc = float(px.iloc[-1]) * float(n) / ratio
        if pcur != fcur:
            mc = fx.convert(mc, pcur, fcur, on=px.index[-1])
            if mc is None:
                continue
        out[dt] = mc
    # validation: latest reconstructed cap vs current method (should be within ~35%: shares/price moved since FY end)
    if out and mdet.get("market_cap_price_ccy"):
        last_dt = max(out)
        cur_px = mdet.get("price")
        px_then = c.loc[:last_dt].iloc[-1]
        cur_cap = mdet["market_cap_price_ccy"]
        if pcur != fcur:
            cur_cap = fx.convert(cur_cap, pcur, fcur)
        if cur_px and px_then and cur_cap:
            implied_now = out[last_dt] * cur_px / px_then
            if not (0.65 < implied_now / cur_cap < 1.5):
                return {}
    return out


def fundamental_flags(m: MetricSet, fin: Financials, company: dict[str, Any]) -> list[dict[str, Any]]:
    f = []
    v = m.val
    banklike = bool(company.get("is_banklike"))
    sector = company.get("sector") or ""
    heavy_debt_ok = sector in ("Utilities", "Real Estate")
    eq = fin.latest_instant("equity")
    if eq is not None and eq < 0:
        f.append(_flag("NEGATIVE_EQUITY", "medium",
                       "Patrimonio netto negativo: ROE e P/B non significativi. Può derivare da forti riacquisti di azioni "
                       "(caso non grave) o da perdite accumulate (caso grave): verifica."))
    if not banklike:
        nde = v("net_debt_ebitda")
        if nde is not None and not heavy_debt_ok:
            if nde > 6:
                f.append(_flag("HIGH_LEVERAGE", "high", f"Debito netto pari a {nde:.1f}× EBITDA: leva molto elevata"))
            elif nde > 4:
                f.append(_flag("HIGH_LEVERAGE", "medium", f"Debito netto pari a {nde:.1f}× EBITDA: leva elevata"))
        ic = v("interest_coverage")
        if ic is not None:
            if ic < 1.5:
                f.append(_flag("LOW_INTEREST_COVERAGE", "high", f"L'utile operativo copre gli interessi solo {ic:.1f} volte"))
            elif ic < 3:
                f.append(_flag("LOW_INTEREST_COVERAGE", "medium", f"Copertura interessi bassa ({ic:.1f}×)"))
        fcf = fin.series("fcf")
        if len(fcf) >= 2 and fcf.iloc[-1] < 0 and fcf.iloc[-2] < 0:
            f.append(_flag("NEGATIVE_FCF", "medium", "Free cash flow negativo negli ultimi due anni fiscali: l'azienda consuma cassa"))
        om_t = v("op_margin_trend")
        if om_t is not None and om_t < -0.05:
            f.append(_flag("MARGIN_DETERIORATION", "medium",
                           f"Margine operativo ultimo anno {om_t * 100:.1f} punti sotto la mediana 5 anni"))
        z = v("altman_z")
        if z is not None and z < 1.8 and sector not in ("Utilities", "Real Estate", "Financial Services"):
            f.append(_flag("ALTMAN_DISTRESS", "medium", f"Altman Z = {z:.2f} (<1.8, zona di stress finanziario)"))
        bm = v("beneish_m")
        if bm is not None and bm > -1.78:
            f.append(_flag("BENEISH_WARNING", "medium",
                           f"Beneish M-score {bm:.2f} > −1.78: profilo contabile da approfondire (molti falsi positivi nelle aziende in forte crescita)"))
        acc = v("accruals_ratio")
        if acc is not None and acc > 0.10:
            f.append(_flag("HIGH_ACCRUALS", "medium", f"Utili superiori al cassa generata ({acc:.0%} dell'attivo): qualità degli utili bassa"))
        sbc = v("sbc_to_revenue")
        if sbc is not None and sbc > 0.10:
            f.append(_flag("HIGH_SBC", "medium", f"Compensi in azioni pari al {sbc:.0%} dei ricavi: costo reale per gli azionisti"))
        gw = v("goodwill_to_assets")
        if gw is not None and gw > 0.5:
            f.append(_flag("HIGH_GOODWILL", "info", f"Avviamento e intangibili = {gw:.0%} dell'attivo: crescita per acquisizioni, rischio svalutazioni"))
        debt = fin.series("total_debt")
        if len(debt) >= 2 and debt.iloc[-2] > 0 and debt.iloc[-1] / debt.iloc[-2] > 1.5 and (nde or 0) > 2:
            f.append(_flag("DEBT_SURGE", "medium", f"Debito aumentato del {debt.iloc[-1] / debt.iloc[-2] - 1:.0%} nell'ultimo anno"))
        om, omv = v("operating_margin"), v("op_margin_volatility")
        om10 = v("op_margin_10y_median") if v("op_margin_10y_median") is not None else v("op_margin_5y_median")
        if om is not None and om10 is not None and om10 > 0 and om > 1.5 * om10 and om - om10 > 0.03 and \
                ((omv is not None and omv > 0.04) or sector in CYCLICAL_SECTORS):
            f.append(_flag("CYCLICAL_PEAK", "medium",
                           f"Margine operativo attuale {om:.0%} contro una mediana di ciclo di {om10:.0%}: utili probabilmente "
                           "vicini al picco del ciclo. P/E bassi e crescita storica possono essere ingannevoli."))
        ebit_t, ni_t = fin.ttm.get("ebit"), fin.ttm.get("net_income")
        int_t, rev_t = fin.ttm.get("interest_expense") or 0.0, fin.ttm.get("revenue")
        if ebit_t and ni_t is not None and rev_t:
            expected = (ebit_t - int_t) * (1 - 0.21)
            if expected > 0 and abs(ni_t - expected) > 0.3 * abs(expected) and abs(ni_t - expected) > 0.02 * rev_t:
                f.append(_flag("ONE_OFF_ITEMS", "info",
                               f"Utile netto ({ni_t:.3g}) molto diverso da quanto implicito nell'utile operativo (≈{expected:.3g}): "
                               "probabili componenti straordinarie (svalutazioni, plusvalenze, effetti fiscali). P/E da leggere con cautela."))
    rg = v("revenue_growth_last_fy")
    if rg is not None and rg < -0.10:
        f.append(_flag("REVENUE_DECLINE", "medium", f"Ricavi in calo del {-rg:.0%} nell'ultimo anno fiscale"))
    dil = v("share_change_cagr_5y")
    if dil is not None:
        if dil > 0.08:
            f.append(_flag("HEAVY_DILUTION", "high", f"Numero di azioni in crescita del {dil:.1%} l'anno (5 anni): forte diluizione"))
        elif dil > 0.03:
            f.append(_flag("DILUTION", "medium", f"Numero di azioni in crescita del {dil:.1%} l'anno (5 anni): diluizione"))
    pay, fpay = v("payout_ratio"), v("fcf_payout")
    if pay is not None and pay > 1 and (fpay is None or fpay > 1):
        f.append(_flag("UNCOVERED_DIVIDEND", "medium", "Dividendo superiore a utili e free cash flow: sostenibilità dubbia"))
    ni = fin.series("net_income")
    if len(ni) >= 2 and ni.iloc[-1] < 0 and ni.iloc[-2] < 0:
        f.append(_flag("PERSISTENT_LOSSES", "medium", "Perdite nette negli ultimi due anni fiscali"))
    age = v("data_age_days")
    if age is not None and age > 450:
        f.append(_flag("STALE_FUNDAMENTALS", "data", f"Ultimo bilancio disponibile di {age:.0f} giorni fa: dati vecchi"))
    yrs = v("years_of_data")
    if yrs is not None and yrs < 4:
        f.append(_flag("SHORT_HISTORY", "data", f"Solo {yrs:.0f} anni di bilanci: metriche di crescita e stabilità poco affidabili"))
    restated = [r for r in fin.fact_rows if r.get("restated") and r["item"] in ("revenue", "net_income")
                and r["period_end"] >= str((pd.Timestamp.today() - pd.DateOffset(years=3)).date())
                and r.get("original_value") and abs(r["value"] / r["original_value"] - 1) > 0.02]
    if restated:
        r0 = restated[0]
        f.append(_flag("VALUES_REVISED", "info",
                       f"{len(restated)} valori chiave rivisti in filing successivi (es. {r0['item']} {r0['period_end']}: "
                       f"{r0['original_value']:.4g} → {r0['value']:.4g}); spesso riclassificazioni, a volte restatement",
                       source="SEC XBRL companyfacts"))
    return f
