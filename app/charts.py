"""Plotly chart helpers with a consistent, restrained style (one axis per chart, thin marks)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
GOOD, WARN, BAD, NEUTRAL = "#1a7f37", "#b7791f", "#c62828", "#8a8a85"


def base_layout(fig: go.Figure, title: str = "", height: int = 320, yfmt: str | None = None) -> go.Figure:
    fig.update_layout(
        title={"text": title, "x": 0, "font": {"size": 15}},
        height=height, margin={"l": 10, "r": 10, "t": 40 if title else 10, "b": 10},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend={"orientation": "h", "y": -0.18, "x": 0},
        hovermode="x unified",
        font={"size": 12},
    )
    fig.update_xaxes(showgrid=False, linecolor="rgba(128,128,128,0.4)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zerolinecolor="rgba(128,128,128,0.4)")
    if yfmt:
        fig.update_yaxes(tickformat=yfmt)
    return fig


def bars(df: pd.DataFrame, cols: list[str], names: list[str], title: str, yfmt: str | None = None, scale: float = 1.0,
         suffix: str = "") -> go.Figure:
    fig = go.Figure()
    for i, (c, n) in enumerate(zip(cols, names)):
        if c not in df.columns:
            continue
        s = df[c].dropna() / scale
        fig.add_bar(x=[d.year if hasattr(d, "year") else d for d in s.index], y=s.values, name=n,
                    marker_color=SERIES[i % len(SERIES)], hovertemplate=f"%{{y:,.2f}}{suffix}<extra>{n}</extra>")
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05)
    return base_layout(fig, title, yfmt=yfmt)


def lines(series: dict[str, pd.Series], title: str, yfmt: str | None = None, height: int = 320) -> go.Figure:
    fig = go.Figure()
    for i, (n, s) in enumerate(series.items()):
        s = s.dropna()
        if s.empty:
            continue
        x = [d.year if hasattr(d, "year") and len(s) < 30 else d for d in s.index]
        fig.add_scatter(x=x, y=s.values, name=n, mode="lines+markers" if len(s) < 30 else "lines",
                        line={"width": 2, "color": SERIES[i % len(SERIES)]}, marker={"size": 8})
    return base_layout(fig, title, yfmt=yfmt, height=height)


def pillar_bars(pillars: dict[str, float | None]) -> go.Figure:
    names = list(pillars.keys())
    vals = [v if v is not None else 0 for v in pillars.values()]
    colors = [GOOD if v is not None and v >= 65 else BAD if v is not None and v <= 35 else NEUTRAL for v in pillars.values()]
    text = [f"{v:.0f}" if v is not None else "n/d" for v in pillars.values()]
    fig = go.Figure(go.Bar(x=vals, y=names, orientation="h", marker_color=colors, text=text, textposition="outside",
                           hovertemplate="%{y}: %{x:.0f}/100<extra></extra>"))
    fig.update_xaxes(range=[0, 108], tickvals=[0, 35, 50, 65, 100])
    fig.update_yaxes(autorange="reversed")
    fig.add_vline(x=50, line_dash="dot", line_color="rgba(128,128,128,0.6)")
    return base_layout(fig, "", height=230)


def heatmap(corr: pd.DataFrame, labels: list[str]) -> go.Figure:
    fig = go.Figure(go.Heatmap(z=corr.values, x=labels, y=labels, zmin=-1, zmax=1,
                               colorscale=[[0, "#2a78d6"], [0.5, "#f2f2f0"], [1, "#e34948"]],
                               hovertemplate="%{y} / %{x}: %{z:.2f}<extra></extra>"))
    return base_layout(fig, "Correlazioni (rendimenti settimanali 3 anni, EUR)", height=520)


def donut_free_bars(exposure: dict[str, float], title: str) -> go.Figure:
    items = sorted(exposure.items(), key=lambda kv: -kv[1])
    fig = go.Figure(go.Bar(x=[v for _, v in items], y=[k for k, _ in items], orientation="h", marker_color=SERIES[0],
                           text=[f"{v:.0%}" for _, v in items], textposition="outside",
                           hovertemplate="%{y}: %{x:.1%}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat=".0%", range=[0, max(v for _, v in items) * 1.25 if items else 1])
    return base_layout(fig, title, height=max(180, 36 * len(items) + 60))
