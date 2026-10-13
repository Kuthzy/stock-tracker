"""Command line entry point: python -m tracker <command>"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import DISCLAIMER, __version__
from .config import load_config
from .logsetup import setup_logging


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji on Windows consoles
    p = argparse.ArgumentParser(prog="tracker", description="24/7 news-driven stock watchlist and alert bot")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run 24/7")
    sub.add_parser("tick", help="one full check, for schedulers like GitHub Actions or cron")
    sub.add_parser("once", help="run one cycle and print the top scores")
    s = sub.add_parser("score", help="show the latest score for a ticker")
    s.add_argument("ticker")
    e = sub.add_parser("export", help="export scores, alerts and news to Excel")
    e.add_argument("--out", default="data/tracker_report.xlsx")
    b = sub.add_parser("backtest", help="test the technical half of the score on price history")
    b.add_argument("tickers", nargs="+")
    b.add_argument("--period", default="1y")
    sub.add_parser("test-alert", help="send a test message to every configured channel")
    sub.add_parser("demo", help="offline demo with sample data")
    sub.add_parser("plugins", help="list every feature that can be switched on in config.yaml")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.path(cfg["storage"]["log_dir"]), args.log_level)
    print(f"Stock Tracker {__version__} · {DISCLAIMER}\n")

    if args.cmd == "demo":
        from .demo import run_demo

        run_demo(cfg)
        return 0

    from .builder import build_engine

    engine = build_engine(cfg)
    if args.cmd == "run":
        from .service import Service

        try:
            asyncio.run(Service(cfg, engine).run())
        except KeyboardInterrupt:
            pass
    elif args.cmd == "tick":
        from .service import run_tick

        run_tick(cfg, engine)
    elif args.cmd == "plugins":
        from . import plugins

        for kind in plugins.KINDS:
            print(f"{kind:<9} " + ", ".join(plugins.available(kind)))
    elif args.cmd == "once":
        summary = engine.cycle(with_prices=True)
        print(f"\nSession: {summary['session']} · new news items: {summary['news']} · "
              f"prices: {summary['prices']} · alerts sent: {summary['alerts']}\n")
        if not summary["news"] and not summary["prices"]:
            print("  No data came in. Check your internet connection (the bot needs Yahoo Finance,\n"
                  "  news RSS feeds and sec.gov), then try again.\n")
        for r in sorted(engine.last_results, key=lambda x: x.score, reverse=True)[:15]:
            print(f"  {r.ticker:<6} {r.score:>5.1f}  {r.direction:<6} " +
                  " ".join(f"{k}={v:.0f}" for k, v in r.components.items()))
    elif args.cmd == "score":
        row = engine.db.latest_score(args.ticker.upper())
        if not row:
            print("No score yet. Run `python -m tracker once` first.")
            return 1
        print(f"{row['ticker']}: {row['score']:.0f}/100 ({row['direction']}) as of {row['asof']}")
        print("Components:", json.loads(row["components"]))
        print("Why:", "; ".join(json.loads(row["reasons"])) or "-")
    elif args.cmd == "export":
        from .export import export_excel

        out = export_excel(engine.db, cfg.path(args.out))
        print(f"Saved {out}")
    elif args.cmd == "backtest":
        from .backtest import backtest

        table = backtest(engine.prices, [t.upper() for t in args.tickers], args.period,
                         cfg["scoring"], engine.calendar)
        print(table.to_string(index=False) if not table.empty else "No data.")
    elif args.cmd == "test-alert":
        engine.alerts.broadcast("🔔 Test alert from Stock Tracker. If you can read this, alerts work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
