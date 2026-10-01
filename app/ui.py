"""Visual layer of the Streamlit app: CSS, page headers, badges.

Editorial, low-noise style for a data-dense research tool: warm off-white canvas, charcoal ink, one accent
(ink blue), muted semantic badges, tabular figures for numbers. No emoji: icons are Material Symbols,
meaning is carried by text labels (colour is never the only signal).
"""
from __future__ import annotations

import html

import streamlit as st

from ire.scoring import (C_AVG, C_CHEAP, C_GROWTH, C_NODATA, C_QDET, C_QDISC, C_QFAIR, C_QFULL, C_REDFLAG,
                         C_TEMP, C_TRAP)

# Streamlit markdown badge colours, by MEANING (green = favourable, orange = caution, red = risk)
CLASS_BADGE = {
    C_TEMP: "green", C_QDISC: "green", C_QFAIR: "blue", C_QFULL: "gray", C_QDET: "orange", C_GROWTH: "violet",
    C_CHEAP: "blue", C_TRAP: "orange", C_REDFLAG: "red", C_AVG: "gray", C_NODATA: "gray",
}
VERDICT_BADGE = {"relativamente economica": "green", "ragionevolmente valutata": "gray", "costosa": "red",
                 "non determinabile": "gray"}
SIGNAL_BADGE = {"economica": "green", "ragionevole": "gray", "costosa": "red"}
SEV_BADGE = {"severe": "red", "high": "red", "medium": "orange", "info": "blue", "data": "gray"}
SEV_IT = {"severe": "grave", "high": "alta", "medium": "media", "info": "informativa", "data": "qualità dati"}

CSS = """
<style>
:root {
  --ink: #1f1e1c; --ink-2: #52514e; --muted: #7d7b75; --line: #e3e0d8; --surface: #fcfcfb; --canvas: #f7f6f3;
  --accent: #256abf;
}
/* layout: comfortable reading width, generous vertical rhythm */
.block-container { max-width: 1320px; padding-top: 4.6rem; padding-bottom: 4rem; }
h1, h2, h3, h4 { letter-spacing: -0.02em; color: var(--ink); text-wrap: balance; }
h1 { font-weight: 650; }
h2, h3 { font-weight: 600; }
p, li { text-wrap: pretty; }
/* numbers line up in tables and metrics */
[data-testid="stMetricValue"], [data-testid="stDataFrame"], [data-testid="stTable"], .ire-num {
  font-variant-numeric: tabular-nums;
}
/* metric tiles: flat surface, hairline border, no heavy shadow */
[data-testid="stMetric"] {
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px 12px;
}
[data-testid="stMetricLabel"] p { color: var(--muted); font-size: 0.78rem; font-weight: 500; letter-spacing: 0.01em; }
[data-testid="stMetricValue"] { font-weight: 600; letter-spacing: -0.02em; }
/* expanders and bordered containers as quiet panels */
[data-testid="stExpander"] details { background: var(--surface); border-color: var(--line); }
[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); }
/* tabs: understated, active tab marked by the accent underline */
.stTabs [data-baseweb="tab-list"] { gap: 1.25rem; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] { padding-left: 0; padding-right: 0; font-weight: 500; }
/* buttons: tactile press */
.stButton button, .stDownloadButton button { transition: transform 120ms ease, background-color 160ms ease; }
.stButton button:active, .stDownloadButton button:active { transform: scale(0.98); }
/* page header */
.ire-eyebrow { color: var(--muted); font-size: 0.78rem; font-weight: 500; letter-spacing: 0.04em;
  text-transform: uppercase; margin-bottom: 0.2rem; }
.ire-title { font-size: 2.1rem; line-height: 1.12; font-weight: 650; letter-spacing: -0.03em; color: var(--ink);
  margin: 0 0 0.35rem 0; }
.ire-sub { color: var(--ink-2); max-width: 68ch; margin: 0 0 1.2rem 0; line-height: 1.55; }
.ire-section { margin-top: 1.8rem; }
.ire-section h3 { margin-bottom: 0.1rem; }
.ire-note { color: var(--muted); font-size: 0.9rem; max-width: 80ch; margin-bottom: 0.6rem; }
.ire-rule { border: none; border-top: 1px solid var(--line); margin: 1.6rem 0; }
/* footer note */
.ire-footer { color: var(--muted); font-size: 0.8rem; margin-top: 3rem; border-top: 1px solid var(--line);
  padding-top: 0.8rem; }
/* keyboard focus always visible */
a:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def page_header(title: str, subtitle: str = "", eyebrow: str = "") -> None:
    """Editorial page header (no st.title: keeps one consistent type scale across pages)."""
    parts = []
    if eyebrow:
        parts.append(f'<div class="ire-eyebrow">{_e(eyebrow)}</div>')
    parts.append(f'<h1 class="ire-title">{_e(title)}</h1>')
    if subtitle:
        parts.append(f'<p class="ire-sub">{_e(subtitle)}</p>')
    st.markdown("".join(parts), unsafe_allow_html=True)


def section(title: str, note: str = "") -> None:
    st.markdown(f'<div class="ire-section"><h3>{_e(title)}</h3></div>', unsafe_allow_html=True)
    if note:
        st.markdown(f'<p class="ire-note">{_e(note)}</p>', unsafe_allow_html=True)


def rule() -> None:
    st.markdown('<hr class="ire-rule">', unsafe_allow_html=True)


def _badge_text(text: str) -> str:
    # markdown badge content must not close the bracket or start LaTeX
    return str(text).replace("[", "(").replace("]", ")").replace("$", "\\$")


def badge(text: str, color: str = "gray") -> str:
    return f":{color}-badge[{_badge_text(text)}]"


def class_badge(cls: str | None) -> str:
    cls = cls or C_NODATA
    return badge(cls, CLASS_BADGE.get(cls, "gray"))


def verdict_badge(verdict: str | None) -> str:
    verdict = verdict or "non determinabile"
    return badge(verdict, VERDICT_BADGE.get(verdict, "gray"))


def signal_badge(verdict: str) -> str:
    return badge(verdict, SIGNAL_BADGE.get(verdict, "gray"))


def sev_badge(sev: str | None) -> str:
    return badge(SEV_IT.get(sev or "", sev or "n/d"), SEV_BADGE.get(sev or "", "gray"))


def footer() -> None:
    st.markdown('<div class="ire-footer">Dati: SEC EDGAR, Yahoo Finance, BCE, FRED, Wikipedia. Strumento di ricerca '
                'personale: non è un consiglio di investimento.</div>', unsafe_allow_html=True)
