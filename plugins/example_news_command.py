"""Example Telegram command plug-in: /news TICKER shows the latest stories for a ticker.
Commands are on as soon as the file is in this folder."""

from tracker.plugins import register


@register("commands", "/news")
def cmd_news(ctx):
    """/news TICKER - latest stories for a ticker"""
    if not ctx.ticker:
        return "Usage: /news TICKER"
    stories = [e for e in ctx.db.recent_events(48) if ctx.ticker in e.tickers][-5:]
    if not stories:
        return f"No stories for {ctx.ticker} in the last 48 hours."
    return "\n\n".join(f"• {e.title}\n  {e.catalyst.replace('_', ' ')}, {e.source_count} source(s)\n  {e.url}"
                       for e in reversed(stories))
