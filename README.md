# Stock Tracker Bot

A 24/7 watchlist and alert bot. It reads market news, SEC filings and price data around the clock, gives every ticker a **Move Score from 0 to 100**, and sends you an alert when a stock shows a news catalyst plus unusual price or volume action.

Example it is built to catch: **BWET** (the tanker freight ETF) jumping after a Red Sea or Strait of Hormuz attack. The chain is *news → theme (tanker shipping) → related tickers (BWET, FRO, DHT, STNG...) → check price, volume and crude oil → alert*.

> **Disclaimer.** This is an informational research tool, not financial advice. Alerts are signals to look closer, not predictions or recommendations. News-driven moves are fast and often reverse. Leveraged or thinly traded products such as freight and commodity ETFs can lose value quickly. Free data is delayed about 15 minutes and can be wrong. The bot never places trades. Never risk money you can't afford to lose.

---

## 0. Run it in the free cloud (works with your computer off)

The bot can run on GitHub's free servers. It checks every 20 minutes in market hours, hourly before and after, and a few times a night and on weekends.

1. **Put this folder in a private GitHub repository.**
   - Easiest: install [GitHub Desktop](https://desktop.github.com/). Choose **File → Add Local Repository**, pick this folder, create the repository, and press **Publish** with **Keep this code private** ticked.
   - Or in a terminal: `git init && git add . && git commit -m "first version" && git branch -M main && git remote add origin <your repo URL> && git push -u origin main`
   - Don't use the web page's "upload files" button: it drops the hidden `.github` folder that makes cloud mode work. `.env` is never uploaded (secrets go in step 2).
2. **Add your alert settings as secrets.** Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - for phone push: `NTFY_TOPIC`, or `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
   - optionally: `SEC_USER_AGENT` (your email) and `FINNHUB_API_KEY`
3. **Switch it on.** On the **Actions** tab, enable workflows. Then open **Stock tracker** and press **Run workflow** for a first check. To check that phone alerts arrive, press **Run workflow**, tick **Only send a test message to your phone**, and run it.

**Run only one copy.** Before switching cloud mode on, close any window where `run.sh` or `run.bat` is running, remove the Mac background service if you installed it (`bash scripts/mac_uninstall_service.sh`), and stop Docker if you used it (`docker compose down`). Otherwise every alert arrives twice. To run on your computer again later, disable the workflow on the Actions tab.

The bot's memory (scores, alerts sent, cooldowns) is carried between runs. After each run, an Excel report is attached to the run under **Artifacts**.

This schedule is about 830 runs a month. A private repository gets 2,000 free minutes, and each run bills 1–2 minutes, so it fits. You can check usage under **Settings → Billing**. GitHub sometimes starts scheduled runs a few minutes late.

## 1. Quick start on your own computer (5 minutes)

You need **Python 3.11 or newer** ([python.org/downloads](https://www.python.org/downloads/); on Windows tick "Add Python to PATH"; on a Mac also run **Install Certificates.command** from the Python folder in Applications).

- **Windows:** double-click `run.bat`.
- **Mac / Linux:** open Terminal in this folder and run `bash run.sh`.

The first run installs everything and starts the bot. Alerts print in that window. Leave it open and the bot keeps running. Press Control+C to stop it.

**Running other commands.** The bot lives in its own Python environment inside the folder.

- **Mac/Linux:** in each new Terminal window, run `source .venv/bin/activate` first. Then the `python -m tracker ...` commands below work as written.
- **Windows:** open Command Prompt in this folder and start each command with `.venv\Scripts\python` instead of `python`, for example `.venv\Scripts\python -m tracker demo`. (This avoids PowerShell's block on activation scripts.)

Then try it without any internet:

```
python -m tracker demo
```

This runs one cycle on **sample data** (a made-up Red Sea tanker story), so you can see what an alert looks like.

## 2. Get alerts on your phone (free)

**Easiest, ntfy:**

1. Install the free **ntfy** app and subscribe to a hard-to-guess topic name, such as `stocks-k8x2q9`.
2. Put `NTFY_TOPIC=stocks-k8x2q9` in `.env` (or add it as a GitHub secret in cloud mode).

**Telegram, which also lets you control the bot:**

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Message **@userinfobot** to get your chat id.
3. Send any message to your new bot, so it is allowed to message you.
4. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Run `python -m tracker test-alert`. You should get a message.

You can also control the bot from Telegram:

| Command | What it does |
|---|---|
| `/watch TSLA` | add a ticker |
| `/unwatch TSLA` | remove a ticker |
| `/list` | show everything being watched |
| `/score BWET` | latest Move Score and why |
| `/top` | top 10 scores right now |
| `/mute 2h` / `/unmute` | pause non-urgent alerts |
| `/news BWET` | latest stories for a ticker (from the example plug-in) |
| `/help` | list every command |

The bot only obeys messages from your own chat id. In cloud mode, commands are answered at the next scheduled check. Discord (webhook URL) and email (SMTP) work too; fill in those lines in `.env`.

## 3. Commands

| Command | What it does |
|---|---|
| `python -m tracker run` | run 24/7 |
| `python -m tracker once` | run one cycle and print the top scores |
| `python -m tracker score BWET` | latest score for one ticker |
| `python -m tracker export` | save scores, alerts and news to `data/tracker_report.xlsx` |
| `python -m tracker backtest BWET FRO --period 2y` | check whether high technical scores came before bigger moves |
| `python -m tracker test-alert` | test your alert channels |
| `python -m tracker demo` | offline demo with sample data |
| `python -m tracker plugins` | list every feature you can switch on |
| `python -m tracker tick` | one full check, for schedulers (used by cloud mode) |
| `streamlit run dashboard.py` | live web dashboard (install `requirements-dashboard.txt` first) |

## 4. How the Move Score works

Every cycle, each ticker gets points from five parts. The maximums are set under `scoring.signals` in `config.yaml`, and plug-ins can add more parts:

| Part | Max | What earns points |
|---|---|---|
| News | 30 | catalyst strength × sentiment × novelty × number of outlets, fading with a 6-hour half-life. A story naming the ticker counts fully; a story that only matches its theme counts 60%. |
| Price | 25 | % move vs. prior close, opening gap, breaking the 20-day high or low |
| Volume | 25 | relative volume (today's volume vs. the 20-day average, adjusted for time of day). 5× earns full points. |
| Volatility | 10 | day range vs. ATR(14), close outside the Bollinger bands |
| Theme | 10 | related context moving (e.g. Brent crude for tankers), or a peer in the same theme already scoring high |

**Alert tiers:** 👀 Watch at 60 or more, 🔥 Hot at 75 or more, 🚨 Urgent at 90 or more. A ticker is not alerted again within 45 minutes unless it climbs a tier, and there are daily caps. Between 22:00 and 07:00 ET only Urgent alerts go out.

Every alert says **why** it fired: the headline, the catalyst type, how many outlets carried it, and each score part.

**Catalysts detected:** FDA approvals and rejections, earnings, guidance changes, M&A, contracts, analyst calls, insider buying, offerings and dilution (negative), short reports, lawsuits, bankruptcy, geopolitical supply shocks, freight-rate moves, and SEC filings (8-K, Form 4, S-1/S-3/424B, 13D/G).

**Supply shocks flip by sector.** A headline like "missile hits tanker" reads negative, but it usually lifts tanker rates. Each theme has a `shock_direction` so the bot reads that news as bullish for tankers and oil, and bearish where disruption hurts (chips).

**Old news is discounted.** The same story from several outlets is grouped into one event, so a story on 5 sites counts as one story with 5 sources. A story that retells something from more than 12 hours ago scores much lower.

## 5. Add features (plug-ins)

New features are drop-in files in `plugins/`, switched on by name in `config.yaml`. The folder includes these:

- free phone push via the **ntfy** app
- a Telegram `/news` command
- a bonus signal for FDA, M&A and contract news
- Reddit as a news source
- a template for signals, news sources, alert channels and Telegram commands

See **[docs/ADDING_FEATURES.md](docs/ADDING_FEATURES.md)**. You can also open the folder in Claude Code and ask for the feature in plain English; `CLAUDE.md` explains the project to Claude.

## 6. Customize

Everything lives in `config.yaml`:

- `watchlist`: tickers you always want.
- `themes`: keywords, tickers and context symbols (oil futures etc.). Add your own theme in a few lines.
- `aliases`: company names, so "Frontline" maps to FRO.
- `market_scan`: adds the day's top gainers and most active stocks at 09:45 ET.
- `scoring` and `alerts`: weights and thresholds.
- `schedule`: polling speed per session when running on your own computer (cloud mode uses the times in `.github/workflows/tracker.yml`). The defaults are every 2 minutes in market hours, every 5 minutes pre-market and after hours, and every 15 minutes overnight and on weekends.

## 7. Data sources

All of these are free and need no key:

- **Prices:** Yahoo Finance via `yfinance`, about 15 minutes delayed.
- **News:** Yahoo Finance headlines per ticker, Google News per theme, PR Newswire, GlobeNewswire, MarketWatch, and CNBC RSS.
- **Filings:** SEC EDGAR. Put your email in `SEC_USER_AGENT`, because the SEC asks for a contact.

These are optional:

- **Finnhub:** extra company news. Get a free key at finnhub.io and set `FINNHUB_API_KEY`.
- **Claude AI headline reading:** set `ANTHROPIC_API_KEY`. Claude then classifies each relevant headline, scores its sentiment, picks out the affected tickers, and adds a one-line "AI read" to alerts. It batches up to 25 headlines into one request per cycle. It uses `claude-opus-5-5` by default; change `llm.model` to `claude-haiku-5-5` for a much cheaper option. If Claude is unreachable, the bot falls back to its built-in rules automatically.

To add a paid provider (Polygon, Alpaca, Benzinga), drop a file in `plugins/`:

- **Price provider:** a `PriceAdapter` subclass decorated with `@register("prices", "polygon")`. It implements `snapshots()` and `history()` and returns `None` from `from_config` when its key is missing. Then set `sources.prices: polygon`.
- **News provider:** the same idea with `@register("news", ...)`. Add its name to `sources.news`.

See `docs/ADDING_FEATURES.md`.

## 8. Run it 24/7 on a server

A home computer only works while it's on. For true 24/7, use a small cloud server: Hetzner, DigitalOcean or Lightsail run about $4–6 a month.

**With Docker (recommended):**

```
git clone <your copy> stock-tracker && cd stock-tracker
cp .env.example .env    # fill in your keys
docker compose up -d --build
docker compose logs -f  # watch it work
```

`restart: unless-stopped` brings it back after crashes and reboots. Your database, logs and exports live in `./data`.

**Without Docker:** use `deploy/stock-tracker.service` with systemd. The steps are inside the file.

**On a Mac, in the background:** move the folder to your home folder (macOS blocks background services in Desktop, Documents and Downloads), then run `bash scripts/mac_install_service.sh`. The bot then starts at login, restarts if it crashes, and keeps the Mac awake while it runs. The lid has to stay open unless the Mac is plugged in with an external display. `bash scripts/mac_uninstall_service.sh` removes it.

**Health:** `curl localhost:8080/health` shows the last cycle and data time. If no data arrives for an hour, the bot sends you a heartbeat warning, and a source that keeps failing is paused for 10 minutes then retried. After 3 failed cycles in a row, you get a message.

## 9. Logs, history and checking whether it works

- `data/tracker.db` (SQLite) stores every story, score, and alert.
- `data/logs/tracker.jsonl` holds the rotating JSON logs.
- **Outcome tracking:** the price 1 hour, 1 trading day and 5 trading days after every alert is recorded. The 16:10 ET daily summary and the Excel export show how often the alert's direction was right and the average next-day move. Use this to tune the weights in `config.yaml`.
- **Daily messages:** a pre-market brief at 08:30 ET (top scores and overnight stories), and a close summary at 16:10 ET.

## 10. Tests

```
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests They cover ticker mapping, catalysts, sentiment, story grouping, scoring, alert cooldowns and quiet hours, market hours, RSS parsing, Telegram commands, AI output parsing, and the Excel export.

## Project layout

```
tracker/
  adapters/     prices (yfinance), news (RSS, Finnhub), SEC filings, market scan
  signals/      ticker mapping, catalysts, sentiment (+ Claude), story grouping, Move Score
  alerts/       formatting, channels (console/Telegram/Discord/email), rules, Telegram commands
  storage/      SQLite
  plugins.py    plug-in registry (@register)
  engine.py     one cycle: news -> scores -> alerts
  service.py    24/7 loop, daily jobs, health check
  outcomes.py   what happened after each alert
  export.py     Excel report
  backtest.py   history replay
plugins/        your own features (drop-in files) + examples
docs/           ADDING_FEATURES.md
config.yaml     what to watch and how to score
.github/        cloud mode (tracker.yml) and automatic tests (tests.yml)
```
