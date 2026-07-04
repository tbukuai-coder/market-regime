# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workspace

This folder lives inside a Lightning AI studio home directory (`/teamspace/studios/this_studio`), not a git repository. The project is the "Market Regime" post-market technicals dashboard, available in two frontends that share one compute layer:

- `app.py` — Streamlit UI (the primary way to run it)
- `technicals_dashboard.py` — the original zero-dependency version (stdlib HTTP server + embedded Chart.js page), which also owns all the data/compute code that `app.py` imports

## Commands

```bash
pip install -r requirements.txt

# Primary: Streamlit app (open via the studio's forwarded port)
streamlit run app.py --server.headless true --server.port 8501

# Alternative: stdlib version, also useful as a JSON API
python3 technicals_dashboard.py --port 8300
curl "http://localhost:8300/api/technicals?ticker=TSLA&ma=20,50,200&slope_window=5&period=1Y"
```

There is no build step, linter, or test suite. To verify frontend changes, render the page headlessly with Playwright (already installed with Chromium) and screenshot it, checking for `pageerror` events. Streamlit takes several seconds to hydrate — wait ~6s before screenshotting.

## Architecture

All data and math live in `technicals_dashboard.py`; `app.py` only renders.

- **Data**: `fetch_history()` pulls max-history adjusted closes from yfinance, cached as pickles in `/tmp/market_regime_cache/` with a 1-hour TTL. Delete that directory to force a refetch. Stooq was tried as a keyless alternative but is blocked by a JavaScript challenge — don't switch back to it.
- **Compute**: `compute(ticker, ma_periods, slope_window, period)` returns a plain JSON-safe dict: SMAs, z-score = (close − long SMA) / `SIGMA_WINDOW`-day rolling std of that deviation (short window on purpose — it was reverse-engineered from the reference screenshot by pixel-measuring the band, and a long window makes the band far too wide), the ±`BAND_SIGMA` band, slope in %/day over `slope_window` (annualized for the header card), and `FWD_DAYS`-forward-return stats grouped by `Z_BANDS`. The stats table always uses full history; `period` only slices the chart series. The main chart's `yrange` is computed from price/SMAs only so the band clips instead of stretching the y-axis.
- **Frontends**: `app.py` styles Streamlit with a large injected CSS block (targets `data-testid`/`data-baseweb` selectors, so Streamlit upgrades can break styling) and wraps `compute` in `st.cache_data(ttl=3600)` — cache keys require hashable args, hence the `tuple` of MA periods. The stdlib version serves the `PAGE` string (complete HTML/JS using Chart.js from CDN) plus `/api/technicals`.

Tunable behavior is concentrated in the module-level constants of `technicals_dashboard.py` (`FWD_DAYS`, `BAND_SIGMA`, `Z_BANDS`, `PERIOD_DAYS`) and the label thresholds in `slope_label()` / `zscore_label()`.

The longest MA period given is treated as "the" long SMA everywhere (z-score, band, annualized slope card), so changing MA periods changes those definitions too.

The UI was originally built against a reference screenshot (since removed from the repo). Deliberate deviations from it: the tab menu (MARKETS, FIXED INCOME, etc.) was removed — the app is technicals-only; don't reintroduce it — and the dark header bar is now white.
