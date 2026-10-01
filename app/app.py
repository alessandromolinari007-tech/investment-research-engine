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
from ire.glossary import GLOSSARY, KIND_IT, explain, fmt, label, money  # noqa: E402
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


def sector_it(s):
    return SECTOR_IT.get(s or "Unknown", s or "Sconosciuto")


def go_company(cid: str):
    st.session_state["cid"] = cid
    st.session_state["tbl_gen"] = st.session_state.get("tbl_gen", 0) + 1   # resets table selections
    st.switch_page(PAGES["company"])


def tkey(name: str) -> str:
    return f"{name}_{st.session_state.get('tbl_gen', 0)}"


def no_data():
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
                     "Tipo": KIND_IT.get(r["kind"], r["kind"]), "Periodo": r["period"], "Come si calcola": r["method"]})
    return pd.DataFrame(rows)


# =====================================================================================
def page_home():
    st.title("📈 Investment Research Engine")
    if RUN is None:
        no_data()
    U = data.universe(RUN)
    r = data.runs()
    run = r[r.run_id == RUN].iloc[0]
    summary = json.loads(run["summary"] or "{}")
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
        cols = ["ticker", "name", "sector", "country", "robust_score", "quality", "valuation", "valuation_verdict", "confidence"]
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
                "valuation_verdict": "Valutazione", "confidence": "Confidenza"})
        if ev and ev.selection and ev.selection.rows:
            go_company(df.iloc[ev.selection.rows[0]]["company_id"])

    top = scored.sort_values("robust_score", ascending=False)
    show(top[top.classification.isin(QUALITY_DISCOUNT)].head(20),
         "💎 Qualità a sconto vs pari", "Alta qualità, prezzo basso rispetto ai pari, nessun segno di deterioramento. Il punto di partenza per la ricerca.")
    show(top[top.classification == C_QFAIR].head(15),
         "✅ Qualità a prezzo ragionevole", "Aziende eccellenti valutate nella media del settore.")
    big = top.sort_values("market_cap", ascending=False) if "market_cap" in top.columns else top
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
    h1.metric("Prezzo", f"{mk.get('price'):,.2f} {mk.get('price_currency')}" if mk.get("price") else "n/d",
              help=f"Ultima chiusura disponibile ({mk.get('as_of')}) da Yahoo Finance")
    h2.metric("Capitalizzazione", money(mv("market_cap"), cur), help=d["market_extra"].get("market_cap_method"))
    h3.metric("Punteggio robusto", f"{sc.get('robust_score'):.0f}/100" if sc.get("robust_score") is not None else "n/d",
              help="Mediana dei punteggi sotto 5 schemi di pesi")
    h4.metric("Confidenza del giudizio", sc.get("confidence") or "n/d",
              help="Dipende da copertura dei dati, qualità della fonte, anni di storia e stabilità della classifica")

    k1, k2 = st.columns([1.1, 1])
    with k1:
        cls = sc.get("classification") or C_NODATA
        st.markdown(f"### {CLASS_ICON.get(cls, '')} {cls}")
        th = det.get("thesis") or {}
        st.write(th.get("headline", "").split(" — ", 1)[-1])
        verdict = sc.get("valuation_verdict") or "non determinabile"
        st.markdown(f"**Valutazione:** {VERDICT_ICON.get(verdict, '')} {verdict} · confidenza **{sc.get('valuation_confidence')}**")
        for s in det.get("signals", []):
            icon = {"economica": "🟢", "ragionevole": "⚪", "costosa": "🔴"}[s["verdict"]]
            st.markdown(f"- {icon} *{s['name']}*: {s['detail']}")
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
            st.markdown(f"- ✅ {s['text']}")
        st.markdown("**Cosa stai pagando**")
        for s in th.get("paying", []):
            st.markdown(f"- 💶 {s}")
    with t2:
        st.markdown("**Rischi e punti deboli**")
        for s in th.get("weaknesses", []) or [{"text": "Nessuna debolezza marcata rispetto ai pari."}]:
            icon = SEV_ICON.get(s.get("severity"), "⚠️")
            st.markdown(f"- {icon} {s['text']}")
        st.markdown("**Cosa deve andare bene**")
        for s in th.get("must_go_right", []):
            st.markdown(f"- 🎯 {s}")
        st.markdown("**Cosa invaliderebbe la tesi (da monitorare)**")
        for s in th.get("monitor", []):
            st.markdown(f"- 👁️ {s}")
    st.caption(th.get("disclaimer", ""))
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
            from ire.valuation import dcf_value, implied_growth

            r1, r2, r3 = st.columns(3)
            dr = r1.slider("Tasso di sconto (rendimento richiesto)", 0.04, 0.16, float(round(rd["discount_rate"], 3)), 0.0025, format="%.2f")
            gt = r2.slider("Crescita perpetua dopo l'orizzonte", 0.0, 0.04, float(rd["terminal_growth"]), 0.0025, format="%.3f")
            yrs = r3.slider("Anni di crescita esplicita", 5, 15, int(rd["years"]))
            g, status = implied_growth(mv("market_cap"), rd["fcf_base"], dr, gt, yrs)
            st.metric("Crescita annua del FCF implicita nel prezzo", f"{g:.1%}" if g is not None else "n/d", help=status)
            hist = [(label(k), mv(k)) for k in ("fcf_ps_cagr_5y", "revenue_cagr_5y", "revenue_cagr_10y") if mv(k) is not None]
            if hist:
                st.caption("Per confronto — " + " · ".join(f"{n}: {v:.1%}" for n, v in hist))
            ug = st.slider("Scenario: la tua ipotesi di crescita annua del FCF", -0.10, 0.30, float(round(g or 0.05, 2)), 0.01, format="%.2f")
            val = dcf_value(rd["fcf_base"], ug, dr, gt, yrs)
            st.write(f"Con crescita {ug:.0%} per {yrs} anni il valore stimato è **{money(val, cur)}** contro una capitalizzazione di "
                     f"**{money(mv('market_cap'), cur)}** (**{val / mv('market_cap') - 1:+.0%}**).")
            st.caption(f"FCF di partenza: {money(rd['fcf_base'], cur)} ({rd['fcf_base_method']}). Tasso risk-free: {rd['risk_free']:.2%} "
                       f"({rd['risk_free_source']}) + premio al rischio {rd['erp']:.1%} (assunzione). È un modello: piccole variazioni "
                       "delle ipotesi cambiano molto il risultato. Usalo per capire le aspettative, non come prezzo obiettivo.")
        else:
            st.info("Reverse DCF non applicabile: " + (rd.get("status") if rd else
                    "banca/assicurazione o free cash flow non positivo/stabile (il DCF sui flussi di cassa non è significativo)."))

    with tabs[2]:
        px = data.prices(c.get("ticker"), c.get("price_currency"))
        if px.empty:
            st.info("Prezzi non disponibili.")
        else:
            fx = data.fx_table()
            eur = fx.series_to_eur(px["adj_close"], c.get("price_currency") or "USD")
            bench = data.prices("SWDA.MI", "EUR")
            start = eur.index.max() - pd.DateOffset(years=5)
            s = {f"{c.get('ticker')} (rendimento totale in EUR)": eur[eur.index >= start]}
            if not bench.empty:
                s["MSCI World (SWDA.MI, EUR)"] = bench["adj_close"][bench.index >= start]
            s = {k: v / v.dropna().iloc[0] * 100 for k, v in s.items() if v.dropna().size}
            st.plotly_chart(charts.lines(s, "Crescita di 100 € investiti 5 anni fa (dividendi reinvestiti)"), width="stretch")
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
        if fl.empty:
            st.success("Nessuna segnalazione.")
        else:
            order = {"severe": 0, "high": 1, "medium": 2, "info": 3, "data": 4}
            fl = fl.sort_values(by="severity", key=lambda s: s.map(order))
            for _, f in fl.iterrows():
                ev = json.loads(f["evidence"]) if f["evidence"] and f["evidence"] != "null" else None
                line = f"{SEV_ICON.get(f['severity'], '')} **{SEV_IT.get(f['severity'], f['severity'])}** — {f['message']}"
                if f.get("source"):
                    line += f"  \n<small>Fonte: {f['source']}</small>"
                st.markdown(line, unsafe_allow_html=True)
                if isinstance(ev, dict):
                    if ev.get("snippet"):
                        st.caption(f"«…{ev['snippet']}…»")
                    if ev.get("url"):
                        st.markdown(f"<small>[Apri il documento]({ev['url']})</small>", unsafe_allow_html=True)
        qd = d["qualitative"]
        if qd:
            st.markdown("#### Report annuali analizzati")
            for f in qd.get("filings", []):
                st.markdown(f"- [{f['form']} depositato il {f['filed']}]({f['url']})")
            rdiff = qd.get("risk_diff")
            if rdiff:
                st.markdown(f"**Fattori di rischio: {rdiff['new_count']} frasi nuove e {rdiff['removed_count']} rimosse** rispetto al "
                            f"report precedente ({rdiff['new_share']:.0%} del testo è nuovo). Le più lunghe tra le nuove:")
                for sentence in rdiff.get("new_examples", [])[:6]:
                    st.caption(f"➕ {sentence}")
            for n in qd.get("notes", []):
                st.caption(f"ℹ️ {n}")
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
            st.markdown(f"- {n}")
        st.markdown(f"- Capitalizzazione: {me.get('market_cap_method', 'n/d')}" + (f" · {me['fx_note']}" if me.get("fx_note") else ""))
        if me.get("adr_ratio"):
            st.markdown(f"- ADR: rapporto azioni ordinarie/ADR stimato = {me['adr_ratio']:.3g} (da filing SEC vs azioni Yahoo)")
        cf = d["conflicts"]
        if not cf.empty:
            st.markdown("**⚖️ Discrepanze tra fonti**")
            st.dataframe(cf, hide_index=True, width="stretch",
                         column_config={"pct_diff": st.column_config.NumberColumn("Differenza", format="%.1%%")})
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
        if "market_cap" in peers:
            peers["d"] = (np.log(peers["market_cap"].astype(float).clip(lower=1)) - np.log(max(float(row.get("market_cap") or 1), 1))).abs()
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
    head = sub[["name", "classification", "valuation_verdict", "robust_score", "quality", "growth", "financial_strength", "valuation"]].T
    head.index = ["Nome", "Classificazione", "Valutazione", "Punteggio robusto", "Qualità", "Crescita", "Solidità", "Economicità"]
    st.dataframe(head.astype(str).replace({"nan": "n/d", "None": "n/d"}), width="stretch")
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
            col.caption(f"{SEV_ICON[f['severity']]} {f['message']}")
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
    conf = g2.multiselect("Confidenza", ["alta", "media", "bassa"])
    tier = g3.multiselect("Qualità dati", ["A", "B"], format_func=lambda x: "A (SEC)" if x == "A" else "B (Yahoo)")
    min_mc = g4.number_input("Capitalizzazione minima (mld, valuta di bilancio)", 0.0, 5000.0, 0.0, 1.0)
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
    if min_mc and "market_cap" in df:
        df = df[pd.to_numeric(df.market_cap, errors="coerce") >= min_mc * 1e9]
    df = df.sort_values("robust_score", ascending=False, na_position="last")
    st.caption(f"{len(df)} società. Clicca una riga per aprire la scheda.")
    view = df[["ticker", "name", "sector", "region", "robust_score", "rank_spread", "quality", "growth", "financial_strength",
               "valuation", "classification", "valuation_verdict", "confidence", "pe", "fcf_sbc_yield", "roic_5y_median",
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
                                     "confidence": "Confidenza", "pe": st.column_config.NumberColumn("P/E", format="%.1f"),
                                     "fcf_sbc_yield": st.column_config.NumberColumn("FCF yield netto SBC", format="percent"),
                                     "roic_5y_median": st.column_config.NumberColumn("ROIC 5a", format="percent"),
                                     "revenue_cagr_5y": st.column_config.NumberColumn("Crescita ricavi 5a", format="percent"),
                                     "net_debt_ebitda": st.column_config.NumberColumn("Debito netto/EBITDA", format="%.1f")})
    if ev and ev.selection and ev.selection.rows:
        go_company(df.iloc[ev.selection.rows[0]]["company_id"])
    st.download_button("⬇️ Scarica CSV", df.drop(columns=["label"], errors="ignore").to_csv(index=False).encode("utf-8"),
                       "classifica.csv", "text/csv")


