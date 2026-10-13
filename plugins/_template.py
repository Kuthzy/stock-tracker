"""Copy this file to a new name WITHOUT the leading underscore (files starting with "_"
are ignored), keep the part you need, and delete the rest. Full guide: docs/ADDING_FEATURES.md
"""

from tracker.adapters.base import NewsAdapter
from tracker.alerts.channels import Channel
from tracker.models import NewsItem, utcnow
from tracker.plugins import register
from tracker.signals.scoring import Signal


# 1) A new part of the Move Score. Turn on with  my_signal: 10  under scoring.signals.
@register("signals", "my_signal")
class MySignal(Signal):
    def compute(self, ctx):
        # ctx.ticker, ctx.snapshot (price data or None), ctx.events (news), ctx.now ...
        if ctx.snapshot and ctx.snapshot.pct_change > 3:
            return self.max_points, ["up more than 3%"]
        return 0.0, []


# 2) A new news source. Turn on by adding "my_news" to sources.news.
@register("news", "my_news")
class MyNews(NewsAdapter):
    name = "my_news"

    def fetch(self, tickers, themes):
        return [NewsItem(title="Example headline", url="https://example.com", source="Example",
                         published=utcnow())]


# 3) A new alert channel. Turn on by adding "my_channel" to alerts.channels.
@register("channels", "my_channel")
class MyChannel(Channel):
    name = "my_channel"

    @classmethod
    def from_config(cls, cfg):
        return cls()  # return None if a needed setting (cfg.env("MY_KEY")) is missing

    def send(self, text):
        print("MY CHANNEL:", text)
        return True


# 4) A new Telegram command. On as soon as the file exists.
@register("commands", "/hello")
def cmd_hello(ctx):
    """/hello - say hi"""
    return f"Hi! I'm watching {len(ctx.universe())} tickers."
