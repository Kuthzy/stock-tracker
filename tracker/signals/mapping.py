"""Maps news text to tickers and themes."""

from __future__ import annotations

import re

from ..models import NewsItem

CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
EXCHANGE = re.compile(r"\((?:NYSE(?: American| Arca)?|NASDAQ|Nasdaq|AMEX|Cboe|OTC\w*|TSX)\s*:\s*([A-Z]{1,5})\)")
# Uppercase words that look like tickers but usually aren't.
NOT_TICKERS = {"CEO", "CFO", "USA", "FDA", "SEC", "ETF", "IPO", "EPS", "GDP", "CPI", "AI", "THE", "AND",
               "FOR", "NEW", "NOW", "ALL", "ONE", "CAN", "ARE", "CAT", "LOW", "BIG", "OUT", "ANY", "NYSE",
               "OPEC", "LNG", "WTI", "NATO", "DOJ", "FTC", "EU", "UK", "US", "API", "Q1", "Q2", "Q3", "Q4",
               # real tickers that are also common acronyms (Libya's NOC, ITA Airways): these only
               # map through $CASHTAGS, "(NYSE: X)" or a company-name alias
               "NOC", "ITA", "GD", "AI", "IT", "ON", "ALL", "NOW"}
# First words too common to stand in for a company on their own ("General" for General Dynamics).
GENERIC_FIRST_WORDS = {"International", "Advanced", "General", "American", "United", "First", "National",
                       "Global", "Applied", "Digital", "Energy", "Great", "Royal", "Southern", "Northern",
                       "Western", "Eastern", "New", "The", "Big", "Marathon", "Micro", "Pioneer"}
# What follows a bare ticker when it is a product name, not the company ("RTX 5090", "RTX Pro").
PRODUCT_SUFFIX = re.compile(r"\s?\d|\s(?:Pro|GPU|Super|Ti)\b")


def _word_re(words: list[str], flags=re.IGNORECASE) -> re.Pattern | None:
    words = [w for w in words if w]
    if not words:
        return None
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])", flags)


def _capitalized_match(rx: re.Pattern | None, text: str) -> bool:
    return bool(rx) and any(m.group(0)[0].isupper() for m in rx.finditer(text))


class TickerMapper:
    def __init__(self, universe: list[str], aliases: dict, themes: dict):
        self.universe = set(universe)
        # Company names match in any case ("NVIDIA", "Frontline Shares") as long as the match starts
        # with a capital, so "Chevron" maps to CVX but "chevron" doesn't.
        self.alias_res = {t: _word_re(names) for t, names in aliases.items()}
        # Inside a feed that is already about one ticker, the first word of its alias is enough
        # ("Frontline jumps" in Frontline's own Yahoo feed), unless that word is a generic one.
        self.loose_res = {t: _word_re(sorted({n.split()[0] for n in names
                                              if n and n.split()[0] not in GENERIC_FIRST_WORDS}))
                          for t, names in aliases.items()}
        bare = [t for t in universe if len(t) >= 3 and t not in NOT_TICKERS and t.isalpha()]
        self.bare_re = _word_re(bare, flags=0)
        self.theme_res = {name: _word_re(th.get("keywords", [])) for name, th in themes.items()}

    def tickers_in(self, text: str) -> set[str]:
        found = set(CASHTAG.findall(text)) | set(EXCHANGE.findall(text))
        found = {t for t in found if t in self.universe}
        if self.bare_re:
            for m in self.bare_re.finditer(text):
                if PRODUCT_SUFFIX.match(text, m.end()):
                    continue  # "RTX 5090" is a graphics card, not Raytheon
                found.add(m.group(0))
        for ticker, rx in self.alias_res.items():
            if _capitalized_match(rx, text):
                found.add(ticker)
        return found

    def themes_in(self, text: str) -> list[str]:
        return [name for name, rx in self.theme_res.items() if rx and rx.search(text)]

    def apply(self, item: NewsItem) -> NewsItem:
        text = item.text
        found = self.tickers_in(text) | set(item.tickers)
        for t in item.hint_tickers:
            if _capitalized_match(self.loose_res.get(t), text):
                found.add(t)
        item.tickers = sorted(t for t in found if t in self.universe or t in item.hint_tickers)
        item.themes = self.themes_in(text)
        return item