# =====================================================================================
def _portfolio_analytics_view(an: dict, tick: dict[str, str]):
    if not an:
        return
    c = st.columns(5)
    c[0].metric("Volatilità attesa", f"{an['volatility']:.1%}" if an.get("volatility") else "n/d",
                help="Covarianza a 3 anni (rendimenti settimanali in EUR) con shrinkage di Ledoit-Wolf")
    c[1].metric("N. effettivo di titoli", f"{an.get('effective_n', 0):.1f}", help="1/Σpesi²: quanti titoli 'equivalenti' a pesi uguali")
    c[2].metric("Rapporto di diversificazione", f"{an.get('diversification_ratio', 0):.2f}",
                help="Media ponderata delle volatilità / volatilità del portafoglio. Più alto = più benefici dalla diversificazione")
    c[3].metric("Correlazione media", f"{an['avg_pair_correlation']:.2f}" if an.get("avg_pair_correlation") is not None else "n/d")
    c[4].metric("Beta vs MSCI World", f"{an['beta']:.2f}" if an.get("beta") is not None else "n/d")
    for w in an.get("warnings", []):
        st.warning(w)
    e1, e2, e3 = st.columns(3)
    if an.get("sector_exposure"):
        e1.plotly_chart(charts.donut_free_bars({sector_it(k): v for k, v in an["sector_exposure"].items()}, "Settori"), width="stretch")
    if an.get("region_exposure"):
        e2.plotly_chart(charts.donut_free_bars(an["region_exposure"], "Aree geografiche (sede)"), width="stretch")
    if an.get("currency_exposure"):
        e3.plotly_chart(charts.donut_free_bars(an["currency_exposure"], "Valute di quotazione"), width="stretch")
    if an.get("factor_tilts"):
        st.markdown("**Esposizione ai fattori** (media ponderata dei percentili: 50 = neutro)")
        st.plotly_chart(charts.pillar_bars({k: v for k, v in an["factor_tilts"].items()}), width="stretch")
    if an.get("risk_contribution"):
        rc = an["risk_contribution"]
        st.plotly_chart(charts.donut_free_bars({tick.get(k, k): v for k, v in rc.items()}, "Contributo al rischio totale"),
                        width="stretch")
    hc = an.get("hist_cum")
    if hc:
        s = {"Portafoglio (pesi attuali)": pd.Series(hc["values"], index=pd.to_datetime(hc["index"]))}
        if an.get("bench_cum"):
            s["MSCI World"] = pd.Series(an["bench_cum"]["values"], index=pd.to_datetime(an["bench_cum"]["index"]))
        st.plotly_chart(charts.lines({k: v * 100 for k, v in s.items()}, "Comportamento storico dei pesi attuali (base 100, EUR)"),
                        width="stretch")
        st.caption("⚠️ Non è un backtest della strategia: i titoli sono scelti OGGI con i dati di oggi (look-ahead e survivorship bias). "
                   "Serve a capire come oscillerebbe questo portafoglio, non quanto renderà. "
                   + (f"Rendimento annuo storico {an['hist_return_ann']:.1%} vs benchmark {an.get('bench_return_ann', float('nan')):.1%}; "
                      f"perdita massima {an['hist_max_drawdown']:.0%} vs {an.get('bench_max_drawdown', float('nan')):.0%}." if an.get("hist_return_ann") is not None else ""))
    if an.get("stress_window"):
        sw = an["stress_window"]
        st.info(f"**Stress test storico:** nel peggior trimestre del mercato ({sw['start']} → {sw['end']}) il benchmark ha fatto "
                f"{sw['benchmark']:.0%}, questo portafoglio {sw['portfolio']:.0%}.")
    cm = an.get("correlation_matrix")
    if cm and cm.get("index"):
        corr = pd.DataFrame(cm["values"], index=cm["index"], columns=cm["columns"])
        labels_ = [tick.get(i, i) for i in corr.index]
        st.plotly_chart(charts.heatmap(corr, labels_), width="stretch")


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
            st.info("Nessun portafoglio proposto nell'ultima analisi." + (" " + " ".join(p.get("log", [])) if p else ""))
        else:
            st.caption("Proposta generata dalla metodologia: " + p.get("method", "") +
                       " È un punto di partenza per la tua ricerca, non una raccomandazione personalizzata.")
            pos = pd.DataFrame(p["positions"])
            view = pos[["ticker", "name", "weight", "role", "sector", "region", "classification", "reason", "risk_note"]].copy()
            view["sector"] = view["sector"].map(sector_it)
            ev = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                              key=tkey("portfolio"),
                              column_config={"ticker": "Ticker", "name": "Società",
                                             "weight": st.column_config.NumberColumn("Peso", format="percent"),
                                             "role": "Ruolo", "sector": "Settore", "region": "Area", "classification": "Classificazione",
                                             "reason": st.column_config.TextColumn("Perché è nel portafoglio", width="large"),
                                             "risk_note": "Rischio che porta"})
            if ev and ev.selection and ev.selection.rows:
                go_company(pos.iloc[ev.selection.rows[0]]["company_id"])
            with st.expander("Ruoli: cosa significano"):
                for role, why in pos.groupby("role")["role_reason"].first().items():
                    st.markdown(f"- **{role}**: {why}")
            with st.expander("Come è stata costruita la selezione"):
                for l in p.get("log", []):
                    st.markdown(f"- {l}")
                rej = p.get("rejected_examples", {})
                if rej:
                    st.markdown("Esempi di candidati esclusi per vincoli di diversificazione:")
                    st.dataframe(pd.DataFrame([{"Ticker": tick.get(k, k), "Motivo": v} for k, v in rej.items()]), hide_index=True)
            _portfolio_analytics_view(p.get("analytics", {}), tick)
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


