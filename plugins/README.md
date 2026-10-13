# Plug-ins: your own features

Every `.py` file in this folder is loaded when the bot starts. Files that start with `_` are skipped.
If a plug-in has an error, the bot logs it and keeps running without it.

| File | What it adds | How to switch it on |
|---|---|---|
| `ntfy_channel.py` | free phone push alerts via the ntfy app | set `NTFY_TOPIC` in `.env` |
| `example_news_command.py` | Telegram `/news TICKER` | on already |
| `example_catalyst_bonus.py` | score points for FDA / M&A / contract news | add e.g. `catalyst_bonus: 5` under `scoring.signals` and lower `news` from 30 to 25, so the total stays 100 |
| `example_reddit_news.py` | Reddit stock subreddits as a news source | add `reddit` to `sources.news` |
| `_template.py` | starter code for all four kinds of plug-in | copy it to a new name without the `_` |

Step-by-step guide: [docs/ADDING_FEATURES.md](../docs/ADDING_FEATURES.md)
