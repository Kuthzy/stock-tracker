"""Rule-based catalyst classification. Each rule: (name, pattern, strength 0..1, sentiment bias)."""

from __future__ import annotations

import re

from ..models import NewsItem

RULES: list[tuple[str, str, float, float]] = [
    ("bankruptcy", r"bankruptcy|chapter 11|delist(ed|ing)|going concern", 1.0, -0.8),
    ("fda_negative", r"complete response letter|\bCRL\b|clinical hold|fail(s|ed)? to meet|trial halted", 1.0, -0.8),
    ("fda_approval", r"\bFDA\b.{0,40}\b(approv|clear|grant|accept)|breakthrough therapy|PDUFA|positive (topline|phase)", 1.0, 0.6),
    ("mna", r"\bacquir(e|es|ed|ing)\b|acquisition|merger|to be acquired|buyout|takeover|tender offer|go(ing)? private", 1.0, 0.4),
    ("geopolitical_supply", r"red sea|houthi|hormuz|blockade|sanction|attack(s|ed)? on .{0,20}(tanker|ship|vessel|refinery)"
                            r"|supply disruption|export ban|embargo|seiz(e|ed|ure) .{0,15}tanker|shadow fleet|opec\+? (cut|output|quota)", 0.9, 0.0),
    ("freight_rates", r"(freight|tanker|charter|day) rates?|\bVLCC\b|suezmax|aframax|baltic (dirty|exchange)", 0.8, 0.0),
    ("guidance", r"(raise|raises|raised|lift|boost)s? .{0,20}(guidance|outlook|forecast)|(cut|cuts|lower|lowers|slash)(es|ed)? .{0,20}(guidance|outlook|forecast)", 0.85, 0.0),
    ("earnings", r"earnings|quarterly results|\bEPS\b|revenue (beat|miss|tops|falls)|(Q[1-4]|first|second|third|fourth)[- ]quarter results|beats estimates|misses estimates", 0.7, 0.0),
    ("offering", r"public offering|stock offering|at[- ]the[- ]market (offering|equity|program|sales|facility)"
                 r"|\bATM (program|offering|facility)|(?<!non-)(?<!anti-)\bdilut(ive|ion)\b|private placement"
                 r"|registered direct|convertible notes", 0.8, -0.6),
    ("short_report", r"short seller|short report|hindenburg|muddy waters|citron", 0.8, -0.6),
    ("contract", r"\bawarded\b|wins? .{0,30}contract|contract (award|win)|\$[\d.]+ ?(million|billion) (contract|order|deal)", 0.65, 0.4),
    ("partnership", r"partnership|strategic agreement|collaborat|teams up with|supply agreement", 0.5, 0.3),
    ("analyst", r"upgrade[sd]?|downgrade[sd]?|price target|initiat(es|ed) coverage|overweight|underweight", 0.5, 0.0),
    ("insider_buy", r"insider (buy|buying|purchase)|director (bought|buys)|ceo (bought|buys)", 0.5, 0.4),
    ("regulatory", r"\bSEC (probe|investigation|charges)|\bDOJ\b|antitrust|subpoena|ban(s|ned)? .{0,15}(export|sale)|tariff", 0.6, -0.2),
    ("lawsuit", r"lawsuit|class action|sued|settlement", 0.4, -0.3),
    ("unusual_activity", r"unusual (options|volume)|shares (soar|surge|jump|plunge|tumble|spike|rocket)|halted|squeeze", 0.6, 0.0),
]
COMPILED = [(n, re.compile(p, re.IGNORECASE), s, b) for n, p, s, b in RULES]

SEC_FORMS = [
    ("424B", "offering", 0.7, -0.6),
    ("S-1", "offering", 0.6, -0.5),
    ("S-3", "offering", 0.5, -0.4),
    ("SC 13D", "activist_stake", 0.7, 0.4),
    ("SCHEDULE 13D", "activist_stake", 0.7, 0.4),   # form name used since Dec 2024
    ("SC 13G", "large_holder", 0.4, 0.2),
    ("SCHEDULE 13G", "large_holder", 0.4, 0.2),
    ("8-K", "filing_8k", 0.5, 0.0),
    ("6-K", "filing_6k", 0.35, 0.0),
    ("4", "insider_filing", 0.35, 0.0),   # matched exactly (not 40-F, 424B, 485BPOS...)
]

# Catalysts whose direction depends on which side of a supply shock a sector sits.
SHOCK_CATALYSTS = {"geopolitical_supply", "freight_rates"}
CATALYST_NAMES = sorted({r[0] for r in RULES} | {f[1] for f in SEC_FORMS} | {"general"})


def classify(item: NewsItem) -> NewsItem:
    if item.form_type:
        form = item.form_type.strip().upper()
        for prefix, name, strength, bias in SEC_FORMS:
            match = form.split("/")[0] == "4" if prefix == "4" else form.startswith(prefix)
            if match:
                item.catalyst, item.catalyst_strength, item.catalyst_bias = name, strength, bias
                return item
    best = None
    for name, rx, strength, bias in COMPILED:
        if rx.search(item.text) and (best is None or strength > best[1]):
            best = (name, strength, bias)
    if best:
        item.catalyst, item.catalyst_strength, item.catalyst_bias = best
    else:
        item.catalyst, item.catalyst_strength, item.catalyst_bias = "general", 0.2, 0.0
    return item
