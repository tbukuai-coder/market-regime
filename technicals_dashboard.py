#!/usr/bin/env python3
"""Market Regime — post-market technicals dashboard.

Single-file tool: stdlib HTTP server + yfinance data + Chart.js frontend.

    python3 technicals_dashboard.py [--port 8300]

Then open http://localhost:8300 (or the forwarded studio port).
"""

import argparse
import json
import math
import os
import pickle
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

FWD_DAYS = 40  # forward-return horizon for the z-score band table
BAND_SIGMA = 1.5  # width of the band drawn around the long SMA
SIGMA_WINDOW = 50  # rolling window for the std of (close - long SMA)
Z_BANDS = [
    (-math.inf, -2.0, "< -2.0"),
    (-2.0, -1.5, "-2.0 to -1.5"),
    (-1.5, -1.0, "-1.5 to -1.0"),
    (-1.0, -0.5, "-1.0 to -0.5"),
    (-0.5, 0.5, "-0.5 to 0.5"),
    (0.5, 1.0, "0.5 to 1.0"),
    (1.0, 1.5, "1.0 to 1.5"),
    (1.5, 2.0, "1.5 to 2.0"),
    (2.0, math.inf, "> 2.0"),
]
PERIOD_DAYS = {"6M": 126, "1Y": 252, "2Y": 504, "5Y": 1260, "MAX": None}

CACHE_DIR = os.path.join(tempfile.gettempdir(), "market_regime_cache")
CACHE_TTL = 3600  # seconds
_cache_lock = threading.Lock()


