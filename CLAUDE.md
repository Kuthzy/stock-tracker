# Stock Tracker Bot: notes for Claude

A 24/7 news-driven watchlist and alert bot (Python 3.11+). It never places trades. Every alert and summary carries the disclaimer in `tracker/__init__.py`.

## Run and check
- `python -m pytest -q`: all tests must pass after every change. Add a test for every new feature in `tests/`.
- `python -m tracker demo`: offline run on sample data. It must keep working with no network.
- `python -m tracker plugins`: lists the registered features.
- `python -m tracker once` / `run` / `tick`: one cycle, the 24/7 loop, and the single pass used by the GitHub Actions cloud mode.

## Architecture (one cycle = `tracker/engine.py: Engine.cycle`)
1. **News sources** (`kind="news"`, `NewsAdapter.fetch`) produce `NewsItem`s.
2. `signals/mapping.py` maps text to tickers and themes. `signals/catalysts.py` assigns a rule-based catalyst. `signals/sentiment.py` adds VADER sentiment, and an optional `ClaudeClassifier` (batched, structured output) refines both.
3. `Engine.group` merges the same story from several outlets into one `NewsEvent` (`source_count`) and marks rehashed stories with low novelty.
4. The **price source** (`kind="prices"`) produces a `PriceSnapshot` per ticker.
5. **Signals** (`kind="signals"`, `Signal.compute(ctx) -> (points, reasons)`) are summed and rescaled to 0–100 in `signals/scoring.py: score_ticker`.
6. `alerts/manager.py` applies tiers, cooldowns, daily caps, quiet hours and mute. **Channels** (`kind="channels"`) deliver the alerts.
7. `storage/db.py` (SQLite) keeps everything. `outcomes.py` records prices 1h/1d/5d after each alert.

## Adding features: use the plug-in system, not core edits
- Register with `@register(kind, name)` from `tracker/plugins.py`. User plug-ins go in `plugins/` (auto-loaded; files starting with `_` are skipped). Built-ins are listed in `plugins.BUILTIN_MODULES`.
- Each feature is switched on by name in `config.yaml`:
  - news sources: `sources.news`
  - the price source: `sources.prices`
  - signals: `scoring.signals` (name → max points)
  - channels: `alerts.channels`
  - commands: always on
- News, price and channel classes may define `from_config(cfg)`, returning `None` when the feature isn't configured (for example, a missing key read via `cfg.env`). Signals are always built as `cls(max_points, cfg)`. The total is rescaled to 0–100, so tell the user when a new signal re-weights the others.
- Every plug-in failure (import, construction, fetch, compute, send) is logged and isolated. Keep it that way.
- Recipes are in `docs/ADDING_FEATURES.md`. Examples are in `plugins/`.
- A new secret also needs a line in `.env.example` and in the `env:` block of `.github/workflows/tracker.yml`.

## Conventions
- Network calls go through `tracker/adapters/http.py: get`, which provides retries, backoff and a per-host circuit breaker, and returns `None` on failure. Never raise from a fetch.
- Times are stored as UTC ISO strings. Market logic uses US/Eastern (`market_hours.py`).
- Keep the scoring explainable: every point needs a human-readable reason.
- Default Claude model: `claude-opus-5-5`, set in config `llm.model`.
