"""Market Regime — post-market technicals dashboard (Streamlit).

    streamlit run app.py

Reuses the data/compute layer from technicals_dashboard.py.
"""

from datetime import date, datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from technicals_dashboard import compute

ACCENT = "#e8820c"
SMA_COLORS = ["#d8892b", "#4a6fa5", "#3a7d44", "#8a5fa0"]
GREEN, RED = "#1a7a2e", "#b32d2d"
MONO = "SF Mono, Menlo, Consolas, monospace"

st.set_page_config(page_title="Market Regime — Technicals", layout="wide")

st.markdown(f"""
<style>
  html, body, [class*="st-"], .stMarkdown, input, textarea {{ font-family: {MONO} !important; }}
  header[data-testid="stHeader"] {{ display: none; }}
  .stApp {{ background: #fff; }}
  .block-container {{ padding-top: 0.8rem; max-width: 1500px; }}
  .shp-header {{ background: #fff; padding: 10px 0 6px; margin-bottom: 0; }}
  .shp-header .title {{ color: {ACCENT}; font-weight: bold; font-size: 17px; letter-spacing: 1px; }}
  .shp-header .asof {{ color: #666; font-size: 11px; margin-left: 10px; }}
  .shp-header .sub {{ color: #888; font-size: 10px; letter-spacing: 1px; }}
  .shp-header .right {{ float: right; color: #888; font-size: 10px; padding-top: 6px; }}
  .card {{ border: 1px solid #ccc; border-top: 2px solid #d8892b; padding: 8px 12px;
           background: #fff; min-height: 86px; }}
  .card .label {{ font-size: 9px; letter-spacing: 1px; color: #666; margin-bottom: 4px; }}
  .card .value {{ font-size: 20px; font-weight: bold; color: #222; }}
  .card .note {{ font-size: 10px; color: #666; margin-top: 3px; }}
  .pos {{ color: {GREEN} !important; }} .neg {{ color: {RED} !important; }}
  .panel-title {{ font-size: 12px; font-weight: bold; letter-spacing: 1px; margin: 4px 0 2px; }}
  table.shp {{ width: 100%; border-collapse: collapse; font-size: 13px; font-family: {MONO}; }}
  table.shp th {{ text-align: right; font-size: 10px; letter-spacing: 1px; color: #666;
                  border-bottom: 1px solid #999; padding: 4px 8px; }}
  table.shp th:first-child, table.shp td:first-child {{ text-align: left; }}
  table.shp td {{ text-align: right; padding: 4px 8px; border-bottom: 1px solid #eee; }}
  table.shp tr:nth-child(even) td {{ background: #f7f6f4; }}
  table.shp tr.current td {{ background: #f6e8d0; border-top: 2px solid #333;
                             border-bottom: 2px solid #333; font-weight: bold; }}
  div[data-testid="stForm"] {{ border: none; border-bottom: 1px solid #d8892b;
                               border-radius: 0; padding: 0 0 10px 0; }}
  button[kind="primaryFormSubmit"], button[kind="primary"] {{
    background: {ACCENT}; color: #111; font-weight: bold; border-radius: 0; }}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_technicals(ticker, ma_periods, slope_window, period):
    return compute(ticker, list(ma_periods), slope_window, period)


def sign(v, digits=2, suffix=""):
    return f"{v:+.{digits}f}{suffix}"


def sign_class(v):
    return "pos" if v >= 0 else "neg"


def base_layout(height, **kw):
    return dict(
        height=height, margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="#fff", paper_bgcolor="#fff",
        font=dict(family=MONO, size=11),
        legend=dict(orientation="h", yanchor="top", y=-0.12, font=dict(size=10)),
        xaxis=dict(gridcolor="#eee", nticks=10),
        yaxis=dict(gridcolor="#eee"),
        hovermode="x unified", **kw,
    )


# --- header -----------------------------------------------------------------
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
st.markdown(f"""
<div class="shp-header">
  <span class="right">Last updated {now}</span>
  <span class="title">MARKET REGIME</span>
  <span class="asof" id="hdr-asof"></span>
  <div class="sub">POST-MARKET RECAP DASHBOARD</div>
</div>
""", unsafe_allow_html=True)

# --- controls -------------------------------------------------------------
with st.form("controls"):
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1, 1, 0.8])
    ticker = c1.text_input("TICKER", "TSLA")
    ma_raw = c2.text_input("MA PERIODS", "20,50,200")
    slope_window = c3.number_input("SLOPE WINDOW (D)", min_value=1, value=5)
    period = c4.selectbox("PERIOD", ["6M", "1Y", "2Y", "5Y", "MAX"], index=1)
    c5.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    c5.form_submit_button("LOAD", type="primary")

try:
    ma_periods = tuple(sorted({int(x) for x in ma_raw.split(",") if x.strip()}))
    if not ma_periods:
        raise ValueError("at least one MA period is required")
    d = load_technicals(ticker.strip().upper(), ma_periods, int(slope_window), period)
except Exception as e:
    st.error(f"ERROR: {e}")
    st.stop()

st.caption(f"Loaded {d['ticker']} as of {d['as_of']}.")
c, L, ch = d["cards"], d["long_period"], d["chart"]

# --- metric cards ----------------------------------------------------------
def card(col, label, value, note="", value_cls="", note_cls=""):
    col.markdown(
        f'<div class="card"><div class="label">{label}</div>'
        f'<div class="value {value_cls}">{value}</div>'
        f'<div class="note {note_cls}">{note}</div></div>',
        unsafe_allow_html=True,
    )

cols = st.columns(5)
card(cols[0], "PRICE", f"{c['price']:.2f}", f"AS OF {d['as_of']}")
card(cols[1], f"{L} DMA / DISTANCE", f"{c['long_sma']:.2f}",
     sign(c["distance_pct"], 2, "%"), note_cls=sign_class(c["distance_pct"]))
card(cols[2], "Z-SCORE", f"{c['zscore']:.2f}", c["zscore_label"])
slope_note_cls = ("pos" if "UP" in c["slope_label"]
                  else "neg" if "DOWN" in c["slope_label"] else "")
card(cols[3], f"{L} DMA SLOPE (ANN.)", sign(c["slope_ann_pct"], 1, "%"),
     c["slope_label"], value_cls=sign_class(c["slope_ann_pct"]), note_cls=slope_note_cls)
card(cols[4], "Z-SCORE BAND", c["zscore_band"])

# --- price chart -----------------------------------------------------------
st.markdown(f'<div class="panel-title">PRICE, MOVING AVERAGES &amp; '
            f'{d["band_sigma"]}σ BAND ({L} DMA)</div>', unsafe_allow_html=True)
fig = go.Figure()
fig.add_scatter(x=ch["dates"], y=ch["lower"], line=dict(width=0),
                name=f"-{d['band_sigma']}σ band", showlegend=False, hoverinfo="skip")
fig.add_scatter(x=ch["dates"], y=ch["upper"], line=dict(width=0), fill="tonexty",
                fillcolor="rgba(216,137,43,0.15)", name=f"±{d['band_sigma']}σ band",
                hoverinfo="skip")
fig.add_scatter(x=ch["dates"], y=ch["close"], name=f"{d['ticker']} Close",
                line=dict(color="#222", width=1.4))
for i, (p, s) in enumerate(ch["smas"].items()):
    fig.add_scatter(x=ch["dates"], y=s, name=f"{p}-day SMA",
                    line=dict(color=SMA_COLORS[i % len(SMA_COLORS)], width=1.2))
fig.update_layout(base_layout(340))
fig.update_yaxes(range=ch["yrange"])
st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# --- slope + z-score charts --------------------------------------------------
left, right = st.columns(2)
with left:
    st.markdown('<div class="panel-title">SMA SLOPE (% PER DAY)</div>',
                unsafe_allow_html=True)
    fig = go.Figure()
    for i, (p, s) in enumerate(ch["slopes"].items()):
        fig.add_scatter(x=ch["dates"], y=s, name=f"{p}-day SMA slope",
                        line=dict(color=SMA_COLORS[i % len(SMA_COLORS)], width=1.2))
    fig.add_hline(y=0, line=dict(color="#999", width=1, dash="dash"))
    fig.update_layout(base_layout(240))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
with right:
    st.markdown(f'<div class="panel-title">Z-SCORE VS {L} DMA</div>',
                unsafe_allow_html=True)
    colors = ["rgba(74,145,120,0.85)" if (v or 0) >= 0.5
              else "rgba(179,45,45,0.85)" if (v or 0) <= -0.5
              else "rgba(150,150,150,0.7)" for v in ch["zscore"]]
    fig = go.Figure()
    fig.add_bar(x=ch["dates"], y=ch["zscore"], marker_color=colors,
                marker_line_width=0, showlegend=False)
    for y in (d["band_sigma"], -d["band_sigma"]):
        fig.add_hline(y=y, line=dict(color="#999", width=1, dash="dash"))
    fig.update_layout(base_layout(240), bargap=0)
    # collapse non-trading days so the bars sit flush against each other
    d0, d1 = date.fromisoformat(ch["dates"][0]), date.fromisoformat(ch["dates"][-1])
    have = set(ch["dates"])
    holidays = [str(d0 + timedelta(n)) for n in range((d1 - d0).days + 1)
                if (d0 + timedelta(n)).weekday() < 5
                and str(d0 + timedelta(n)) not in have]
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]),
                                  dict(values=holidays)])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# --- forward-return stats table ----------------------------------------------
s = d["stats"]
st.markdown(f'<div class="panel-title">{d["fwd_days"]}-DAY FORWARD RETURN BY Z-SCORE '
            f'BAND ({s["start"]} → {s["end"]}, {s["sessions"]} sessions)</div>',
            unsafe_allow_html=True)
rows_html = ""
for r in s["rows"]:
    cls = ' class="current"' if r["band"] == s["current_band"] else ""
    if r["count"] == 0:
        cells = "<td>—</td>" * 3
    else:
        cells = (f'<td class="{sign_class(r["mean"])}">{sign(r["mean"])}</td>'
                 f'<td class="{sign_class(r["median"])}">{sign(r["median"])}</td>'
                 f'<td>{r["win_rate"]:.1f}</td>')
    rows_html += f'<tr{cls}><td>{r["band"]}</td><td>{r["count"]}</td>{cells}</tr>'
st.markdown(
    '<table class="shp"><thead><tr><th>Z-SCORE BAND</th><th>COUNT</th>'
    '<th>MEAN RETURN %</th><th>MEDIAN RETURN %</th><th>WIN RATE %</th></tr></thead>'
    f'<tbody>{rows_html}</tbody></table>',
    unsafe_allow_html=True,
)
