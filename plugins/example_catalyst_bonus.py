"""Example signal plug-in: extra Move Score points when a ticker has big-ticket news
(FDA decisions, M&A, contract wins) in the last few hours.

Switch it on under `scoring.signals` in config.yaml, taking the points from another signal
so the total stays at 100, e.g.:
    news: 25
    catalyst_bonus: 5
News is now worth 5 points less, so tickers without a big catalyst score a little lower than
before and tickers with one score higher. That shift is the point of the plug-in.
"""

from tracker.plugins import register
from tracker.signals.scoring import Signal

BIG_CATALYSTS = {"fda_approval", "fda_negative", "mna", "contract", "activist_stake"}
WINDOW_HOURS = 6


@register("signals", "catalyst_bonus")
class CatalystBonus(Signal):
    def compute(self, ctx):
        for ev in ctx.events:
            age_h = (ctx.now - ev.first_seen).total_seconds() / 3600
            if ctx.ticker in ev.tickers and ev.catalyst in BIG_CATALYSTS and age_h <= WINDOW_HOURS:
                return self.max_points, [f"big catalyst: {ev.catalyst.replace('_', ' ')}"]
        return 0.0, []
