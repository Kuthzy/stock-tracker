# Adding features

The bot is built so new features are **drop-in files**. You don't need to touch the core code.

**How it works:** every feature (news source, price source, score signal, alert channel, Telegram command) registers itself under a name with `@register(kind, "name")`. The bot loads every `.py` file in `plugins/` at startup. Then `config.yaml` decides which names are switched on.

Run `python -m tracker plugins` to list every feature that can be switched on.

There are two easy ways to add something:

- **Ask Claude.** Open this folder in Claude Code and describe the feature, for example *"add a signal that gives points when a stock is near its 52-week high"*. `CLAUDE.md` tells Claude how the project fits together.
- **Do it yourself.** Copy `plugins/_template.py` to a new file name without the leading `_`, and keep the part you need.

After any change, run `python -m tracker plugins` to check your feature is listed. Then run `python -m pytest -q` and `python -m tracker demo` to check nothing broke. On Mac/Linux, activate the `.venv` first (see the README); on Windows, use `.venv\Scripts\python` in place of `python`. The tests need one extra install the first time: `pip install -r requirements-dev.txt` (Windows: `.venv\Scripts\python -m pip install -r requirements-dev.txt`). A test in `tests/test_plugins.py` also fails if any file in `plugins/` can't load.

---

## 1. A new part of the Move Score (signal)

```python
# plugins/near_high.py
from tracker.plugins import register
from tracker.signals.scoring import Signal

@register("signals", "near_high")
class NearHigh(Signal):
    def compute(self, ctx):
        s = ctx.snapshot                      # price data, or None
        if s and s.price >= 0.98 * s.high_20:
            return self.max_points, ["within 2% of 20-day high"]
        return 0.0, []
```

To switch it on, add `near_high: 10` under `scoring.signals` in `config.yaml`.

The total is always rescaled to 0–100. That means adding a signal **re-weights** the others: a ticker that gets no points from your new signal will score a little lower than before. To keep alerts as sensitive as they are now, take the new signal's points from the others so the maximums still add up to about 100. For example, change `news: 30` to `news: 25` and add `near_high: 5`.

To switch a signal off, delete its line or set it to `0`.

`ctx` gives you:

| Field | Contents |
|---|---|
| `ctx.ticker` | the ticker being scored |
| `ctx.snapshot` | price data: `price`, `prev_close`, `open`, `day_high`, `day_low`, `volume`, `avg_volume_20`, `high_20`, `low_20`, `atr_14`, `pct_change`, `gap_pct`, `rvol` |
| `ctx.events` | recent news stories, each with `title`, `tickers`, `themes`, `catalyst`, `sentiment`, `source_count`, `first_seen` |
| `ctx.now`, `ctx.themes`, `ctx.my_themes` | the current time, all configured themes, and this ticker's themes |
| `ctx.context` | context symbols such as crude futures |
| `ctx.peer_scores` | the first-pass scores of other tickers |

Return `(points, ["reason shown in the alert"])`. Points are capped at `self.max_points`.

## 2. A new news source

```python
# plugins/my_feed.py
from tracker.adapters import http
from tracker.adapters.base import NewsAdapter
from tracker.adapters.rss_news import parse_feed
from tracker.plugins import register

@register("news", "my_feed")
class MyFeed(NewsAdapter):
    name = "my_feed"
    def fetch(self, tickers, themes):
        body = http.get("https://example.com/rss")   # retries, backoff and outage protection built in
        return parse_feed(body, "Example") if body else []
```

To switch it on, add `my_feed` to `sources.news`. Headlines are mapped to tickers, classified and scored automatically.

If the source needs an API key, give it an `__init__` and a `from_config`:

```python
@register("news", "my_feed")
class MyFeed(NewsAdapter):
    name = "my_feed"

    def __init__(self, key):
        self.key = key

    @classmethod
    def from_config(cls, cfg):
        key = cfg.env("MY_FEED_KEY")     # read from .env (or a GitHub secret in cloud mode)
        return cls(key) if key else None  # None = quietly off

    def fetch(self, tickers, themes):
        body = http.get("https://example.com/api/news", params={"token": self.key})
        ...
```

Give each `NewsItem` a timezone-aware `published` time, such as `tracker.models.utcnow()` or a `datetime` with `tzinfo=timezone.utc`. Naive times are treated as UTC.

## 3. A new alert channel

See `plugins/ntfy_channel.py` for a complete example. Subclass `Channel`, implement `send(text) -> bool`, return `None` from `from_config` when its settings are missing, and add the name to `alerts.channels`.

## 4. A new Telegram command

```python
# plugins/hello.py
from tracker.plugins import register

@register("commands", "/hello")
def cmd_hello(ctx):
    """/hello - say hi"""            # this first line shows up in /help
    return f"Watching {len(ctx.universe())} tickers"
```

The command is on as soon as the file exists. `ctx.args` holds the words after the command, `ctx.ticker` is the first of those words in uppercase, and `ctx.db` is the database.

## 5. No code needed

- **A new theme:** add a block under `themes:` in `config.yaml` with keywords, tickers, optional `context_symbols`, and a `shock_direction`.
- **More tickers:** add them to `watchlist:`, or send `/watch TICKER` in Telegram.
- **More RSS feeds:** add URLs under `sources.rss.feeds`.
- **Tuning:** change the weights under `scoring.signals` and the thresholds under `alerts`.

## 6. Cloud mode (GitHub)

- If a plug-in needs a key, add it as a repository secret: **Settings → Secrets and variables → Actions → New repository secret**.
- Then add one line to the `env:` block in `.github/workflows/tracker.yml`, like `MY_FEED_KEY: ${{ secrets.MY_FEED_KEY }}`.
- Every push runs the tests automatically (`.github/workflows/tests.yml`).

## Rules that keep the bot reliable

- Never let a plug-in raise on a network error. Use `tracker.adapters.http.get`, which returns `None` on failure. Even so, a plug-in that crashes (while loading, starting up, fetching, scoring or sending) is logged and skipped, so it can't stop the bot.
- Keep `fetch()` under about a minute. Respect each site's rate limits and terms of service.
- The bot only watches and alerts. Don't add trade execution.
