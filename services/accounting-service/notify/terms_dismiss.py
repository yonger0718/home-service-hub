"""The terms dismiss list (#287): hide the verified findings and announced blocks the owner accepted.

Runs on the joined result, before the digest and the show rules. Matching is the notifier's own: Hermes never sees
the list. A pin compares the leading token of the model's site text; text without that token matches no pin, so the
finding stays visible."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

from notify.terms_config import (DATE_FIELDS, NO_VALUE, NUMBER_FIELDS, TOKEN_FIELDS, CardEntry, Dismissal,
                                 format_number, sort_rates)
from notify.terms_parse import CardResult, Finding, NextTerms

EXPIRED = "expired"  # `until` is past, or the announced period a next_terms entry names has ended
RULE_GONE = "rule"  # the rule a rule-field entry names is not among the card's live rules of this run

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})(?!\d)")
# One number, then something that cannot continue it. The lookahead holds every character a shorter prefix could
# stop in front of, so a range, a fraction or a list ("1.5-3%", "9,000-12,000", "1.5/2") gives no token at all
# instead of its first digits. Commas only as thousands separators.
_NUMBER = re.compile(r"((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(?![0-9.,\-/])")
_BOOL = re.compile(r"(true|false)(?![A-Za-z])", re.IGNORECASE)
_TOKEN = re.compile(r"([a-z_]+)(?![A-Za-z0-9_])")
_NONE = re.compile(r"(?:(?:null|none)(?![A-Za-z])|無)", re.IGNORECASE)  # the model spelling a missing value out


@dataclass(frozen=True)
class Applied:
    card: CardResult  # what is shown and digested
    hidden: tuple[Finding, ...] = ()
    hidden_next_terms: bool = False
    stale: tuple[tuple[Dismissal, str], ...] = ()
    hidden_rates: tuple[tuple[str | None, str | None], ...] = ()  # of the hidden announced block

    @property
    def hidden_count(self) -> int:
        return len(self.hidden) + int(self.hidden_next_terms)


def site_key(field: str, site: str | None) -> str | None:
    """The comparable form of a site value for ``field``: its leading token, ``none`` for a missing value, and None
    when the text has no such token (or the field takes no pin)."""
    text = (site or "").strip()
    if (field in DATE_FIELDS or field in NUMBER_FIELDS) and (not text or _NONE.match(text)):
        return NO_VALUE
    if field in DATE_FIELDS:
        m = _DATE.match(text)
        try:
            return date.fromisoformat(m.group(1)).isoformat() if m else None
        except ValueError:
            return None
    if field in NUMBER_FIELDS:
        m = _NUMBER.match(text)
        return format_number(Decimal(m.group(1).replace(",", ""))) if m else None
    if field == "is_basic":
        m = _BOOL.match(text)
        return m.group(1).lower() if m else None
    if field in TOKEN_FIELDS:
        m = _TOKEN.match(text)
        return m.group(1) if m else None
    return None


def _number(value: object) -> str | None:
    if value is None:
        return None
    try:
        return format_number(Decimal(str(value)))
    except InvalidOperation:
        return str(value)


def terms_rates(nt: NextTerms) -> tuple[tuple[str | None, str | None], ...]:
    """The announced rows as sorted (rate, cap) pairs: the part of an announced block that is numbers, which the
    model writes the same way from run to run (the row names and notes it words afresh each time)."""
    return sort_rates((_number(r.get("rate")), _number(r.get("cap"))) for r in nt.rules)


def _hides_terms(d: Dismissal, nt: NextTerms) -> bool:
    return (d.field == "next_terms" and nt.period[1] == d.value
            and (d.rates is None or terms_rates(nt) == d.rates))


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
    hide_nt = bool(nt and nt.announced and any(_hides_terms(d, nt) for d in active))
    if not any(flags) and not hide_nt:
        return Applied(card, stale=tuple(stale))
    kept = tuple(f for f, hide in zip(card.findings, flags) if not hide)
    next_terms = None if hide_nt else nt
    # `not_found`, and `changed` with no verified finding, are the every-run 「找不到官網來源」 line (base spec §5):
    # hiding an accepted item must not turn that into "no difference".
    if card.status == "not_found" or (card.status == "changed" and not card.findings):
        status = card.status
    else:
        status = "changed" if kept else ("announced" if next_terms and next_terms.announced else "unchanged")
    return Applied(
        card=replace(card, findings=kept, next_terms=next_terms, status=status),
        hidden=tuple(f for f, hide in zip(card.findings, flags) if hide),
        hidden_next_terms=hide_nt,
        stale=tuple(stale),
        hidden_rates=terms_rates(nt) if hide_nt else (),
    )
