"""Example news-source plug-in: newest posts from stock subreddits via Reddit's public RSS.
Off by default. Switch it on by adding "reddit" to sources.news in config.yaml:
    news: [rss, finnhub, sec_edgar, reddit]
"""

from tracker.adapters import http
from tracker.adapters.base import NewsAdapter
from tracker.adapters.rss_news import parse_feed
from tracker.plugins import register

SUBREDDITS = ["wallstreetbets", "stocks", "investing"]


@register("news", "reddit")
class RedditNews(NewsAdapter):
    name = "reddit"

    def fetch(self, tickers, themes):
        items = []
        for sub in SUBREDDITS:
            body = http.get(f"https://www.reddit.com/r/{sub}/new/.rss?limit=50", retries=2)
            if body:
                for it in parse_feed(body, f"r/{sub}", max_age_hours=12):
                    it.summary = ""  # post bodies are noisy; keep the title only
                    items.append(it)
        return items
