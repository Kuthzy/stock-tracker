from __future__ import annotations

import abc

import pandas as pd

from ..models import NewsItem, PriceSnapshot


class PriceAdapter(abc.ABC):
    name = "prices"

    @abc.abstractmethod
    def snapshots(self, tickers: list[str]) -> dict[str, PriceSnapshot]:
        """Latest snapshot per ticker. Missing tickers are simply left out."""

    @abc.abstractmethod
    def history(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        """Daily bars with columns Open, High, Low, Close, Volume, indexed by date."""


class NewsAdapter(abc.ABC):
    name = "news"

    @abc.abstractmethod
    def fetch(self, tickers: list[str], themes: dict) -> list[NewsItem]:
        """Recent news items. Must not raise on network errors; log and return []."""
