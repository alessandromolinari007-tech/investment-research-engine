"""Plotly chart helpers with a consistent, restrained style (one axis per chart, thin marks)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Validated categorical palette (fixed order, never cycled past 8) and status colours from the dataviz reference
# palette; chart chrome uses the same warm neutrals as the app theme (.streamlit/config.toml).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
GOOD, WARN, BAD, NEUTRAL = "#0ca30c", "#fab219", "#d03b3b", "#c3c2b7"
INK, INK_2, MUTED, GRID, AXIS = "#1f1e1c", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
FONT = "Geist, system-ui, -apple-system, 'Segoe UI', sans-serif"


def base_layout(fig: go.Figure, title: str = "", height: int = 320, yfmt: str | None = None) -> go.Figure:
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "font": {"size": 15, "color": INK, "family": FONT}},
        height=height, margin={"l": 8, "r": 8, "t": 44 if title else 8, "b": 8},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend={"orientation": "h", "y": -0.16, "x": 0, "font": {"color": INK_2}},
        hovermode="x unified",
        hoverlabel={"bgcolor": "#fcfcfb", "bordercolor": GRID, "font": {"color": INK, "family": FONT}},
        font={"size": 12, "color": INK_2, "family": FONT},
        barcornerradius=4,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont={"color": MUTED})
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=AXIS, tickfont={"color": MUTED})
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
    # one series → one hue; the value label carries the level (colour never carries meaning alone)
    colors = [SERIES[0] if v is not None else NEUTRAL for v in pillars.values()]
    text = [f"{v:.0f}" if v is not None else "n/d" for v in pillars.values()]
    fig = go.Figure(go.Bar(x=vals, y=names, orientation="h", marker_color=colors, text=text, textposition="outside",
                           hovertemplate="%{y}: %{x:.0f}/100<extra></extra>"))
    fig.update_layout(bargap=0.45)                 # thin marks
    fig.update_xaxes(range=[0, 108], tickvals=[0, 35, 50, 65, 100])
    fig.update_yaxes(autorange="reversed")
    fig.add_vline(x=50, line_dash="dot", line_color=AXIS)
    fig = base_layout(fig, "", height=230)
    fig.update_layout(hovermode="closest")
    return fig


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
    fig = base_layout(fig, title, height=max(180, 36 * len(items) + 60))
    fig.update_layout(hovermode="closest")
    return fig


def count_bars(counts: dict[str, int]) -> go.Figure:
    """Horizontal bars of counts (single series: one hue, value labels, no legend)."""
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    fig = go.Figure(go.Bar(x=[v for _, v in items], y=[k for k, _ in items], orientation="h", marker_color=SERIES[0],
                           text=[str(v) for _, v in items], textposition="outside", cliponaxis=False,
                           hovertemplate="%{y}: %{x}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(showticklabels=False, range=[0, max((v for _, v in items), default=1) * 1.15])
    fig = base_layout(fig, "", height=max(200, 30 * len(items) + 40))
    fig.update_layout(hovermode="closest")
    return fig
