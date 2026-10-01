"""Investment Research Engine — interfaccia (Streamlit).

Avvio:  python -m ire app   (oppure run_app.bat)
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import charts  # noqa: E402
import data  # noqa: E402
from ire.classify import SECTOR_IT  # noqa: E402
from ire.glossary import GLOSSARY, KIND_IT, explain, fmt, label, money, num_it  # noqa: E402
from ire.scoring import (C_AVG, C_CHEAP, C_GROWTH, C_NODATA, C_QDET, C_QDISC, C_QFAIR, C_QFULL,  # noqa: E402
                         C_REDFLAG, C_TEMP, C_TRAP, NEGATIVE_CLASSES, PILLARS, QUALITY_DISCOUNT)

st.set_page_config(page_title="Investment Research Engine", page_icon="📈", layout="wide")

SEV_ICON = {"severe": "🔴", "high": "🟠", "medium": "🟡", "info": "🔵", "data": "⚪"}
SEV_IT = {"severe": "grave", "high": "alta", "medium": "media", "info": "informativa", "data": "qualità dati"}
CLASS_ICON = {
    C_TEMP: "💎", C_QDISC: "💎", C_QFAIR: "✅", C_QFULL: "🏷️", C_QDET: "📉", C_GROWTH: "🚀", C_CHEAP: "🔎",
    C_TRAP: "⚠️", C_REDFLAG: "🚩", C_AVG: "➖", C_NODATA: "❔",
}
VERDICT_ICON = {"relativamente economica": "🟢", "ragionevolmente valutata": "⚪", "costosa": "🔴", "non determinabile": "❔"}
DIRECTION = {m: d for p in PILLARS.values() for prof in p.values() for m, d, _ in prof}
DIRECTION.update({"fcf_margin": 1, "operating_margin": 1, "net_margin": 1, "roe": 1, "pe": -1, "ev_ebit": -1,
                  "fcf_yield": 1, "vol_1y": -1, "max_drawdown_5y": 1, "implied_fcf_growth": -1, "dividend_yield": 1,
                  "beta_world": -1, "pb": -1})

RUN = data.latest_run()
STATUS_IT = {"completed": "completata", "failed": "fallita", "interrupted": "interrotta", "running": "in corso",
             "degraded": "incompleta (troppi dati mancanti o non aggiornati)"}


def esc(text) -> str:
    """Streamlit markdown reads `$…$` as LaTeX: escape dollars in texts coming from data."""
    return str(text if text is not None else "").replace("$", "\\$")


def run_banner():
    """Make failed / interrupted runs visible instead of silently showing an older analysis."""
    r = data.runs()
    if r.empty:
        return
    last = r.iloc[0]
    if last["status"] == "completed":
        return
    when = str(last["started_at"] or "")[:16].replace("T", " ")
    msg = f"L'ultima analisi (#{last['run_id']} del {when} UTC) risulta **{STATUS_IT.get(last['status'], last['status'])}**."
    try:
        summ = json.loads(last["summary"] or "{}")
    except ValueError:
        summ = {}
    if summ.get("error"):
        msg += f" Motivo: {esc(str(summ['error'])[:300])}"
    if summ.get("health"):
        msg += " Problemi: " + esc("; ".join(summ["health"])) + "."
    if RUN is not None:
        msg += f" Qui vedi l'ultima analisi completata (#{RUN})."
    msg += " Il log completo è nella pagina *Dati e metodologia*."
    (st.info if last["status"] == "running" else st.warning)(msg)


def clamp(v, lo, hi) -> float:
    return float(min(max(float(v), lo), hi))


def sector_it(s):
    return SECTOR_IT.get(s or "Unknown", s or "Sconosciuto")


def go_company(cid: str):
    st.session_state["cid"] = cid
    st.session_state["tbl_gen"] = st.session_state.get("tbl_gen", 0) + 1   # resets table selections
    st.switch_page(PAGES["company"])


def tkey(name: str) -> str:
    return f"{name}_{st.session_state.get('tbl_gen', 0)}"


def no_data():
    run_banner()
    st.warning("Nessuna analisi completata trovata. Esegui prima la pipeline: **run_pipeline.bat** "
               "(oppure `python -m ire run --mode quick` per un primo test).")
    st.stop()


def metric_table(mdf: pd.DataFrame, keys: list[str], currency: str | None, pcts: dict | None = None) -> pd.DataFrame:
    rows = []
    for k in keys:
        if k not in mdf.index:
            continue
        r = mdf.loc[k]
        p = (pcts or {}).get(k, {}).get("percentile")
        rows.append({"Metrica": label(k), "Valore": fmt(k, r["value"], currency),
                     "Percentile nel settore": p if p is not None else None,
                     "Cosa significa": explain(k) or "", "Tipo": KIND_IT.get(r["kind"], r["kind"]),
                     "Periodo": r["period"], "Come si calcola": r["method"]})
    return pd.DataFrame(rows)


# =====================================================================================
def page_home():
    st.title("📈 Investment Research Engine")
    if RUN is None:
        no_data()
    U = data.universe(RUN)
    r = data.runs()
    run = r[r.run_id == RUN].iloc[0]
    run_banner()
    st.caption(f"Analisi #{RUN} del {str(run['finished_at'])[:16].replace('T', ' ')} UTC · modalità {run['mode']} · "
               "Strumento di ricerca personale: non è un consiglio di investimento.")
    scored = U[U["robust_score"].notna()]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Società analizzate", f"{len(U)}")
    c2.metric("Con punteggio completo", f"{len(scored)}")
    c3.metric("Bilanci da filing SEC (qualità A)", f"{(U.data_tier == 'A').sum()}")
    c4.metric("Paesi", f"{U['country'].nunique()}")

    with st.expander("👋 Come leggere questo strumento (2 minuti)", expanded=False):
        st.markdown("""
- Ogni azienda riceve **5 punteggi da 0 a 100 relativi al proprio settore**: Qualità, Crescita, Solidità finanziaria,
  Valutazione (100 = molto economica) e Allocazione del capitale. 50 = nella media dei concorrenti.
- Il **punteggio robusto** è la mediana di 5 combinazioni di pesi diverse: se una società è in alto solo con certi pesi,
  lo vedi dalla *stabilità della classifica*.
- La **classificazione** distingue un'azienda *economica* da una *di qualità* e da una *di qualità a sconto rispetto ai pari*,
  e segnala le possibili **value trap** (economiche ma in deterioramento).