def _analyze_user(ed: pd.DataFrame, U: pd.DataFrame):
    from ire.portfolio import analyze_portfolio
    from ire.risk import weekly_returns
    from ire.sources import yahoo

    ed = ed.dropna(subset=["weight"])
    ed = ed[ed["ticker"].astype(str).str.strip() != ""]
    if ed.empty:
        st.warning("Inserisci almeno un titolo con peso.")
        return
    fx = data.fx_table()
    weekly, weights, meta_rows, missing = {}, {}, [], []
    by_t = U.set_index("ticker")
    for _, r in ed.iterrows():
        t = str(r["ticker"]).strip().upper()
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
                missing.append(t)
                continue
            cur, div = yahoo.normalize_currency(info.get("currency") or "USD")
            px = raw / div
            meta_rows.append({"company_id": cid, "sector": info.get("sector") or "n/d", "region": None, "price_currency": cur})
        if px.empty:
            missing.append(t)
            continue
        eur = fx.series_to_eur(px["adj_close"], cur or "USD")
        if eur is None:
            missing.append(t)
            continue
        weekly[cid] = weekly_returns(eur)
        weights[cid] = float(r["weight"])
    if missing:
        st.warning("Prezzi non disponibili per: " + ", ".join(missing))
    if not weights:
        return
    W = pd.DataFrame(weekly)
    bench = data.prices("SWDA.MI", "EUR")
    bw = weekly_returns(bench["adj_close"]) if not bench.empty else None
    an = analyze_portfolio(weights, W, pd.DataFrame(meta_rows), bw)
    tick = {**dict(zip(U.company_id, U.ticker)), **{k: k.replace("USER:", "") for k in weights if k.startswith("USER:")}}
    st.subheader("Le tue posizioni viste dal sistema")
    rows = []
    tot = sum(weights.values())
    for cid, w in weights.items():
        rr = U[U.company_id == cid]
        rows.append({"Ticker": tick.get(cid), "Peso": w / tot,
                     "Classificazione": rr["classification"].iloc[0] if len(rr) else "non nell'universo (solo prezzi)",
                     "Valutazione": rr["valuation_verdict"].iloc[0] if len(rr) else "",
                     "Punteggio robusto": rr["robust_score"].iloc[0] if len(rr) else None})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Peso": st.column_config.NumberColumn(format="percent")})
    _portfolio_analytics_view(an if isinstance(an, dict) else {}, tick)


