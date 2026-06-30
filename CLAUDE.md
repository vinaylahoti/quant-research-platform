# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Commands

All commands are run from the repo root. There is no build step — this is a pure Python research project.

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run all tests:**
```bash
python -m pytest tests/
```

**Run a single test file:**
```bash
python -m pytest tests/test_ws3_validation.py -v
```

**Run a single test by name:**
```bash
python -m pytest tests/test_ws5_execution.py::test_execute_trade_long_take_profit -v
```

**Download raw data (klines + metrics):**
```bash
python src/data/download.py
```

**Build the parquet feature store from raw files:**
```python
from src.data.featurestore import FeatureStore
FeatureStore().build()
```

**Run the break-even hurdle calculator:**
```bash
python hurdle.py
```

---

## Architecture

### The research phases (WS = workstream)

The project is organized around sequentially gated workstreams:

- **WS1** (`src/research_log/`) — append-only SQLite experiment log at `data/research_log.db`. Every validation run auto-logs via `log_experiment()`. Trial numbers drive multiple-testing deflation.
- **WS2** (`src/features/`) — versioned feature engine with content-keyed cache. `FeatureEngine.compute_feature()` hashes both the feature code version and the underlying data to avoid stale cache hits. `attach_metrics_asof()` joins OI/funding metrics with a configurable publish-lag to enforce point-in-time semantics.
- **WS3** (`src/validation/`) — purged walk-forward validation. `PurgedWalkForwardSplitter` uses **calendar days**, not sample counts, for windows — this is intentional and critical for cross-sectional datasets where samples-per-day vary. `WalkForwardValidator.run()` auto-logs results and computes deflated Sharpe ratio using the cumulative trial count.
- **WS4** (`src/universe/`) — point-in-time universe builder. `PointInTimeUniverse.as_of(date)` returns the top-N symbols by trailing 30-day quote volume as they would have been known on that date, using each symbol's actual first/last observed bar to avoid survivorship bias.
- **WS5** (`src/execution/`) — shared execution model. `execute_trade()` applies: volatility-targeted sizing, intrabar SL/TP/time-stop resolution on 1m bars, entry/exit slippage (2 bps each side). **Known debt:** exchange fees (taker ~4 bps/side) are not yet charged — only slippage. Any "live-ready" claim requires fixing this first.

### Data flow

```
data.binance.vision
    ↓ src/data/download.py  (BinanceDataDumper, "um" asset class)
data/raw/klines/futures/um/monthly/klines/{SYMBOL}/
    ↓ src/data/featurestore.py  (FeatureStore.build)
data/featurestore/symbol={SYM}/year={YYYY}/data.parquet  (zstd)
    ↓ src/data/featurestore.py  (FeatureStore.load → resampled OHLCV)
    ↓ src/features/engine.py    (FeatureEngine → versioned features + metrics join)
    ↓ src/validation/runner.py  (WalkForwardValidator)
data/research_log.db
```

### Key conventions

**Timestamps:** all timestamps are UTC throughout. The featurestore indexes by `open_time` (bar open). `FeatureEngine.load_point_in_time_bars()` shifts the index to bar close time before any feature computation — a row timestamp is when the full bar is knowable, not when it opened.

**Parquet partitioning:** `symbol={SYM}/year={YYYY}/data.parquet` — mirrors the Hive convention used by the raw downloader, so load by year range without scanning all files.

**Feature versioning:** each feature in `src/features/registry.py` has an explicit `version` string. Changing feature logic requires bumping the version; the cache key is `{name}-{version}-{data_hash}`.

**Signal-vs-control screening:** signals are screened by the gap between signal PF and a random-entry control PF, not by absolute PF/Sharpe. This eliminates cost-level sensitivity from early screening. Absolute metrics are only meaningful after WS5 costs are fully modeled.

**Deflated Sharpe:** `compute_deflated_sharpe_details()` in `src/validation/metrics.py` uses the running trial count from the research log. The decision gate (`passes_decision_gate`) requires all fold returns > 0 AND deflated Sharpe probability > 0.5.

### Universe

`config/settings.py` defines `SYMBOLS` — 30 USDT-M perpetual futures (top liquid names). Two delisted symbols are included for historical coverage: `LUNAUSDT` (data ends 2022-05) and `FTTUSDT` (data ends 2025-06). The universe is **not** a complete reconstruction of all historical symbols — survivorship bias is reduced, not eliminated.

### Research direction

`notes/RESEARCH_DIRECTION_V2.md` is the authoritative research document. It supersedes earlier signal-screening framing. Current phase: Step 1 arithmetic characterization of structural premia (funding carry only — liquidation data is unavailable from public sources). The standing rule: any candidate must pass the "name the counterparty" test AND clear real fee-inclusive costs with margin before any signal code is written.