- La **valutazione** (economica / ragionevole / costosa) combina 4 segnali indipendenti e dice quanti sono d'accordo (confidenza).
- Il **reverse DCF** risponde a: *quanta crescita sta già pagando il prezzo attuale?*
- Ogni numero ha **fonte, data e metodo**: nella scheda azienda, sezione *Fonti e dati*.
- Nessun punteggio prevede il futuro: servono a **scegliere cosa studiare**, non cosa comprare a occhi chiusi.
""")

    def show(df, title, note):
        st.subheader(title)
        st.caption(note)
        if df.empty:
            st.info("Nessuna società in questa categoria nell'ultima analisi.")
            return
        cols = ["ticker", "name", "sector", "country", "robust_score", "quality", "valuation", "valuation_verdict",
                "valuation_confidence", "confidence"]
        view = df[cols].copy()
        view["sector"] = view["sector"].map(sector_it)
        ev = st.dataframe(
            view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
            key=tkey(f"tbl_{title}"),
            column_config={
                "ticker": "Ticker", "name": "Società", "sector": "Settore", "country": "Paese",
                "robust_score": st.column_config.ProgressColumn("Punteggio robusto", min_value=0, max_value=100, format="%.0f"),
                "quality": st.column_config.NumberColumn("Qualità", format="%.0f"),
                "valuation": st.column_config.NumberColumn("Economicità", format="%.0f"),
                "valuation_verdict": "Valutazione", "valuation_confidence": "Confidenza del verdetto",
                "confidence": "Affidabilità dei dati"})
        if ev and ev.selection and ev.selection.rows:
            go_company(df.iloc[ev.selection.rows[0]]["company_id"])

    top = scored.sort_values("robust_score", ascending=False)
    show(top[top.classification.isin(QUALITY_DISCOUNT)].head(20),
         "💎 Qualità a sconto vs pari", "Qualità alta e multipli bassi rispetto ai pari secondo i dati disponibili; i controlli "
         "automatici di deterioramento e le principali segnalazioni finanziarie non sono scattati. È un elenco da "
         "studiare, non da comprare: controlla verdetto e segnalazioni nella scheda.")
    show(top[top.classification == C_QFAIR].head(15),
         "✅ Qualità a prezzo ragionevole", "Qualità sopra i pari (≥ 70/100) e valutazione non estrema: controlla il "
         "verdetto di valutazione nella tabella.")
    big = top.sort_values("market_cap_eur", ascending=False, na_position="last")
    show(big[big.classification.isin(NEGATIVE_CLASSES)].head(15),
         "🚩 Da maneggiare con cura (grandi società)", "Red flag gravi o apparente economicità con fondamentali in peggioramento.")
    st.divider()
    st.caption("Distribuzione delle classificazioni")
    st.bar_chart(U["classification"].value_counts())


# =====================================================================================
def page_company():
    if RUN is None:
        no_data()
    U = data.universe(RUN).sort_values("robust_score", ascending=False, na_position="last")
    if U.empty:
        st.warning("L'ultima analisi completata non contiene società con dati: controlla il log nella pagina "
                   "*Dati e metodologia*.")
        st.stop()
    labels = list(U["label"])
    ids = list(U["company_id"])
    cid = st.session_state.get("cid") or st.query_params.get("cid") or (ids[0] if ids else None)
    idx = ids.index(cid) if cid in ids else 0
    choice = st.selectbox("🔎 Cerca un'azienda (ticker o nome)", labels, index=idx)
    cid = ids[labels.index(choice)]
    st.session_state["cid"] = cid
    d = data.company(cid, RUN)
    c, sc, det = d["company"], d["score"], d["detail"]
    mdf = d["metrics"].set_index("metric") if not d["metrics"].empty else pd.DataFrame(columns=["value"])
    mv = lambda k: (mdf.loc[k, "value"] if k in mdf.index else None)  # noqa: E731
    cur = c.get("fin_currency")
    pcts = (det.get("percentiles") or {}).get("metrics", {})

    # ---------------------------------------------------------------- header
    st.title(f"{c.get('name') or c.get('ticker')}")
    tier = "A · bilanci dai filing SEC (XBRL)" if c.get("data_tier") == "A" else "B · bilanci da Yahoo Finance (storico breve)"
    alt = json.loads(c.get("other_listings") or "[]")
    st.caption(f"**{c.get('ticker')}** · {c.get('exchange') or ''} · {sector_it(c.get('sector'))} / {c.get('industry') or ''} · "
               f"{c.get('country') or ''} · Qualità dati: {tier}"
               + (f" · Altre quotazioni: {', '.join(alt)}" if alt else ""))
    mk = d["market"]
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Prezzo", f"{num_it(mk.get('price'), 2)} {mk.get('price_currency')}" if mk.get("price") else "n/d",
              help=f"Ultima chiusura disponibile ({mk.get('as_of')}) da Yahoo Finance")
    h2.metric("Capitalizzazione", money(mv("market_cap"), cur), help=d["market_extra"].get("market_cap_method"))
    h3.metric("Punteggio robusto", f"{sc.get('robust_score'):.0f}/100" if sc.get("robust_score") is not None else "n/d",
              help="Mediana dei punteggi sotto 5 schemi di pesi")
    h4.metric("Affidabilità del punteggio", sc.get("confidence") or "n/d",
              help="Dipende da copertura dei dati, qualità della fonte, anni di storia e stabilità della classifica")

    k1, k2 = st.columns([1.1, 1])
    with k1:
        cls = sc.get("classification") or C_NODATA
        st.markdown(f"### {CLASS_ICON.get(cls, '')} {cls}")
        th = det.get("thesis") or {}
        st.markdown(esc(th.get("headline", "").split(" — ", 1)[-1]))
        verdict = sc.get("valuation_verdict") or "non determinabile"
        st.markdown(f"**Valutazione:** {VERDICT_ICON.get(verdict, '')} {verdict} · confidenza "
                    f"**{sc.get('valuation_confidence') or 'n/d'}**")
        for s in det.get("signals", []):
            icon = {"economica": "🟢", "ragionevole": "⚪", "costosa": "🔴"}.get(s["verdict"], "")
            st.markdown(f"- {icon} *{s['name']}*: {esc(s['detail'])}")
        vp = det.get("valuation_peers")
        if vp is not None:
            st.caption(f"Economicità rispetto ai pari considerando SOLO i multipli: {vp:.0f}/100 "
                       "(il punteggio 'Valutazione' include anche storia e crescita implicita).")
    with k2:
        if th.get("pillars"):
            st.plotly_chart(charts.pillar_bars(th["pillars"]), width="stretch", config={"displayModeBar": False})
            st.caption(f"Punteggi 0-100 rispetto a: **{sc.get('peer_group')}** (50 = mediana). Valutazione alta = più economica.")

    # ---------------------------------------------------------------- thesis
    st.subheader("🧭 Tesi d'investimento (interpretazione automatica)")
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Punti di forza**")
        for s in th.get("strengths", []) or [{"text": "Nessun punto di forza marcato rispetto ai pari."}]:
            st.markdown(f"- ✅ {esc(s['text'])}")
        st.markdown("**Cosa stai pagando**")
        for s in th.get("paying", []):
            st.markdown(f"- 💶 {esc(s)}")
    with t2:
        st.markdown("**Rischi e punti deboli**")
        for s in th.get("weaknesses", []) or [{"text": "Nessuna debolezza marcata rispetto ai pari."}]:
            icon = SEV_ICON.get(s.get("severity"), "⚠️")
            st.markdown(f"- {icon} {esc(s['text'])}")
        st.markdown("**Cosa deve andare bene**")
        for s in th.get("must_go_right", []):
            st.markdown(f"- 🎯 {esc(s)}")
        st.markdown("**Cosa invaliderebbe la tesi (da monitorare)**")
        for s in th.get("monitor", []):
            st.markdown(f"- 👁️ {esc(s)}")
    st.caption(esc(th.get("disclaimer", "")))
    wl = data.watchlist()
    if c.get("ticker") in wl:
        if st.button("★ Rimuovi dalla watchlist"):
            data.set_watch(c["ticker"], False)
            st.rerun()
    elif st.button("☆ Aggiungi alla watchlist (verrà sempre analizzata)"):
        data.set_watch(c["ticker"], True)
        st.rerun()

    tabs = st.tabs(["📊 Fondamentali nel tempo", "💶 Valutazione", "📉 Rischio e prezzo", "🚩 Red flag e filing",
                    "🧮 Tutte le metriche", "🔗 Fonti e dati"])
    facts = d["facts"]
    fy = facts[facts.period_type == "FY"].pivot_table(index="period_end", columns="item", values="value", aggfunc="last")
    fy.index = pd.to_datetime(fy.index)
    with tabs[0]:
        if fy.empty:
            st.info("Nessun bilancio disponibile.")
        else:
            sc_ = 1e9 if (fy.get("revenue", pd.Series([0])).abs().max() or 0) > 5e9 else 1e6
            unit = "mld" if sc_ == 1e9 else "mln"
            a1, a2 = st.columns(2)
            a1.plotly_chart(charts.bars(fy, ["revenue", "net_income", "fcf"], ["Ricavi", "Utile netto", "Free cash flow"],
                                        f"Ricavi, utile e cassa ({unit} {cur})", scale=sc_, suffix=f" {unit}"), width="stretch")
            marg = {}
            if "revenue" in fy:
                rv = fy["revenue"].where(fy["revenue"] > 0)
                for col, n in (("gross_profit", "Margine lordo"), ("operating_income", "Margine operativo"),
                               ("net_income", "Margine netto"), ("fcf", "Margine FCF")):
                    if col in fy and not c.get("is_banklike"):
                        marg[n] = fy[col] / rv
            a2.plotly_chart(charts.lines(marg, "Margini", yfmt=".0%"), width="stretch")
            b1, b2 = st.columns(2)
            if "shares_diluted" in fy:
                b1.plotly_chart(charts.lines({"Azioni diluite (mln)": fy["shares_diluted"] / 1e6},
                                             "Numero di azioni: in calo = riacquisti, in aumento = diluizione"), width="stretch")
            debt = {}
            for col, n in (("total_debt", "Debito finanziario"), ("cash", "Cassa")):
                if col in fy:
                    debt[n] = fy[col] / sc_
            if debt and not c.get("is_banklike"):
                b2.plotly_chart(charts.lines(debt, f"Debito vs cassa ({unit} {cur})"), width="stretch")
            if "equity" in fy and "net_income" in fy:
                eq = fy["equity"].where(fy["equity"] > 0)
                roe = fy["net_income"] / eq.rolling(2).mean()
                st.plotly_chart(charts.lines({"ROE": roe}, "Rendimento sul patrimonio (ROE) nel tempo", yfmt=".0%", height=260),
                                width="stretch")
            st.caption(f"Valori per anno fiscale in {cur}. {d['market_extra'].get('ttm_derivation', '')}")

    with tabs[1]:
        keys = ["pe", "forward_pe", "ev_ebit", "ev_ebitda", "ev_fcf", "ev_sales", "ps", "pb", "fcf_yield", "fcf_sbc_yield",
                "earnings_yield", "dividend_yield", "shareholder_yield", "p_ffo", "pe_hist_median", "pe_vs_history_pct",
                "ev_ebit_vs_history_pct", "p_fcf_vs_history_pct", "implied_fcf_growth", "growth_gap", "fwd_eps_growth"]
        st.dataframe(metric_table(mdf, keys, cur, pcts), hide_index=True, width="stretch",
                     column_config={"Percentile nel settore": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})
        pm = det.get("peer_medians", {})
        if pm:
            st.caption("Mediane dei pari (" + str(sc.get("peer_group")) + "): " + " · ".join(
                f"{label(k)} {fmt(k, v, cur)}" for k, v in pm.items() if k in ("pe", "ev_ebit", "fcf_sbc_yield", "pb", "ev_sales")))
        hm = pd.DataFrame(d["market_extra"].get("hist_multiples") or [])
        if not hm.empty:
            hm["date"] = pd.to_datetime(hm["date"])
            hm = hm.set_index("date")
            series = {k: hm[k].where((hm[k] > 0) & (hm[k] < 200)) for k, n in (("pe", "P/E"), ("ev_ebit", "EV/EBIT"), ("p_fcf", "P/FCF")) if k in hm}
            st.plotly_chart(charts.lines({({"pe": "P/E", "ev_ebit": "EV/EBIT", "p_fcf": "P/FCF"}[k]): v for k, v in series.items()},
                                         "Multipli a fine anno fiscale (storia della società)"), width="stretch")
        rd = d["market_extra"].get("rdcf")
        st.markdown("#### 🔄 Reverse DCF interattivo — *quanta crescita sta pagando il prezzo?*")
        if rd and rd.get("fcf_base") and mv("market_cap"):
            r1, r2, r3 = st.columns(3)
            # sliders in PERCENT (4.25 = 4,25%), converted to fractions for the model
            dr = r1.slider("Tasso di sconto (%)", 4.0, 16.0,
                           clamp(round(rd["discount_rate"] * 400) / 4, 4.0, 16.0), 0.25, format="%.2f") / 100
            gt = r2.slider("Crescita perpetua dopo l'orizzonte (%)", 0.0, 4.0,
                           clamp(round(rd["terminal_growth"] * 400) / 4, 0.0, 4.0), 0.25, format="%.2f") / 100
            yrs = r3.slider("Anni di crescita esplicita", 5, 15, int(clamp(rd["years"], 5, 15)))
            if dr <= gt + 0.005:
                st.warning("Il tasso di sconto deve superare la crescita perpetua di almeno 0,5 punti: con valori così "
                           "vicini il valore terminale tende all'infinito e il modello non ha senso.")
            else:
                _dcf_scenario(rd, mv, cur, dr, gt, yrs)
        else:
            st.info("Reverse DCF non applicabile: " + (("capitalizzazione non disponibile" if rd.get("fcf_base")
                                                        else (rd.get("fcf_base_method") or rd.get("status")
                                                              or "dati insufficienti")) if rd else
                    "dati di mercato non disponibili" if not c.get("is_banklike") else
                    "banca/assicurazione: il DCF sui flussi di cassa non è significativo."))

    with tabs[2]:
        px = data.prices(c.get("ticker"), c.get("price_currency"))
        if px.empty:
            st.info("Prezzi non disponibili.")
        else:
            fx = data.fx_table()
            eur = fx.series_to_eur(px["adj_close"], c.get("price_currency") or "USD")
            if eur is None or eur.dropna().empty:
                st.info(f"Cambio {c.get('price_currency')}/EUR non disponibile: grafici in euro non mostrati.")
                eur = None
        if not px.empty and eur is not None:
            eur = eur.dropna()
            bench, bench_name = data.benchmark(RUN)
            start = max(eur.index.max() - pd.DateOffset(years=5), eur.index.min())
            if bench is not None and len(bench):
                start = max(start, bench.index.min())          # same starting date for both lines
            s = {f"{c.get('ticker')} (rendimento totale in EUR)": eur[eur.index >= start]}
            if bench is not None and len(bench):
                s[f"Benchmark: {bench_name}"] = bench[bench.index >= start]
            s = {k: v / v.dropna().iloc[0] * 100 for k, v in s.items() if v.dropna().size}
            st.plotly_chart(charts.lines(s, f"Crescita di 100 € investiti dal {start.date()} (dividendi reinvestiti)"),
                            width="stretch")
            e5 = eur[eur.index >= start].dropna()
            dd = e5 / e5.cummax() - 1
            fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy", line={"color": charts.BAD, "width": 1}))
            st.plotly_chart(charts.base_layout(fig, "Perdite dal massimo precedente (drawdown)", height=240, yfmt=".0%"), width="stretch")
        st.dataframe(metric_table(mdf, ["vol_1y", "vol_3y", "beta_world", "max_drawdown_5y", "max_drawdown_10y",
                                        "drawdown_from_52w_high", "drawdown_from_3y_high", "return_1y", "return_5y_ann", "momentum_12_1"],
                                  cur, pcts), hide_index=True, width="stretch")
        if c.get("price_currency") and c.get("price_currency") != "EUR":
            st.caption(f"⚠️ Il titolo è quotato in {c.get('price_currency')}: per te in euro il rendimento include la variazione del cambio.")

    with tabs[3]:
        fl = d["flags"]
        qd = d["qualitative"]
        deep = bool(det.get("deep_analysis")) or bool(qd.get("performed") and qd.get("run_id") == RUN)
        if not deep:
            st.info("L'analisi del testo dei report annuali NON è stata eseguita per questa società in questa analisi "
                    "(non registrata alla SEC, oppure fuori dalla lista approfondita): le segnalazioni qui sotto vengono "
                    "solo dai numeri di bilancio e dagli eventi SEC.")
        if fl.empty:
            st.success("Nessuna segnalazione." if deep else "Nessuna segnalazione dai numeri di bilancio e dagli eventi SEC.")
        else:
            order = {"severe": 0, "high": 1, "medium": 2, "info": 3, "data": 4}
            fl = fl.sort_values(by="severity", key=lambda s: s.map(order))
            for _, f in fl.iterrows():
                ev = json.loads(f["evidence"]) if f["evidence"] and f["evidence"] != "null" else None
                line = f"{SEV_ICON.get(f['severity'], '')} **{SEV_IT.get(f['severity'], f['severity'])}** — {esc(f['message'])}"
                if f.get("source"):
                    line += f"  \n<small>Fonte: {f['source']}</small>"
                st.markdown(line, unsafe_allow_html=True)
                if isinstance(ev, dict):
                    if ev.get("snippet"):
                        st.caption(esc(f"«…{ev['snippet']}…»"))
                    if ev.get("url"):
                        st.markdown(f"<small>[Apri il documento]({ev['url']})</small>", unsafe_allow_html=True)
        if qd:
            st.markdown("#### Report annuali analizzati")
            for f in qd.get("filings", []):
                st.markdown(f"- [{f['form']} depositato il {f['filed']}]({f['url']})")
            rdiff = qd.get("risk_diff")
            if rdiff:
                st.markdown(f"**Fattori di rischio: {rdiff['new_count']} frasi nuove e {rdiff['removed_count']} rimosse** rispetto al "
                            f"report precedente ({rdiff['new_share']:.0%} del testo è nuovo). Le più lunghe tra le nuove:")
                for sentence in rdiff.get("new_examples", [])[:6]:
                    st.caption(esc(f"➕ {sentence}"))
            for n in qd.get("notes", []):
                st.caption(esc(f"ℹ️ {n}"))
        fg = d["filings"]
        if not fg.empty:
            st.markdown("#### Filing recenti (SEC EDGAR)")
            fg = fg.head(40).copy()
            st.dataframe(fg, hide_index=True, width="stretch",
                         column_config={"url": st.column_config.LinkColumn("Documento"), "form": "Tipo", "filed": "Depositato",
                                        "report_date": "Periodo", "items": "Voci 8-K"})

    with tabs[4]:
        allk = [k for k in GLOSSARY if k in mdf.index] + [k for k in mdf.index if k not in GLOSSARY]
        st.dataframe(metric_table(mdf, allk, cur, pcts), hide_index=True, width="stretch", height=600,
                     column_config={"Percentile nel settore": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})
        st.caption("Tipo: *dato osservato* = riportato dalla fonte; *calcolato* = formula sui dati; *stima* = modello con assunzioni; "
                   "*stima di terzi* = consenso analisti Yahoo.")

    with tabs[5]:
        st.markdown("**Come sono stati ottenuti i dati**")
        me = d["market_extra"]
        for n in me.get("notes", []):
            st.markdown(f"- {esc(n)}")
        st.markdown(esc(f"- Capitalizzazione: {me.get('market_cap_method', 'n/d')}" + (f" · {me['fx_note']}" if me.get("fx_note") else "")))
        if me.get("adr_ratio"):
            st.markdown(f"- ADR: rapporto azioni ordinarie/ADR stimato = {me['adr_ratio']:.3g} (da filing SEC vs azioni Yahoo)")
        cf = d["conflicts"]
        if not cf.empty:
            st.markdown("**⚖️ Discrepanze tra fonti**")
            st.dataframe(cf, hide_index=True, width="stretch",
                         column_config={"pct_diff": st.column_config.NumberColumn("Differenza", format="percent")})
        if not facts.empty:
            f2 = facts.copy()
            if c.get("cik"):
                f2["link"] = f2["source_ref"].apply(
                    lambda a: f"https://www.sec.gov/Archives/edgar/data/{int(c['cik'])}/{a.replace('-', '')}/{a}-index.htm"
                    if isinstance(a, str) and a[:4].isdigit() else None)
            items = sorted(f2["item"].unique())
            pick = st.multiselect("Voci di bilancio", items, default=[i for i in ("revenue", "net_income", "fcf", "total_debt") if i in items])
            f2 = f2[f2["item"].isin(pick)] if pick else f2
            cols = [x for x in ["item", "period_type", "period_end", "value", "unit", "source", "concept", "form", "filed",
                                "derivation", "restated", "original_value", "split_adjusted", "link"] if x in f2.columns]
            st.dataframe(f2[cols].sort_values(["item", "period_end"], ascending=[True, False]), hide_index=True, width="stretch",
                         column_config={"link": st.column_config.LinkColumn("Filing"), "value": st.column_config.NumberColumn(format="%.4g"),
                                        "original_value": st.column_config.NumberColumn("Valore originale", format="%.4g")})
            st.caption("*restated=1*: il valore è stato modificato in un filing successivo (si usa l'ultimo; l'originale è mostrato). "
                       "*split_adjusted=1*: rettificato per frazionamento azionario.")


def _dcf_scenario(rd: dict, mv, cur: str | None, dr: float, gt: float, yrs: int) -> None:
    from ire.valuation import dcf_value, implied_growth

    g, status = implied_growth(mv("market_cap"), rd["fcf_base"], dr, gt, yrs)
    st.metric("Crescita annua del FCF implicita nel prezzo", f"{g:.1%}" if g is not None else "n/d", help=status)
    hist = [(label(k), mv(k)) for k in ("fcf_cagr_5y", "revenue_cagr_5y", "revenue_cagr_10y")
            if mv(k) is not None and pd.notna(mv(k))]
    if hist:
        st.caption("Per confronto (storico) — " + " · ".join(f"{n}: {num_it(v * 100)}%" for n, v in hist))
    default = clamp(round((g if g is not None else 0.05) * 200) / 2, -10.0, 30.0)     # 0,5-point steps
    ug = st.slider("Scenario: la tua ipotesi di crescita annua del FCF (%)", -10.0, 30.0, default, 0.5, format="%.1f",
                   help="Parte dalla crescita implicita nel prezzo (arrotondata a 0,5 punti): spostala per vedere "
                        "come cambia il valore.") / 100
    val = dcf_value(rd["fcf_base"], ug, dr, gt, yrs)
    moved = g is None or abs(ug - g) >= 0.005
    if val is not None and np.isfinite(val) and not moved:
        st.caption("Con l'ipotesi uguale alla crescita implicita il valore coincide con la capitalizzazione: sposta il "
                   "cursore per esplorare altri scenari.")
    elif val is not None and np.isfinite(val):
        st.markdown(esc(f"Con crescita {ug:.0%} per {yrs} anni il valore stimato è **{money(val, cur)}** contro una "
                        f"capitalizzazione di **{money(mv('market_cap'), cur)}** (**{val / mv('market_cap') - 1:+.0%}**)."))
    st.caption(esc(f"FCF di partenza: {money(rd['fcf_base'], cur)} ({rd['fcf_base_method']}). Tasso risk-free: "
                   f"{rd['risk_free']:.2%} ({rd['risk_free_source']}) + premio al rischio {rd['erp']:.1%} (assunzione). "
                   "È un modello: piccole variazioni delle ipotesi cambiano molto il risultato. Usalo per capire "
                   "le aspettative, non come prezzo obiettivo."))


# =====================================================================================
COMPARE_GROUPS = [
    ("Chi cresce di più?", ["revenue_cagr_5y", "eps_cagr_5y", "fcf_ps_cagr_5y"]),
    ("Chi genera più cassa?", ["fcf_margin", "fcf_sbc_yield"]),
    ("Chi ha margini migliori?", ["gross_margin", "operating_margin", "net_margin"]),
    ("Chi usa meglio il capitale?", ["roic_5y_median", "roe", "share_change_cagr_5y", "shareholder_yield"]),
    ("Chi è più indebitata?", ["net_debt_ebitda", "interest_coverage"]),
    ("Chi è più costosa?", ["pe", "ev_ebit", "fcf_sbc_yield", "pe_vs_history_pct"]),
    ("Cosa sto pagando?", ["implied_fcf_growth", "growth_gap"]),
    ("Quanto oscilla?", ["vol_1y", "max_drawdown_5y", "beta_world"]),
]


def page_compare():
    st.title("⚖️ Confronta aziende")
    if RUN is None:
        no_data()
    U = data.universe(RUN)
    lab = dict(zip(U["label"], U["company_id"]))
    default = []
    cid = st.session_state.get("cid")
    if cid in set(U.company_id):
        row = U[U.company_id == cid].iloc[0]
        peers = U[(U.industry == row["industry"]) & (U.company_id != cid)].copy()
        if "market_cap_eur" in peers:
            peers["d"] = (np.log(peers["market_cap_eur"].astype(float).clip(lower=1))
                          - np.log(max(float(row.get("market_cap_eur") or 1), 1))).abs()
            peers = peers.sort_values("d")
        default = [row["label"]] + list(peers["label"].head(2))
    pick = st.multiselect("Scegli da 2 a 5 aziende (suggerite: stessa industria, dimensione simile)", list(U["label"]), default=default,
                          max_selections=5)
    if len(pick) < 2:
        st.info("Seleziona almeno due aziende.")
        return
    sub = U[U.company_id.isin([lab[p] for p in pick])].set_index("ticker")
    if sub["peer_group"].nunique() > 1:
        st.warning("Stai confrontando aziende di settori diversi: alcune metriche (margini, debito) non sono direttamente confrontabili.")
    head = sub[["name", "classification", "valuation_verdict", "robust_score", "quality", "growth", "financial_strength", "valuation"]].copy()
    for col in ("robust_score", "quality", "growth", "financial_strength", "valuation"):
        head[col] = pd.to_numeric(head[col], errors="coerce").map(lambda v: "n/d" if pd.isna(v) else f"{v:.0f}/100")
    head = head.T.astype(str).replace({"nan": "n/d", "None": "n/d"})
    head.index = ["Nome", "Classificazione", "Valutazione", "Punteggio robusto", "Qualità", "Crescita", "Solidità", "Economicità"]
    st.dataframe(head, width="stretch")
    for title, keys in COMPARE_GROUPS:
        rows = []
        for k in keys:
            if k not in sub.columns:
                continue
            vals = pd.to_numeric(sub[k], errors="coerce")
            best = None
            if vals.notna().sum() >= 2 and k in DIRECTION:
                best = vals.idxmax() if DIRECTION[k] > 0 else vals.idxmin()
            r = {"Metrica": label(k)}
            for t in sub.index:
                v = vals.get(t)
                r[t] = fmt(k, v, sub.loc[t, "fin_currency"]) + (" 🏆" if t == best else "")
            rows.append(r)
        if rows:
            st.markdown(f"**{title}**")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("🏆 = migliore nel gruppo per quella metrica (per debito, costo e volatilità: il valore più basso).")
    st.markdown("**Quali rischi le differenziano?**")
    cols = st.columns(len(sub))
    for col, (t, r) in zip(cols, sub.iterrows()):
        d = data.company(r["company_id"], RUN)
        fl = d["flags"]
        fl = fl[fl.severity.isin(["severe", "high", "medium"])]
        col.markdown(f"**{t}**")
        if fl.empty:
            col.caption("Nessuna segnalazione rilevante")
        for _, f in fl.iterrows():
            col.caption(f"{SEV_ICON[f['severity']]} {esc(f['message'])}")
    # history charts
    rev, om = {}, {}
    for t, r in sub.iterrows():
        f = data.company(r["company_id"], RUN)["facts"]
        f = f[f.period_type == "FY"].pivot_table(index="period_end", columns="item", values="value", aggfunc="last")
        f.index = pd.to_datetime(f.index)
        if "revenue" in f and f["revenue"].dropna().size >= 2:
            s = f["revenue"].dropna()
            s = s[s.index >= s.index.max() - pd.DateOffset(years=8)]
            rev[t] = s / s.iloc[0] * 100
            if "operating_income" in f:
                om[t] = (f["operating_income"] / f["revenue"].where(f["revenue"] > 0)).dropna()
    c1, c2 = st.columns(2)
    if rev:
        c1.plotly_chart(charts.lines(rev, "Ricavi indicizzati (primo anno = 100, valuta di bilancio)"), width="stretch")
    if om:
        c2.plotly_chart(charts.lines(om, "Margine operativo", yfmt=".0%"), width="stretch")


# =====================================================================================
def page_screener():
    st.title("🧭 Classifica e filtri")
    if RUN is None:
        no_data()
    U = data.universe(RUN)
    f1, f2, f3 = st.columns(3)
    regions = f1.multiselect("Area", sorted(U["region"].dropna().unique()))
    sectors = f2.multiselect("Settore", sorted(U["sector"].dropna().unique()), format_func=sector_it)
    classes = f3.multiselect("Classificazione", sorted(U["classification"].dropna().unique()))
    g1, g2, g3, g4 = st.columns(4)
    verdicts = g1.multiselect("Valutazione", sorted(U["valuation_verdict"].dropna().unique()))
    conf = g2.multiselect("Affidabilità dei dati", ["alta", "media", "bassa"])
    tier = g3.multiselect("Qualità dati", ["A", "B"], format_func=lambda x: "A (SEC)" if x == "A" else "B (Yahoo)")
    min_mc = g4.number_input("Capitalizzazione minima (mld EUR)", 0.0, 5000.0, 0.0, 1.0)
    df = U.copy()
    if regions:
        df = df[df.region.isin(regions)]
    if sectors:
        df = df[df.sector.isin(sectors)]
    if classes:
        df = df[df.classification.isin(classes)]
    if verdicts:
        df = df[df.valuation_verdict.isin(verdicts)]
    if conf:
        df = df[df.confidence.isin(conf)]
    if tier:
        df = df[df.data_tier.isin(tier)]
    if min_mc:
        df = df[pd.to_numeric(df.market_cap_eur, errors="coerce") >= min_mc * 1e9]
    df = df.sort_values("robust_score", ascending=False, na_position="last")
    st.caption(f"{len(df)} società. Clicca una riga per aprire la scheda.")
    view = df[["ticker", "name", "sector", "region", "robust_score", "rank_spread", "quality", "growth", "financial_strength",
               "valuation", "classification", "valuation_verdict", "valuation_confidence", "confidence", "pe",
               "fcf_sbc_yield", "roic_5y_median",
               "revenue_cagr_5y", "net_debt_ebitda"]].copy()
    view["sector"] = view["sector"].map(sector_it)
    pc = lambda n: st.column_config.ProgressColumn(n, min_value=0, max_value=100, format="%.0f")  # noqa: E731
    ev = st.dataframe(view, hide_index=True, width="stretch", height=620, on_select="rerun", selection_mode="single-row",
                      key=tkey("screener"),
                      column_config={"ticker": "Ticker", "name": "Società", "sector": "Settore", "region": "Area",
                                     "robust_score": pc("Robusto"), "rank_spread": st.column_config.NumberColumn("Instabilità rank", format="%.2f",
                                     help="0 = stessa posizione con tutti i pesi; alto = dipende dai pesi"),
                                     "quality": pc("Qualità"), "growth": pc("Crescita"), "financial_strength": pc("Solidità"),
                                     "valuation": pc("Economicità"), "classification": "Classificazione", "valuation_verdict": "Valutazione",
                                     "valuation_confidence": "Confidenza del verdetto",
                                     "confidence": "Affidabilità dei dati", "pe": st.column_config.NumberColumn("P/E", format="%.1f"),
                                     "fcf_sbc_yield": st.column_config.NumberColumn("FCF yield netto SBC", format="percent"),
                                     "roic_5y_median": st.column_config.NumberColumn("ROIC 5a", format="percent"),
                                     "revenue_cagr_5y": st.column_config.NumberColumn("Crescita ricavi 5a", format="percent"),
                                     "net_debt_ebitda": st.column_config.NumberColumn("Debito netto/EBITDA", format="%.1f")})
    if ev and ev.selection and ev.selection.rows:
        go_company(df.iloc[ev.selection.rows[0]]["company_id"])
    st.download_button("⬇️ Scarica CSV (per Excel italiano)",
                       df.drop(columns=["label"], errors="ignore").to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                       "classifica.csv", "text/csv")


# =====================================================================================
def _portfolio_analytics_view(an: dict, tick: dict[str, str], key: str):
    if not an:
        return
    c = st.columns(5)
    c[0].metric("Volatilità attesa", f"{an['volatility']:.1%}" if an.get("volatility") else "n/d",
                help="Rendimenti settimanali in EUR degli ultimi 3 anni. Metodo: " + str(an.get("cov_method") or "n/d"))
    c[1].metric("N. effettivo di titoli", f"{an.get('effective_n', 0):.1f}", help="1/Σpesi²: quanti titoli 'equivalenti' a pesi uguali")
    c[2].metric("Rapporto di diversificazione", f"{an.get('diversification_ratio', 0):.2f}",
                help="Media ponderata delle volatilità / volatilità del portafoglio. Più alto = più benefici dalla diversificazione")
    c[3].metric("Correlazione media", f"{an['avg_pair_correlation']:.2f}" if an.get("avg_pair_correlation") is not None else "n/d")
    c[4].metric("Beta vs MSCI World", f"{an['beta']:.2f}" if an.get("beta") is not None else "n/d")
    for w in an.get("warnings", []):
        st.warning(esc(w))
    if an.get("missing_prices"):
        st.caption("Senza prezzi utilizzabili (esclusi dall'analisi di rischio): "
                   + ", ".join(tick.get(k, k) for k in an["missing_prices"]))
    e1, e2, e3 = st.columns(3)
    if an.get("sector_exposure"):
        e1.plotly_chart(charts.donut_free_bars({sector_it(k): v for k, v in an["sector_exposure"].items()}, "Settori"), width="stretch", key=f"{key}_chart0")
    if an.get("region_exposure"):
        e2.plotly_chart(charts.donut_free_bars(an["region_exposure"], "Aree geografiche (sede)"), width="stretch", key=f"{key}_chart1")
    if an.get("currency_exposure"):
        e3.plotly_chart(charts.donut_free_bars(an["currency_exposure"], "Valute di quotazione"), width="stretch", key=f"{key}_chart2")
    if an.get("factor_tilts"):
        st.markdown("**Esposizione ai fattori** (media ponderata dei percentili: 50 = neutro)")
        st.plotly_chart(charts.pillar_bars({k: v for k, v in an["factor_tilts"].items()}), width="stretch", key=f"{key}_chart3")
    if an.get("risk_contribution"):
        rc = an["risk_contribution"]
        st.plotly_chart(charts.donut_free_bars({tick.get(k, k): v for k, v in rc.items()}, "Contributo al rischio totale"),
                        width="stretch", key=f"{key}_chart5")
    hc = an.get("hist_cum")
    if hc:
        s = {"Portafoglio (pesi attuali)": pd.Series(hc["values"], index=pd.to_datetime(hc["index"]))}
        if an.get("bench_cum"):
            s["MSCI World"] = pd.Series(an["bench_cum"]["values"], index=pd.to_datetime(an["bench_cum"]["index"]))
        st.plotly_chart(charts.lines({k: v * 100 for k, v in s.items()}, "Comportamento storico dei pesi attuali (base 100, EUR)"),
                        width="stretch", key=f"{key}_chart6")
        st.caption("⚠️ Non è un backtest della strategia: i titoli sono scelti OGGI con i dati di oggi (look-ahead e survivorship bias). "
                   "Serve a capire come oscillerebbe questo portafoglio, non quanto renderà. "
                   + (f"Rendimento annuo storico {an['hist_return_ann']:.1%} vs benchmark {an.get('bench_return_ann', float('nan')):.1%}; "
                      f"perdita massima {an['hist_max_drawdown']:.0%} vs {an.get('bench_max_drawdown', float('nan')):.0%}." if an.get("hist_return_ann") is not None else ""))
    if an.get("stress_window"):
        sw = an["stress_window"]
        st.info(f"**Stress test storico:** nel peggior trimestre del mercato ({sw['start']} → {sw['end']}) il benchmark ha fatto "
                f"{sw['benchmark']:.0%}, questo portafoglio {sw['portfolio']:.0%}. ⚠️ I titoli sono stati scelti OGGI "
                "guardando anche questo periodo (correlazioni e volatilità degli ultimi 3 anni): il risultato è "
                "descrittivo, non una prova di come si sarebbe comportata la strategia.")
    cm = an.get("correlation_matrix")
    if cm and cm.get("index"):
        corr = pd.DataFrame(cm["values"], index=cm["index"], columns=cm["columns"])
        labels_ = [tick.get(i, i) for i in corr.index]
        st.plotly_chart(charts.heatmap(corr, labels_), width="stretch", key=f"{key}_chart4")


def page_portfolio():
    st.title("🧺 Portafoglio")
    if RUN is None:
        no_data()
    U = data.universe(RUN)
    tick = dict(zip(U.company_id, U.ticker))
    t1, t2 = st.tabs(["Portafoglio proposto", "Analizza il MIO portafoglio"])
    with t1:
        p = data.portfolio(RUN)
        if not p.get("positions"):
            st.info("Nessun portafoglio proposto nell'ultima analisi." + (" " + esc(" ".join(p.get("log", []))) if p else ""))
        else:
            if p.get("status") == "non proposto":
                st.error("**Portafoglio NON proposto**: troppo pochi titoli rispettano i criteri e i vincoli di "
                         "diversificazione. L'elenco sotto è solo una lista di candidati da studiare.")
            elif p.get("status") == "concentrato":
                st.error("**Portafoglio CONCENTRATO**: i limiti di diversificazione (per titolo, settore o area) sono "
                         "superati di molto. Non è una proposta diversificata: è una lista di candidati da studiare.")
            m1, m2, m3 = st.columns(3)
            m1.metric("Posizioni", len(p["positions"]))
            m2.metric("Rotazione vs proposta precedente", f"{p['turnover']:.0%}" if p.get("turnover") is not None else "n/d",
                      help="Quota del capitale da spostare rispetto all'ultima proposta della stessa modalità")
            m3.metric("Soglia di percentile usata", f"{p.get('min_percentile_used', float('nan')):.0f}"
                      if p.get("min_percentile_used") is not None else "n/d")
            if p.get("exited"):
                st.caption("Usciti rispetto alla proposta precedente: " + ", ".join(tick.get(k, k) for k in p["exited"]))
            cons = p.get("constraints") or []
            if cons:
                cdf = pd.DataFrame(cons)
                bad = cdf[~cdf["rispettato"].astype(bool)]
                if len(bad):
                    st.warning("Vincoli NON rispettati: " + "; ".join(
                        f"{r.vincolo} {r.effettivo:.1%} (configurato {r.configurato:.1%})" for r in bad.itertuples()))
                with st.expander("Vincoli di diversificazione: configurati vs effettivi"):
                    st.dataframe(cdf.assign(rispettato=cdf["rispettato"].map({True: "sì", False: "NO"})),
                                 hide_index=True, width="stretch",
                                 column_config={"vincolo": "Vincolo", "rispettato": "Rispettato", "dettaglio": "Dettaglio",
                                                "configurato": st.column_config.NumberColumn("Configurato", format="percent"),
                                                "effettivo": st.column_config.NumberColumn("Effettivo", format="percent")})
            st.caption("Proposta generata dalla metodologia: " + p.get("method", "") +
                       " È un punto di partenza per la tua ricerca, non una raccomandazione personalizzata.")
            pos = pd.DataFrame(p["positions"])
            for col in ("valuation_verdict", "valuation_confidence"):
                if col not in pos.columns:
                    pos[col] = None
            view = pos[["ticker", "name", "weight", "role", "sector", "region", "classification", "valuation_verdict",
                        "valuation_confidence", "reason", "risk_note"]].copy()
            view["sector"] = view["sector"].map(sector_it)
            ev = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                              key=tkey("portfolio"),
                              column_config={"ticker": "Ticker", "name": "Società",
                                             "weight": st.column_config.NumberColumn("Peso", format="percent"),
                                             "role": "Ruolo", "sector": "Settore", "region": "Area", "classification": "Classificazione",
                                             "valuation_verdict": "Valutazione", "valuation_confidence": "Confidenza del verdetto",
                                             "reason": st.column_config.TextColumn("Perché è nel portafoglio", width="large"),
                                             "risk_note": "Rischio che porta"})
            if ev and ev.selection and ev.selection.rows:
                go_company(pos.iloc[ev.selection.rows[0]]["company_id"])
            with st.expander("Ruoli: cosa significano"):
                for role, why in pos.groupby("role")["role_reason"].first().items():
                    st.markdown(f"- **{role}**: {why}")
            with st.expander("Come è stata costruita la selezione"):
                for line in p.get("log", []):
                    st.markdown(f"- {esc(line)}")
                rej = p.get("rejected_examples", {})
                if rej:
                    st.markdown("Esempi di candidati esclusi per vincoli di diversificazione:")
                    st.dataframe(pd.DataFrame([{"Ticker": tick.get(k, k), "Motivo": v} for k, v in rej.items()]), hide_index=True)
            _portfolio_analytics_view(p.get("analytics", {}), tick, "proposed")
    with t2:
        st.caption("Inserisci i tuoi titoli (notazione Yahoo: AAPL, ENI.MI, ASML.AS, 7203.T…) e il peso o l'importo. "
                   "I titoli verranno anche inclusi automaticamente nelle prossime analisi.")
        up = data.user_portfolio()
        if up.empty:
            up = pd.DataFrame({"ticker": [""], "weight": [None], "note": [""]})
        ed = st.data_editor(up, num_rows="dynamic", width="stretch",
                            column_config={"ticker": "Ticker", "weight": st.column_config.NumberColumn("Peso o importo", min_value=0.0),
                                           "note": "Nota"})
        cA, cB = st.columns(2)
        if cA.button("💾 Salva"):
            data.save_user_portfolio(ed)
            st.success("Salvato.")
        if cB.button("📊 Analizza"):
            data.save_user_portfolio(ed)
            _analyze_user(ed, U)


# Yahoo suffix → currency, used only when Yahoo metadata are unavailable for a ticker outside the universe
SUFFIX_CCY = {".MI": "EUR", ".PA": "EUR", ".AS": "EUR", ".DE": "EUR", ".F": "EUR", ".MC": "EUR", ".BR": "EUR",
              ".LS": "EUR", ".HE": "EUR", ".VI": "EUR", ".IR": "EUR", ".L": "GBp", ".SW": "CHF", ".ST": "SEK",
              ".CO": "DKK", ".OL": "NOK", ".T": "JPY", ".TO": "CAD", ".AX": "AUD", ".HK": "HKD"}


def user_holdings(ed: pd.DataFrame) -> pd.DataFrame:
    """Cleans the editor table: tickers upper-case, rows of the same ticker (several purchase lots) SUMMED."""
    df = ed.copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df = df[(df["ticker"] != "") & (df["ticker"] != "NAN") & df["weight"].notna()]
    return df.groupby("ticker", as_index=False)["weight"].sum()


def _analyze_user(ed: pd.DataFrame, U: pd.DataFrame):
    from ire.portfolio import analyze_portfolio, serializable
    from ire.risk import weekly_returns
    from ire.sources import yahoo

    hold = user_holdings(ed)
    if hold.empty or hold["weight"].sum() <= 0 or (hold["weight"] < 0).any():
        st.warning("Inserisci almeno un titolo con peso o importo maggiore di zero (nessun valore negativo).")
        return
    entered = dict(zip(hold["ticker"], hold["weight"] / hold["weight"].sum()))
    fx = data.fx_table()
    weekly, weights, meta_rows, missing = {}, {}, [], []
    by_t = U.set_index("ticker")
    cid_of = {}
    for t, wgt in zip(hold["ticker"], hold["weight"]):
        if wgt <= 0:
            continue
        if t in by_t.index:
            row = by_t.loc[t]
            cid = row["company_id"]
            cur = row["price_currency"]
            px = data.prices(t, cur)
            meta_rows.append({"company_id": cid, "sector": row["sector"], "region": row["region"], "price_currency": cur,
                              "quality": row["quality"], "valuation": row["valuation"], "growth": row["growth"],
                              "financial_strength": row["financial_strength"]})
        else:
            cid = f"USER:{t}"
            with st.spinner(f"Scarico i prezzi di {t}…"):
                info = yahoo.info(t) or {}
                raw = yahoo.download_prices([t], start="2019-01-01").get(t)
            if raw is None or raw.empty:
                missing.append(f"{t} (nessun prezzo)")
                continue
            raw_cur = info.get("currency") or next((c for suf, c in SUFFIX_CCY.items() if t.endswith(suf)), None)
            if raw_cur is None and "." not in t:
                raw_cur = "USD"                        # no exchange suffix in Yahoo notation = US listing
            if raw_cur is None:
                missing.append(f"{t} (valuta sconosciuta)")
                continue
            cur, div = yahoo.normalize_currency(raw_cur)
            px = raw / div
            meta_rows.append({"company_id": cid, "sector": info.get("sector") or "n/d", "region": None, "price_currency": cur})
        if px.empty:
            missing.append(f"{t} (nessun prezzo)")
            continue
        eur = fx.series_to_eur(px["adj_close"], cur or "USD")
        if eur is None or eur.dropna().empty:
            missing.append(f"{t} (cambio {cur}/EUR non disponibile)")
            continue
        weekly[cid] = weekly_returns(eur.dropna())
        weights[cid] = float(wgt)
        cid_of[cid] = t
    if missing:
        st.warning("Esclusi dall'analisi: " + ", ".join(missing))
    if not weights:
        return
    W = pd.DataFrame(weekly)
    bench, bench_name = data.benchmark(RUN)
    bw = weekly_returns(bench) if bench is not None and len(bench) else None
    an = serializable(analyze_portfolio(weights, W, pd.DataFrame(meta_rows), bw))
    tick = {**dict(zip(U.company_id, U.ticker)), **cid_of}
    st.subheader("Le tue posizioni viste dal sistema")
    rows = []
    in_risk = [c for c in weights if c not in set(an.get("missing_prices", []))]
    tot_risk = sum(weights[c] for c in in_risk)
    for cid, w in weights.items():
        rr = U[U.company_id == cid]
        rows.append({"Ticker": tick.get(cid), "Peso inserito": entered.get(cid_of[cid]),
                     "Peso nell'analisi di rischio": (w / tot_risk) if cid in in_risk and tot_risk else None,
                     "Classificazione": rr["classification"].iloc[0] if len(rr) else "non nell'universo (solo prezzi)",
                     "Valutazione": rr["valuation_verdict"].iloc[0] if len(rr) else "",
                     "Punteggio robusto": rr["robust_score"].iloc[0] if len(rr) else None})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Peso inserito": st.column_config.NumberColumn(format="percent"),
                                "Peso nell'analisi di rischio": st.column_config.NumberColumn(format="percent")})
    st.caption(f"Benchmark: {bench_name}. Righe con lo stesso ticker sono state sommate. Il rischio è calcolato solo sui "
               "titoli con almeno un anno di prezzi recenti, riproporzionando i loro pesi.")
    _portfolio_analytics_view(an, tick, "mine")


# =====================================================================================
def page_changes():
    st.title("🔔 Cosa è cambiato")
    run_banner()
    r = data.runs()
    done = r[r.status == "completed"]
    if len(done) < 2:
        st.info("Servono almeno due analisi completate per vedere i cambiamenti. Riesegui la pipeline tra qualche giorno/settimana.")
    else:
        opts = [f"#{x.run_id} — {str(x.finished_at)[:10]}" for x in done.itertuples()]
        c1, c2 = st.columns(2)
        new = c1.selectbox("Analisi recente", opts, index=0)
        old = c2.selectbox("Confronta con", opts, index=1)
        nid, oid = int(new.split()[0][1:]), int(old.split()[0][1:])
        modes = dict(zip(done.run_id, done["mode"]))
        if modes.get(nid) != modes.get(oid):
            st.warning(f"Le due analisi usano modalità diverse ({modes.get(nid)} vs {modes.get(oid)}): l'universo è diverso, "
                       "quindi entrate e uscite dall'universo non vengono mostrate.")
        ch = data.changes(nid, oid)
        if ch.empty:
            st.success("Nessun cambiamento rilevante.")
        else:
            wl = set(data.watchlist())
            only_wl = st.toggle("Solo watchlist", value=False)
            if only_wl:
                ch = ch[ch.ticker.isin(wl)]
            kinds = st.multiselect("Tipo", sorted(ch["tipo"].unique()), default=sorted(ch["tipo"].unique()))
            ch = ch[ch["tipo"].isin(kinds)]
            st.caption("Punteggi e classi sono RELATIVI ai pari: possono cambiare anche se l'azienda non è cambiata (cambia "
                       "il gruppo di confronto). Guarda prima i cambiamenti dei Fondamentali e le nuove segnalazioni.")
            st.dataframe(ch.drop(columns=["company_id"]), hide_index=True, width="stretch", height=500)
    st.subheader("⭐ Watchlist")
    wl = data.watchlist()
    new_t = st.text_input("Aggiungi ticker (notazione Yahoo)")
    if st.button("Aggiungi") and new_t.strip():
        data.set_watch(new_t.strip().upper(), True)
        st.rerun()
    if wl and RUN:
        U = data.universe(RUN)
        w = U[U.ticker.isin(wl)][["ticker", "name", "classification", "valuation_verdict", "valuation_confidence",
                                  "robust_score", "confidence"]]
        st.dataframe(w, hide_index=True, width="stretch")
        missing = sorted(set(wl) - set(w.ticker))
        if missing:
            st.caption("Non ancora analizzati (verranno inclusi alla prossima esecuzione): " + ", ".join(missing))
        rem = st.selectbox("Rimuovi", [""] + wl)
        if rem and st.button("Rimuovi dalla watchlist"):
            data.set_watch(rem, False)
            st.rerun()


# =====================================================================================
def page_data():
    st.title("📚 Dati, fonti e metodologia")
    if RUN is None:
        no_data()
    run_banner()
    t1, t2, t3, t4 = st.tabs(["Copertura e qualità", "Esclusioni", "Discrepanze tra fonti", "Metodologia"])
    U = data.universe(RUN)
    with t1:
        st.markdown("**Fonti usate** · SEC EDGAR XBRL (bilanci USA e emittenti esteri registrati) · Yahoo Finance via yfinance "
                    "(prezzi, metadati, bilanci non-SEC, stime analisti) · BCE (cambi) · FRED (tassi) · Wikipedia (composizione indici).")
        c1, c2 = st.columns(2)
        c1.dataframe(U.groupby(["region", "data_tier"]).size().unstack(fill_value=0), width="stretch")
        c2.dataframe(U["sector"].map(sector_it).value_counts().rename("società"), width="stretch")
        fl = data.q("SELECT code, severity, COUNT(*) AS n FROM flags WHERE run_id=? GROUP BY code, severity ORDER BY n DESC", [RUN])
        st.markdown("**Segnalazioni per tipo**")
        st.dataframe(fl, hide_index=True, width="stretch")
        runs_ = data.runs()
        rid_log = int(runs_["run_id"].iloc[0]) if len(runs_) else RUN
        lg = data.q("SELECT ts, level, stage, message FROM log WHERE run_id=? ORDER BY ts", [rid_log])
        with st.expander(f"Log dell'ultima analisi (#{rid_log}, {STATUS_IT.get(runs_['status'].iloc[0], '')})"):
            st.dataframe(lg, hide_index=True, width="stretch")
        st.markdown("**Storico delle analisi**")
        st.dataframe(runs_[["run_id", "started_at", "finished_at", "mode", "status"]].assign(
            status=runs_["status"].map(lambda x: STATUS_IT.get(x, x))), hide_index=True, width="stretch",
            column_config={"run_id": "N.", "started_at": "Inizio (UTC)", "finished_at": "Fine (UTC)", "mode": "Modalità",
                           "status": "Esito"})
    with t2:
        allc = data.all_companies()
        ex = allc[allc.in_universe == 0]
        st.caption(f"{len(ex)} titoli esaminati ma esclusi dall'universo, con il motivo. Lo stato si riferisce all'ultima "
                   "esecuzione, anche se non completata.")
        ex = ex.assign(motivo=ex["exclusion_reason"].str.split("(").str[0].str.strip())
        st.dataframe(ex["motivo"].value_counts(), width="stretch")
        st.dataframe(ex[["ticker", "name", "exclusion_reason"]], hide_index=True, width="stretch", height=400)
    with t3:
        cf = data.q("SELECT c.ticker, k.item, k.period_end, k.value_a, k.source_a, k.value_b, k.source_b, k.pct_diff, k.likely_reason "
                    "FROM conflicts k JOIN companies c USING(company_id) WHERE k.run_id=? ORDER BY ABS(k.pct_diff) DESC", [RUN])
        st.caption("Quando due fonti non concordano, il sistema usa la fonte primaria (filing SEC) e mostra la discrepanza con una "
                   "spiegazione probabile.")
        st.dataframe(cf, hide_index=True, width="stretch",
                     column_config={"pct_diff": st.column_config.NumberColumn("Differenza", format="percent")})
    with t4:
        p = os.path.join(os.path.dirname(HERE), "METHODOLOGY.md")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                st.markdown(esc(fh.read()))
        else:
            st.info("File METHODOLOGY.md non trovato nella cartella del progetto.")


PAGES = {
    "home": st.Page(page_home, title="Panoramica", icon="🏠", default=True),
    "company": st.Page(page_company, title="Scheda azienda", icon="🔎", url_path="azienda"),
    "compare": st.Page(page_compare, title="Confronta", icon="⚖️", url_path="confronta"),
    "screener": st.Page(page_screener, title="Classifica e filtri", icon="🧭", url_path="classifica"),
    "portfolio": st.Page(page_portfolio, title="Portafoglio", icon="🧺", url_path="portafoglio"),
    "changes": st.Page(page_changes, title="Cambiamenti e watchlist", icon="🔔", url_path="cambiamenti"),
    "data": st.Page(page_data, title="Dati e metodologia", icon="📚", url_path="dati"),
}
nav = st.navigation(list(PAGES.values()))


def _quick_nav():
    lab_ = st.session_state.get("quick_search")
    if lab_:
        U2 = data.universe(RUN)
        st.session_state["cid"] = U2.set_index("label").loc[lab_, "company_id"]
        st.session_state["quick_go"] = True
        st.session_state["quick_search"] = ""


with st.sidebar:
    if RUN:
        U_ = data.universe(RUN)
        st.caption("Ricerca rapida")
        st.selectbox("Ticker o nome", [""] + sorted(U_["label"]), label_visibility="collapsed", key="quick_search",
                     on_change=_quick_nav)
    st.caption("Dati: SEC EDGAR, Yahoo Finance, BCE, FRED. Non è un consiglio di investimento.")
if st.session_state.pop("quick_go", False):
    st.session_state["tbl_gen"] = st.session_state.get("tbl_gen", 0) + 1
    st.switch_page(PAGES["company"])
if os.environ.get("IRE_TEST_PAGE"):          # used only by automated UI tests
    globals()["page_" + os.environ["IRE_TEST_PAGE"]]()
else:
    nav.run()
