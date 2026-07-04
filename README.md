# Market Regime

Post-market technicals dashboard: loads a ticker's full daily history and shows
price vs. moving averages with a ±1.5σ band, SMA slopes, a z-score vs. the
200 DMA, and 40-day forward-return statistics grouped by z-score band.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Alternative zero-dependency frontend (stdlib HTTP server + Chart.js, also a JSON API):

```bash
python3 technicals_dashboard.py --port 8300
curl "http://localhost:8300/api/technicals?ticker=TSLA&ma=20,50,200&slope_window=5&period=1Y"
```

Price data comes from yfinance, cached on disk for an hour.

## How the band and z-score are defined

- Long SMA = the largest of the configured MA periods (default 200).
- σ = 50-day rolling std of (close − long SMA); the band is long SMA ± 1.5σ.
- z-score = (close − long SMA) / σ.
- The forward-return table groups every session in history by its z-score band
  and reports mean/median 40-day forward return and win rate.
