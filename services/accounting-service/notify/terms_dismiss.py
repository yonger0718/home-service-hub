"""The terms dismiss list (#287): hide the verified findings and announced blocks the owner accepted.

Runs on the joined result, before the digest and the show rules. Matching is the notifier's own: Hermes never sees
the list. A pin compares the leading token of the model's site text; text without that token matches no pin, so the
finding stays visible."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from notify.terms_config import (DATE_FIELDS, NO_VALUE, NUMBER_FIELDS, TOKEN_FIELDS, CardEntry, Dismissal,
                                 format_number)
from notify.terms_parse import CardResult, Finding

EXPIRED = "expired"  # `until` is past, or the announced period a next_terms entry names has ended
RULE_GONE = "rule"  # the rule a rule-field entry names is not among the card's live rules of this run

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})(?!\d)")
_NUMBER = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)(?![0-9\-/])")
_BOOL = re.compile(r"(true|false)(?![A-Za-z])", re.IGNORECASE)
_TOKEN = re.compile(r"([a-z_]+)(?![A-Za-z0-9])")


@dataclass(frozen=True)
class Applied:
    card: CardResult  # what is shown and digested
    hidden: tuple[Finding, ...] = ()
    hidden_next_terms: bool = False
    stale: tuple[tuple[Dismissal, str], ...] = ()

    @property
    def hidden_count(self) -> int:
        return len(self.hidden) + int(self.hidden_next_terms)


def site_key(field: str, site: str | None) -> str | None:
    """The comparable form of a site value for ``field``: its leading token, ``none`` for a missing value, and None
    when the text has no such token (or the field takes no pin)."""
    text = (site or "").strip()
    if field in DATE_FIELDS:
        if not text:
            return NO_VALUE
        m = _DATE.match(text)
        try:
            return date.fromisoformat(m.group(1)).isoformat() if m else None
        except ValueError:
            return None
    if field in NUMBER_FIELDS:
        if not text:
            return NO_VALUE
        m = _NUMBER.match(text)
        return format_number(Decimal(m.group(1).replace(",", ""))) if m else None
    if field == "is_basic":
        m = _BOOL.match(text)
        return m.group(1).lower() if m else None
    if field in TOKEN_FIELDS:
        m = _TOKEN.match(text)
        return m.group(1) if m else None
    return None


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _stale_reason(d: Dismissal, live_rule_ids: frozenset[int], today: date) -> str | None:
    if d.until is not None and today > d.until:
        return EXPIRED
    if d.field == "next_terms" and today > date.fromisoformat(d.value):
        return EXPIRED
    if d.rule is not None and d.rule not in live_rule_ids:
        return RULE_GONE
    return None


def _hides(d: Dismissal, f: Finding) -> bool:
    if d.field == "missing_rule":
        if f.field != "missing_rule":
            return False
        hay = _fold(f"{f.site or ''} {f.quote}")
        return all(_fold(s) in hay for s in d.contains)
    if d.field != f.field or d.rule != f.rule_id:
        return False
    return d.value is None or site_key(f.field, f.site) == d.value


def apply_dismissals(card: CardResult, entry: CardEntry, live_rule_ids: frozenset[int], today: date) -> Applied:
    """Filter one card. A card nothing was hidden on is returned as the same object (its status untouched)."""
    if not entry.dismiss:
        return Applied(card)
    active: list[Dismissal] = []
    stale: list[tuple[Dismissal, str]] = []
    for d in entry.dismiss:
        reason = _stale_reason(d, live_rule_ids, today)
        if reason is None:
            active.append(d)
        else:
            stale.append((d, reason))
    rule_entries = [d for d in active if d.field != "next_terms"]
    flags = [any(_hides(d, f) for d in rule_entries) for f in card.findings]
    nt = card.next_terms
    hide_nt = bool(nt and nt.announced and any(
        d.field == "next_terms" and nt.period[1] == d.value for d in active))
    if not any(flags) and not hide_nt:
        return Applied(card, stale=tuple(stale))
    kept = tuple(f for f, hide in zip(card.findings, flags) if not hide)
    next_terms = None if hide_nt else nt
    status = "changed" if kept else ("announced" if next_terms and next_terms.announced else "unchanged")
    return Applied(
        card=replace(card, findings=kept, next_terms=next_terms, status=status),
        hidden=tuple(f for f, hide in zip(card.findings, flags) if hide),
        hidden_next_terms=hide_nt,
        stale=tuple(stale),
    )
