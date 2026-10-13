"""Market-wide scan: today's top gainers and most active stocks (via yfinance screens)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def market_scan(screens: list[str], max_tickers: int) -> list[str]:
    try:
        import yfinance as yf
    except ImportError:
        return []
    found: dict[str, None] = {}
    for name in screens:
        try:
            result = yf.screen(name, count=max_tickers)
        except Exception as exc:
            log.warning("screen %s failed: %s", name, exc)
            continue
        for q in (result or {}).get("quotes", []):
            sym = q.get("symbol")
            if sym and "." not in sym:
                found[sym.upper()] = None
    tickers = list(found)[:max_tickers]
    log.info("market scan added: %s", ", ".join(tickers) or "nothing")
    return tickers
