"""Headline sentiment: VADER with a finance word list, optionally upgraded by Claude."""

from __future__ import annotations

import json
import logging

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ..models import NewsItem
from .catalysts import CATALYST_NAMES

log = logging.getLogger(__name__)

FINANCE_LEXICON = {
    "beat": 2.0, "beats": 2.0, "tops": 1.5, "miss": -2.0, "misses": -2.0, "upgrade": 2.0,
    "upgraded": 2.0, "downgrade": -2.0, "downgraded": -2.0, "surge": 2.5, "surges": 2.5,
    "soar": 2.5, "soars": 2.5, "jump": 1.8, "jumps": 1.8, "rally": 2.0, "rallies": 2.0,
    "plunge": -2.5, "plunges": -2.5, "tumble": -2.2, "tumbles": -2.2, "slump": -2.0,
    "sink": -2.0, "sinks": -2.0, "record": 1.5, "approval": 2.0, "approved": 2.0,
    "bankruptcy": -3.0, "dilution": -2.0, "offering": -1.0, "raises": 1.0, "cuts": -1.2,
    "halt": -1.5, "halted": -1.5, "probe": -1.8, "lawsuit": -1.5, "bullish": 2.0,
    "bearish": -2.0, "outperform": 1.8, "underperform": -1.8, "spike": 1.5, "spikes": 1.5,
    "disruption": -1.0, "shortage": -1.0, "windfall": 2.0, "strong": 1.2, "weak": -1.2,
}

_vader: SentimentIntensityAnalyzer | None = None


def vader() -> SentimentIntensityAnalyzer:
    global _vader
    if _vader is None:
        _vader = SentimentIntensityAnalyzer()
        _vader.lexicon.update(FINANCE_LEXICON)
    return _vader


def score_sentiment(item: NewsItem) -> NewsItem:
    compound = vader().polarity_scores(item.title + ". " + item.summary[:300])["compound"]
    item.sentiment = max(-1.0, min(1.0, compound + 0.5 * item.catalyst_bias))
    return item


SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "catalyst": {"type": "string", "enum": CATALYST_NAMES},
                    "strength": {"type": "number"},
                    "sentiment": {"type": "number"},
                    "tickers": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["index", "catalyst", "strength", "sentiment", "tickers", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PROMPT = """You classify market news headlines for a stock watchlist alert tool.
For each numbered headline return:
- catalyst: the best-fitting catalyst type from the allowed list ("general" if none fits)
- strength: 0 to 1, how likely this news is to move the affected stocks sharply today
- sentiment: -1 (clearly bad for the affected stocks) to 1 (clearly good)
- tickers: US tickers directly affected (empty if unclear; do not guess)
- reason: one short plain sentence explaining the call

Watchlist tickers: {universe}

Headlines:
{headlines}"""


class ClaudeClassifier:
    """Batches headlines into one Claude request; falls back silently to the rules on any error."""

    def __init__(self, model: str, batch_size: int = 25):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.batch_size = batch_size

    def classify(self, items: list[NewsItem], universe: list[str]) -> None:
        for start in range(0, len(items), self.batch_size):
            self._batch(items[start:start + self.batch_size], universe)

    def _batch(self, batch: list[NewsItem], universe: list[str]) -> None:
        import anthropic

        headlines = "\n".join(f"{i}. [{it.source}] {it.title} — {it.summary[:200]}" for i, it in enumerate(batch))
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": PROMPT.format(
                    universe=", ".join(universe[:150]), headlines=headlines)}],
            )
        except anthropic.RateLimitError:
            log.warning("Claude rate limited; using rule-based classification this cycle")
            return
        except anthropic.APIStatusError as exc:
            log.warning("Claude API error %s; using rule-based classification", exc.status_code)
            return
        except anthropic.APIConnectionError:
            log.warning("Claude unreachable; using rule-based classification")
            return
        except Exception as exc:  # e.g. missing API key
            log.warning("Claude classification unavailable (%s); using rule-based classification", exc)
            return
        if response.stop_reason in ("refusal", "max_tokens"):
            log.warning("Claude stopped with %s; using rule-based classification", response.stop_reason)
            return
        try:
            text = next(b.text for b in response.content if b.type == "text")
            rows = json.loads(text)["items"]
        except (StopIteration, ValueError, KeyError):
            log.warning("could not parse Claude output; using rule-based classification")
            return
        for row in rows:
            idx = row.get("index")
            if not isinstance(idx, int) or not 0 <= idx < len(batch):
                continue
            item = batch[idx]
            item.catalyst = row["catalyst"]
            item.catalyst_strength = max(0.0, min(1.0, float(row["strength"])))
            item.sentiment = max(-1.0, min(1.0, float(row["sentiment"])))
            item.reason = row["reason"][:200]
            extra = {t.upper() for t in row["tickers"] if t.upper() in universe}
            item.tickers = sorted(set(item.tickers) | extra)