# =====================================================================================
def page_changes():
    st.title("🔔 Cosa è cambiato")
    from ire.changes import compute_changes

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
        ch = compute_changes(data.con(), nid, oid)
        if ch.empty:
            st.success("Nessun cambiamento rilevante.")
        else:
            wl = set(data.watchlist())
            only_wl = st.toggle("Solo watchlist", value=False)
            if only_wl:
                ch = ch[ch.ticker.isin(wl)]
            kinds = st.multiselect("Tipo", sorted(ch["tipo"].unique()), default=sorted(ch["tipo"].unique()))
            ch = ch[ch["tipo"].isin(kinds)]
            st.dataframe(ch.drop(columns=["company_id"]), hide_index=True, width="stretch", height=500)
    st.subheader("⭐ Watchlist")
    wl = data.watchlist()
    new_t = st.text_input("Aggiungi ticker (notazione Yahoo)")
    if st.button("Aggiungi") and new_t.strip():
        data.set_watch(new_t.strip().upper(), True)
        st.rerun()
    if wl and RUN:
        U = data.universe(RUN)
        w = U[U.ticker.isin(wl)][["ticker", "name", "classification", "valuation_verdict", "robust_score", "confidence"]]
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
        lg = data.q("SELECT ts, level, stage, message FROM log WHERE run_id=? ORDER BY ts", [RUN])
        with st.expander("Log dell'ultima analisi"):
            st.dataframe(lg, hide_index=True, width="stretch")
    with t2:
        allc = data.all_companies()
        ex = allc[allc.in_universe == 0]
        st.caption(f"{len(ex)} titoli esaminati ma esclusi dall'universo, con il motivo.")
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
            st.markdown(open(p, encoding="utf-8").read())


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
