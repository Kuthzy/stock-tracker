"""Plug-in registry. Every feature the bot can switch on (news sources, price sources,
score signals, alert channels, Telegram commands) registers itself here by name.

To add a feature, drop a .py file into the top-level `plugins/` folder that uses
`@register(...)`, then switch it on by name in config.yaml. See docs/ADDING_FEATURES.md.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

KINDS = ("news", "prices", "signals", "channels", "commands")
REGISTRY: dict[str, dict[str, object]] = {k: {} for k in KINDS}

# Modules that hold the built-in features; importing them runs their @register calls.
BUILTIN_MODULES = [
    "tracker.adapters.rss_news",
    "tracker.adapters.finnhub_news",
    "tracker.adapters.sec_edgar",
    "tracker.adapters.yfinance_prices",
    "tracker.signals.scoring",
    "tracker.alerts.channels",
    "tracker.alerts.telegram_commands",
]

_loaded_dirs: set[str] = set()


def register(kind: str, name: str):
    """Decorator: @register("signals", "my_signal") on a class (or a function for commands)."""
    if kind not in KINDS:
        raise ValueError(f"unknown plug-in kind {kind!r}; use one of {KINDS}")

    def wrap(obj):
        if name in REGISTRY[kind] and REGISTRY[kind][name] is not obj:
            log.info("plug-in %s/%s replaced by %s", kind, name, getattr(obj, "__module__", obj))
        REGISTRY[kind][name] = obj
        if isinstance(obj, type):
            obj.plugin_name = name
        return obj

    return wrap


def get(kind: str, name: str):
    load_builtins()
    return REGISTRY[kind].get(name)


def available(kind: str) -> list[str]:
    load_builtins()
    return sorted(REGISTRY[kind])


def load_builtins() -> None:
    for mod in BUILTIN_MODULES:
        if mod not in sys.modules:
            importlib.import_module(mod)


def load_plugin_dir(folder: Path) -> list[str]:
    """Import every .py file in `folder` (files starting with '_' are skipped).
    A broken plug-in is logged and skipped so it can never stop the bot."""
    load_builtins()
    folder = Path(folder)
    key = str(folder.resolve())
    if key in _loaded_dirs or not folder.is_dir():
        return []
    _loaded_dirs.add(key)
    loaded = []
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        mod_name = f"tracker_user_plugins.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            loaded.append(path.name)
        except Exception:
            sys.modules.pop(mod_name, None)
            log.exception("plug-in %s failed to load and was skipped", path.name)
    if loaded:
        log.info("plug-ins loaded: %s", ", ".join(loaded))
    return loaded


def build(kind: str, names: list[str], cfg) -> list:
    """Create the enabled features of one kind, in the order listed in config.
    A class may define `from_config(cfg)` returning None when it isn't set up (e.g. no API key)."""
    out = []
    for name in names:
        cls = get(kind, name)
        if cls is None:
            log.warning("config names %s %r but no such plug-in exists (available: %s)",
                        kind, name, ", ".join(available(kind)))
            continue
        try:
            obj = cls.from_config(cfg) if hasattr(cls, "from_config") else cls()
        except Exception:
            log.exception("could not start %s %r", kind, name)
            continue
        if obj is not None:
            out.append(obj)
    return out