def fetch_history(ticker: str) -> pd.Series:
    """Full daily adjusted-close history, cached on disk for CACHE_TTL."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker.upper()}.pkl")
    with _cache_lock:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
            with open(path, "rb") as f:
                return pickle.load(f)
    import yfinance as yf

    df = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    if df.empty:
        raise ValueError(f"no data returned for {ticker!r}")
    close = df["Close"].dropna()
    close.index = close.index.tz_localize(None).normalize()
    with _cache_lock:
        with open(path, "wb") as f:
            pickle.dump(close, f)
    return close


def slope_label(ann_pct: float) -> str:
    if ann_pct > 10:
        return "STRONG UPTREND"
    if ann_pct > 2:
        return "UPTREND"
    if ann_pct >= -2:
        return "FLAT"
    if ann_pct >= -10:
        return "DOWNTREND"
    return "STRONG DOWNTREND"


def zscore_label(z: float) -> str:
    if z < -2:
        return "DEEPLY OVERSOLD"
    if z < -1:
        return "OVERSOLD"
    if z < -0.5:
        return "MILDLY OVERSOLD"
    if z <= 0.5:
        return "NEUTRAL"
    if z <= 1:
        return "MILDLY EXTENDED"
    if z <= 2:
        return "EXTENDED"
    return "DEEPLY EXTENDED"


def band_of(z: float):
    for lo, hi, label in Z_BANDS:
        if lo <= z < hi or (hi is Z_BANDS[-1][1] and z >= lo):
            return lo, hi, label
    return Z_BANDS[-1]


def compute(ticker: str, ma_periods, slope_window: int, period: str) -> dict:
    close = fetch_history(ticker)
    ma_periods = sorted(set(ma_periods))
    long_p = ma_periods[-1]
    if len(close) < long_p + slope_window + 5:
        raise ValueError(f"only {len(close)} sessions of data; need more than {long_p}")

    smas = {p: close.rolling(p).mean() for p in ma_periods}
    long_sma = smas[long_p]
    sigma = (close - long_sma).rolling(SIGMA_WINDOW).std()
    z = (close - long_sma) / sigma
    upper = long_sma + BAND_SIGMA * sigma
    lower = long_sma - BAND_SIGMA * sigma
    # slope in % per day over the trailing window, and annualized for the long SMA
    slopes = {
        p: (s / s.shift(slope_window) - 1) / slope_window * 100 for p, s in smas.items()
    }
    long_ratio = long_sma.iloc[-1] / long_sma.iloc[-1 - slope_window]
    ann_slope = (long_ratio ** (252 / slope_window) - 1) * 100

    # forward-return stats by z-score band, over the full history
    fwd = close.shift(-FWD_DAYS) / close - 1
    stats_df = pd.DataFrame({"z": z, "fwd": fwd}).dropna()
    rows = []
    for lo, hi, label in Z_BANDS:
        grp = stats_df.fwd[(stats_df.z >= lo) & (stats_df.z < hi)]
        rows.append({
            "band": label,
            "count": int(len(grp)),
            "mean": round(grp.mean() * 100, 2) if len(grp) else None,
            "median": round(grp.median() * 100, 2) if len(grp) else None,
            "win_rate": round((grp > 0).mean() * 100, 1) if len(grp) else None,
        })

    last = close.index[-1]
    z_now = float(z.iloc[-1])
    lo, hi, band_label = band_of(z_now)
    dist_pct = (close.iloc[-1] / long_sma.iloc[-1] - 1) * 100

    n = PERIOD_DAYS.get(period.upper())
    view = slice(-n, None) if n else slice(None)
    dates = [d.strftime("%Y-%m-%d") for d in close.index[view]]

    def ser(s):
        return [None if pd.isna(v) else round(float(v), 2) for v in s.iloc[view]]

    # y-axis ranges on price/SMAs only so the sigma band clips instead of
    # stretching the scale
    visible = pd.concat([close.iloc[view]] + [s.iloc[view] for s in smas.values()])
    y_lo, y_hi = float(visible.min()), float(visible.max())
    yrange = [math.floor(y_lo * 0.97 / 10) * 10, math.ceil(y_hi * 1.01 / 10) * 10]

    return {
        "ticker": ticker.upper(),
        "as_of": last.strftime("%Y-%m-%d"),
        "long_period": long_p,
        "band_sigma": BAND_SIGMA,
        "fwd_days": FWD_DAYS,
        "cards": {
            "price": round(float(close.iloc[-1]), 2),
            "long_sma": round(float(long_sma.iloc[-1]), 2),
            "distance_pct": round(float(dist_pct), 2),
            "zscore": round(z_now, 2),
            "zscore_label": zscore_label(z_now),
            "slope_ann_pct": round(float(ann_slope), 1),
            "slope_label": slope_label(ann_slope),
            "zscore_band": band_label,
        },
        "chart": {
            "dates": dates,
            "yrange": yrange,
            "close": ser(close),
            "smas": {str(p): ser(s) for p, s in smas.items()},
            "upper": ser(upper),
            "lower": ser(lower),
            "slopes": {str(p): ser(s) for p, s in slopes.items()},
            "zscore": ser(z),
        },
        "stats": {
            "start": stats_df.index[0].strftime("%Y-%m-%d"),
            "end": stats_df.index[-1].strftime("%Y-%m-%d"),
            "sessions": int(len(stats_df)),
            "rows": rows,
            "current_band": band_label,
        },
    }


PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Market Regime — Technicals</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "SF Mono", "Menlo", "Consolas", monospace; background: #fff;
         font-size: 12px; color: #222; }
  header { background: #fff; color: #222; padding: 10px 18px; display: flex;
           justify-content: space-between; align-items: baseline; }
  header .title { color: #e8820c; font-weight: bold; font-size: 16px; letter-spacing: 1px; }
  header .asof { color: #666; margin-left: 10px; font-size: 11px; }
  header .sub { color: #888; font-size: 10px; letter-spacing: 1px; margin-top: 2px; }
  header .right { display: flex; gap: 10px; align-items: center; font-size: 10px; color: #888; }
  button.accent { background: #e8820c; color: #111; border: none; font-family: inherit;
                  font-weight: bold; font-size: 11px; padding: 5px 12px; cursor: pointer;
                  letter-spacing: 1px; }
  button.accent:hover { background: #f79a2e; }
  main { background: #fff; min-height: calc(100vh - 60px); padding: 14px 22px;
         border-top: 2px solid #e8820c; }
  .controls { display: flex; gap: 14px; align-items: flex-end; padding-bottom: 14px;
              border-bottom: 1px solid #d8892b; margin-bottom: 14px; flex-wrap: wrap; }
  .field label { display: block; font-size: 9px; letter-spacing: 1px; color: #666;
                 margin-bottom: 3px; }
  .field input, .field select { font-family: inherit; font-size: 12px; padding: 5px 7px;
                                border: 1px solid #999; width: 110px; }
  .field input.narrow { width: 60px; }
  #status { color: #555; font-size: 11px; padding-bottom: 6px; }
  #status.err { color: #b32d2d; }
  .cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 16px; }
  .card { border: 1px solid #ccc; border-top: 2px solid #d8892b; padding: 8px 12px; }
  .card .label { font-size: 9px; letter-spacing: 1px; color: #666; margin-bottom: 4px; }
  .card .value { font-size: 19px; font-weight: bold; }
  .card .note { font-size: 10px; color: #666; margin-top: 3px; }
  .pos { color: #1a7a2e !important; } .neg { color: #b32d2d !important; }
  .panel { border: 1px solid #ccc; padding: 10px 12px; margin-bottom: 14px; }
  .panel h3 { font-size: 11px; letter-spacing: 1px; margin-bottom: 6px; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .chartbox { position: relative; height: 300px; } .chartbox.small { height: 200px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: right; font-size: 9px; letter-spacing: 1px; color: #666;
       border-bottom: 1px solid #999; padding: 4px 8px; }
  th:first-child, td:first-child { text-align: left; }
  td { text-align: right; padding: 4px 8px; border-bottom: 1px solid #eee; }
  tr:nth-child(even) td { background: #f7f6f4; }
  tr.current td { background: #f6e8d0; border-top: 2px solid #333; border-bottom: 2px solid #333;
                  font-weight: bold; }
</style>
</head>
<body>
<header>
  <div>
    <span class="title">MARKET REGIME</span><span class="asof" id="hdr-asof"></span>
    <div class="sub">POST-MARKET RECAP DASHBOARD</div>
  </div>
  <div class="right">
    <span id="hdr-updated"></span>
    <button class="accent" onclick="load()">REFRESH</button>
  </div>
</header>
<main>
  <div id="tab-technicals">
    <div class="controls">
      <div class="field"><label>TICKER</label><input id="in-ticker" value="TSLA"></div>
      <div class="field"><label>MA PERIODS</label><input id="in-ma" value="20,50,200"></div>
      <div class="field"><label>SLOPE WINDOW (D)</label><input id="in-slope" class="narrow" value="5"></div>
      <div class="field"><label>PERIOD</label>
        <select id="in-period">
          <option>6M</option><option selected>1Y</option><option>2Y</option>
          <option>5Y</option><option>MAX</option>
        </select>
      </div>
      <button class="accent" onclick="load()">LOAD</button>
      <div id="status"></div>
    </div>
    <div class="cards" id="cards" style="display:none">
      <div class="card"><div class="label">PRICE</div>
        <div class="value" id="c-price"></div><div class="note" id="c-price-note"></div></div>
      <div class="card"><div class="label" id="c-sma-label">200 DMA / DISTANCE</div>
        <div class="value" id="c-sma"></div><div class="note" id="c-dist"></div></div>
      <div class="card"><div class="label">Z-SCORE</div>
        <div class="value" id="c-z"></div><div class="note" id="c-z-note"></div></div>
      <div class="card"><div class="label" id="c-slope-label">200 DMA SLOPE (ANN.)</div>
        <div class="value" id="c-slope"></div><div class="note" id="c-slope-note"></div></div>
      <div class="card"><div class="label">Z-SCORE BAND</div>
        <div class="value" id="c-band"></div></div>
    </div>
    <div class="panel" id="p-price" style="display:none">
      <h3 id="t-price"></h3><div class="chartbox"><canvas id="ch-price"></canvas></div>
    </div>
    <div class="row2" id="p-row2" style="display:none">
      <div class="panel"><h3>SMA SLOPE (% PER DAY)</h3>
        <div class="chartbox small"><canvas id="ch-slope"></canvas></div></div>
      <div class="panel"><h3 id="t-z"></h3>
        <div class="chartbox small"><canvas id="ch-z"></canvas></div></div>
    </div>
    <div class="panel" id="p-table" style="display:none">
      <h3 id="t-table"></h3>
      <table>
        <thead><tr><th>Z-SCORE BAND</th><th>COUNT</th><th>MEAN RETURN %</th>
          <th>MEDIAN RETURN %</th><th>WIN RATE %</th></tr></thead>
        <tbody id="stats-body"></tbody>
      </table>
    </div>
  </div>
</main>
<script>
const SMA_COLORS = ["#d8892b", "#4a6fa5", "#3a7d44", "#8a5fa0"];
let charts = {};

function sign(v, digits = 2, suffix = "") {
  return (v >= 0 ? "+" : "") + v.toFixed(digits) + suffix;
}
function signClass(v) { return v >= 0 ? "pos" : "neg"; }

function mkChart(id, cfg) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), cfg);
}

const baseOpts = {
  responsive: true, maintainAspectRatio: false, animation: false,
  interaction: { mode: "index", intersect: false },
  scales: {
    x: { ticks: { maxTicksLimit: 10, maxRotation: 0, font: { size: 10, family: "monospace" } },
         grid: { color: "#eee" } },
    y: { ticks: { font: { size: 10, family: "monospace" } }, grid: { color: "#eee" } },
  },
  plugins: { legend: { position: "bottom",
    labels: { boxWidth: 10, font: { size: 10, family: "monospace" } } } },
};
const clone = o => JSON.parse(JSON.stringify(o));

async function load() {
  const status = document.getElementById("status");
  status.className = ""; status.textContent = "Loading…";
  const q = new URLSearchParams({
    ticker: document.getElementById("in-ticker").value.trim(),
    ma: document.getElementById("in-ma").value.trim(),
    slope_window: document.getElementById("in-slope").value.trim(),
    period: document.getElementById("in-period").value,
  });
  let d;
  try {
    const r = await fetch("/api/technicals?" + q);
    d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
  } catch (e) {
    status.className = "err"; status.textContent = "ERROR: " + e.message;
    return;
  }
  status.textContent = `Loaded ${d.ticker} as of ${d.as_of}.`;
  render(d);
}

function render(d) {
  const c = d.cards, L = d.long_period;
  document.getElementById("hdr-asof").textContent = "AS OF " + d.as_of;
  document.getElementById("hdr-updated").textContent =
    "Last updated " + new Date().toISOString().slice(0, 19);
  ["cards", "p-price", "p-row2", "p-table"].forEach(
    id => document.getElementById(id).style.display = "");

  document.getElementById("c-price").textContent = c.price.toFixed(2);
  document.getElementById("c-price-note").textContent = "AS OF " + d.as_of;
  document.getElementById("c-sma-label").textContent = L + " DMA / DISTANCE";
  document.getElementById("c-sma").textContent = c.long_sma.toFixed(2);
  const dist = document.getElementById("c-dist");
  dist.textContent = sign(c.distance_pct, 2, "%"); dist.className = "note " + signClass(c.distance_pct);
  document.getElementById("c-z").textContent = c.zscore.toFixed(2);
  document.getElementById("c-z-note").textContent = c.zscore_label;
  document.getElementById("c-slope-label").textContent = L + " DMA SLOPE (ANN.)";
  const slope = document.getElementById("c-slope");
  slope.textContent = sign(c.slope_ann_pct, 1, "%"); slope.className = "value " + signClass(c.slope_ann_pct);
  const slopeNote = document.getElementById("c-slope-note");
  slopeNote.textContent = c.slope_label;
  slopeNote.className = "note " + (c.slope_label.includes("UP") ? "pos" :
    c.slope_label.includes("DOWN") ? "neg" : "");
  document.getElementById("c-band").textContent = c.zscore_band;

  // price chart: band fill + close + SMAs
  document.getElementById("t-price").textContent =
    `PRICE, MOVING AVERAGES & ${d.band_sigma}σ BAND (${L} DMA)`;
  const ch = d.chart;
  const priceSets = [
    { label: `-${d.band_sigma}σ band`, data: ch.lower, borderWidth: 0,
      pointRadius: 0, fill: false },
    { label: `+${d.band_sigma}σ`, data: ch.upper, borderWidth: 0, pointRadius: 0,
      fill: "-1", backgroundColor: "rgba(216,137,43,0.15)" },
    { label: `${d.ticker} Close`, data: ch.close, borderColor: "#222",
      borderWidth: 1.4, pointRadius: 0 },
  ];
  Object.keys(ch.smas).forEach((p, i) => priceSets.push({
    label: p + "-day SMA", data: ch.smas[p],
    borderColor: SMA_COLORS[i % SMA_COLORS.length], borderWidth: 1.2, pointRadius: 0,
  }));
  const priceOpts = clone(baseOpts);
  priceOpts.scales.y.min = ch.yrange[0];
  priceOpts.scales.y.max = ch.yrange[1];
  mkChart("ch-price", { type: "line",
    data: { labels: ch.dates, datasets: priceSets }, options: priceOpts });

  // slope chart
  const slopeSets = Object.keys(ch.slopes).map((p, i) => ({
    label: p + "-day SMA slope", data: ch.slopes[p],
    borderColor: SMA_COLORS[i % SMA_COLORS.length], borderWidth: 1.2, pointRadius: 0,
  }));
  slopeSets.push({ label: "0", data: ch.dates.map(() => 0), borderColor: "#999",
    borderWidth: 1, borderDash: [4, 4], pointRadius: 0 });
  mkChart("ch-slope", { type: "line",
    data: { labels: ch.dates, datasets: slopeSets }, options: clone(baseOpts) });

  // z-score bars with dashed +/-2 guides
  document.getElementById("t-z").textContent = `Z-SCORE VS ${L} DMA`;
  const zOpts = clone(baseOpts);
  zOpts.plugins.legend.display = false;
  mkChart("ch-z", { type: "bar",
    data: { labels: ch.dates, datasets: [
      { data: ch.zscore, backgroundColor: ch.zscore.map(
          v => v >= 0.5 ? "rgba(74,145,120,0.85)"
             : v <= -0.5 ? "rgba(179,45,45,0.85)" : "rgba(150,150,150,0.7)"),
        barPercentage: 1, categoryPercentage: 1 },
      { type: "line", data: ch.dates.map(() => d.band_sigma), borderColor: "#999",
        borderWidth: 1, borderDash: [4, 4], pointRadius: 0 },
      { type: "line", data: ch.dates.map(() => -d.band_sigma), borderColor: "#999",
        borderWidth: 1, borderDash: [4, 4], pointRadius: 0 },
    ]}, options: zOpts });

  // stats table
  const s = d.stats;
  document.getElementById("t-table").textContent =
    `${d.fwd_days}-DAY FORWARD RETURN BY Z-SCORE BAND ` +
    `(${s.start} → ${s.end}, ${s.sessions} sessions)`;
  const body = document.getElementById("stats-body");
  body.innerHTML = "";
  s.rows.forEach(r => {
    const tr = document.createElement("tr");
    if (r.band === s.current_band) tr.className = "current";
    const cell = (txt, cls) => {
      const td = document.createElement("td");
      td.textContent = txt; if (cls) td.className = cls; tr.appendChild(td);
    };
    cell(r.band); cell(r.count);
    if (r.count === 0) { cell("—"); cell("—"); cell("—"); }
    else {
      cell(sign(r.mean), signClass(r.mean));
      cell(sign(r.median), signClass(r.median));
      cell(r.win_rate.toFixed(1));
    }
    body.appendChild(tr);
  });
}

load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if url.path == "/api/technicals":
            q = parse_qs(url.query)
            try:
                ticker = q.get("ticker", ["TSLA"])[0].strip() or "TSLA"
                ma = [int(x) for x in q.get("ma", ["20,50,200"])[0].split(",") if x.strip()]
                if not ma:
                    raise ValueError("at least one MA period is required")
                slope_window = max(1, int(q.get("slope_window", ["5"])[0]))
                period = q.get("period", ["1Y"])[0]
                result = compute(ticker, ma, slope_window, period)
                return self._send(200, json.dumps(result), "application/json")
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}), "application/json")
        self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, fmt, *args):
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {fmt % args}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8300)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Market Regime technicals dashboard: http://localhost:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
