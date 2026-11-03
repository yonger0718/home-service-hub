"""Hermes terms verdict: last fenced JSON block, strict validation, source rule, per-card digest."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from notify.terms_config import CardEntry

STATUSES = ("unchanged", "changed", "announced", "not_found", "signal")
_BLOCK = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_NOTES_MAX = 500


class TermsParseError(Exception):
    """Unusable Hermes answer; never carries model text. The CLI sets ``archive`` before re-raising."""

    reason = "parse"

    def __init__(self, archive: Path | None = None):
        super().__init__()
        self.archive = archive

    def __str__(self) -> str:
        return f"{self.reason} {self.archive}"


@dataclass(frozen=True)
class Finding:
    rule_id: int | None
    field: str
    site: str | None
    system: str | None
    source: str
    quote: str
    reason: str = ""  # why an unverified item is unverified; "" = source rule


UNKNOWN_RULE = "未知規則"


@dataclass(frozen=True)
class PeriodEnd:
    date: str
    source: str
    quote: str


@dataclass(frozen=True)
class NextTerms:
    announced: bool
    period: tuple[str | None, str | None]
    rules: tuple[dict, ...]
    source: str | None
    quote: str | None


@dataclass(frozen=True)
class TruncatedSource:
    """A ``fetch_log.truncated`` entry for one card. ``url`` is the verified URL, or (``verified`` False) the
    cleaned raw text whose host alone may be printed."""

    url: str
    verified: bool
    read: str


@dataclass(frozen=True)
class CardResult:
    account_id: int
    status: str
    current_period_end: PeriodEnd | None
    findings: tuple[Finding, ...]
    unverified: tuple[Finding, ...]
    next_terms: NextTerms | None
    next_terms_unverified_url: str | None
    sources: tuple[str, ...]
    notes: str
    truncated: tuple[TruncatedSource, ...]


@dataclass(frozen=True)
class TermsResult:
    cards: tuple[CardResult, ...]
    fetch_log: dict


def last_json_block(text: str) -> str:
    blocks = _BLOCK.findall(text)
    if not blocks:
        raise TermsParseError()
    return blocks[-1]


def _norm_domain(d: str) -> str:
    d = d.strip().lower()
    return d[:-1] if d.endswith(".") else d


_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f\\]")
# Printable-URL rule: RFC 3986 characters minus @ ' * and everything Discord can turn into a link or mention.
_PRINTABLE = re.compile(r"[A-Za-z0-9\-._~:/?#!$&+,;=%()]+")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_URL_MAX = 2048


def _clean_url(url: str) -> bool:
    return len(url) <= _URL_MAX and not _BAD_CHARS.search(url)


def _safe_url(value: str) -> str:
    value = value.strip()
    return value if _clean_url(value) else ""


def source_ok(url: str, domains: tuple[str, ...]) -> bool:
    """https, printable URL characters only, no userinfo, no non-443 port, valid labels, host equal to / under a
    bank domain. Every URL the message prints in full passes this."""
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not _clean_url(url) or not _PRINTABLE.fullmatch(url):
        return False
    try:
        parts = urlsplit(url)
        if "%" in parts.netloc:
            return False
        scheme, user, pw = parts.scheme.lower(), parts.username, parts.password
        host, port = parts.hostname, parts.port
    except (ValueError, AttributeError):
        return False
    if scheme != "https" or user is not None or pw is not None or not host:
        return False
    if port not in (None, 443):
        return False
    host = _norm_domain(host)
    if not host or not all(_LABEL.match(lb) for lb in host.split(".")):
        return False
    for d in map(_norm_domain, domains):
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def _bad() -> TermsParseError:
    return TermsParseError()


_NUM = re.compile(r"^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$")


def _dec(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise _bad()
    text = str(value).strip()
    if len(text) > 40 or (isinstance(value, str) and not _NUM.match(text)):
        raise _bad()
    try:
        d = Decimal(text)
        if not d.is_finite() or abs(d.adjusted()) > 30:
            raise _bad()
        out = "{:f}".format(d.normalize())
    except (ArithmeticError, ValueError):
        raise _bad() from None
    return "0" if out in ("-0", "0") else out


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise _bad() from None


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _bad()
    return str(value)


def _text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _bad()
    return value


def _finding(raw: object) -> Finding:
    if not isinstance(raw, dict):
        raise _bad()
    rid = raw.get("rule_id")
    if rid is not None and (isinstance(rid, bool) or not isinstance(rid, int)):
        raise _bad()
    field = _text(raw.get("field")).strip()
    if not field:
        raise _bad()
    return Finding(rid, field, _opt_str(raw.get("site")), _opt_str(raw.get("system")),
                   _text(raw.get("source")).strip(), _text(raw.get("quote")))


def _demote(f: Finding) -> Finding:
    return Finding(f.rule_id, f.field, f.site, f.system, _safe_url(f.source), f.quote)


def _period_end(raw: object, domains: tuple[str, ...]) -> PeriodEnd | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _bad()
    d, src, quote = raw.get("date"), raw.get("source"), raw.get("quote")
    if any(v is not None and not isinstance(v, str) for v in (d, src, quote)):
        raise _bad()
    if not (d and src and quote):
        return None
    if not quote.strip() or not source_ok(src, domains):
        return None
    return PeriodEnd(_iso(d) or "", src.strip(), quote)


def _next_terms(raw: object, domains: tuple[str, ...]) -> tuple[NextTerms | None, str | None]:
    if not isinstance(raw, dict):
        raise _bad()
    announced = raw.get("announced")
    if not isinstance(announced, bool):
        raise _bad()
    period = raw.get("period", [None, None])
    if not isinstance(period, list) or len(period) != 2:
        raise _bad()
    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise _bad()
    rules = []
    for r in rules_raw:
        if not isinstance(r, dict):
            raise _bad()
        rules.append({"name": _text(r.get("name")), "rate": _dec(r.get("rate")),
                      "cap": _dec(r.get("cap")), "notes": _opt_str(r.get("notes"))})
    src, quote = raw.get("source"), raw.get("quote")
    if src is not None and not isinstance(src, str):
        raise _bad()
    if quote is not None and not isinstance(quote, str):
        raise _bad()
    nt = NextTerms(announced, (_iso(period[0]), _iso(period[1])), tuple(rules),
                   src.strip() if src else None, quote)
    if src and quote and quote.strip() and source_ok(src, domains):
        return nt, None
    return None, (_safe_url(src) or None if src and announced else None)  # an unannounced block shows nothing


_UNVERIFIED = "[unverified source]"
_FAILED_SUFFIX = re.compile(r" ?\(([0-9]{3}|[A-Za-z0-9 _-]{1,40})\)")  # fullmatch; ASCII digits only
_READ = re.compile(r"(?:[^\W\d]|[0-9 ~%,+\-]){0,80}")  # fullmatch; letters of any script, ASCII digits only


def _read_text(value: object) -> str:
    return value if isinstance(value, str) and _READ.fullmatch(value) else ""


def _clean_log(log: dict, cards: tuple[CardEntry, ...]) -> tuple[dict, dict[int, tuple[TruncatedSource, ...]]]:
    """The archived log (allowlisted keys, failing URLs replaced or dropped) and each card's truncated sources."""
    allowed = tuple(d for c in cards for d in c.bank_domains)
    by_id = {c.account_id: c.bank_domains for c in cards}
    out = {k: v for k, v in log.items() if k in ("tool_calls", "failed", "truncated")}
    calls = log.get("tool_calls")
    if calls is not None and (isinstance(calls, bool) or not isinstance(calls, int)):
        raise _bad()
    failed = log.get("failed", [])
    if not isinstance(failed, list):
        raise _bad()
    out["failed"] = [u for u in (_clean_failed(x, allowed) for x in failed) if u]
    trunc = log.get("truncated", [])
    if not isinstance(trunc, list):
        raise _bad()
    clean, per_card = [], {}
    for e in trunc:
        if not isinstance(e, dict):
            raise _bad()
        aid, url = e.get("account_id"), e.get("url")
        if isinstance(aid, bool) or not isinstance(aid, int):
            continue  # unattributable entry: dropped
        ok = aid in by_id and source_ok(url, by_id[aid])
        read = _read_text(e.get("read"))
        clean.append({"account_id": aid if aid in by_id else None,
                      "url": url.strip() if ok else _UNVERIFIED, "read": read})
        if aid in by_id:
            shown = url.strip() if ok else (_safe_url(url) if isinstance(url, str) else "")
            per_card.setdefault(aid, []).append(TruncatedSource(shown, ok, read))
    out["truncated"] = clean
    return out, {k: tuple(v) for k, v in per_card.items()}


def _clean_failed(entry: object, allowed: tuple[str, ...]) -> str:
    """The entry when its URL passes the source rule (suffix kept only when allowlisted), else "" (dropped)."""
    if not isinstance(entry, str):
        return ""
    url = entry.strip().split(" ", 1)[0]  # "https://... (403)"
    rest = entry.strip()[len(url):]
    if not source_ok(url, allowed):
        return ""
    return url + rest if _FAILED_SUFFIX.fullmatch(rest) else url


def _card(raw: object, entry: CardEntry, trunc: tuple[TruncatedSource, ...]) -> CardResult:
    if not isinstance(raw, dict):
        raise _bad()
    status = raw.get("status")
    if status not in STATUSES:
        raise _bad()
    doms = entry.bank_domains
    if status == "changed" and not isinstance(raw.get("findings"), list):
        raise _bad()
    if status == "announced" and not isinstance(raw.get("next_terms"), dict):
        raise _bad()
    fl = raw.get("findings") or []
    if not isinstance(fl, list):
        raise _bad()
    ok, bad = [], []
    for f in map(_finding, fl):
        if f.quote.strip() and source_ok(f.source, doms):
            ok.append(f)
        else:
            bad.append(_demote(f))
    nt, nt_url = (None, None)
    if raw.get("next_terms") is not None:
        nt, nt_url = _next_terms(raw["next_terms"], doms)
    srcs = raw.get("sources") or []
    if not isinstance(srcs, list):
        raise _bad()
    notes = raw.get("notes")
    notes = notes[:_NOTES_MAX] if isinstance(notes, str) else ""
    return CardResult(entry.account_id, status, _period_end(raw.get("current_period_end"), doms),
                      tuple(ok), tuple(bad), nt, nt_url,
                      tuple(s.strip() for s in srcs if isinstance(s, str) and source_ok(s, doms)),
                      notes, trunc)


def _empty(entry: CardEntry, trunc: tuple[TruncatedSource, ...]) -> CardResult:
    return CardResult(entry.account_id, "not_found", None, (), (), None, None, (), "", trunc)


def parse_result(stdout: str, cards: tuple[CardEntry, ...]) -> TermsResult:
    block = last_json_block(stdout)
    try:
        data = json.loads(block)
    except (ValueError, RecursionError):
        data = None  # raised below, outside the except: no chained model text
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise _bad()
    log = data.get("fetch_log")
    log, trunc = _clean_log(log if isinstance(log, dict) else {}, cards)
    entries = {c.account_id: c for c in cards}
    seen: dict[int, object] = {}
    for raw in data["cards"]:
        aid = raw.get("account_id") if isinstance(raw, dict) else None
        if isinstance(aid, bool) or not isinstance(aid, int) or aid not in entries or aid in seen:
            raise _bad()
        seen[aid] = raw
    return TermsResult(tuple(_card(seen[c.account_id], c, trunc.get(c.account_id, ())) if c.account_id in seen
                             else _empty(c, trunc.get(c.account_id, ())) for c in cards), log)


def _system_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _join_card(card: CardResult, rules: tuple[dict, ...]) -> CardResult:
    rows = {r.get("id"): r for r in rules}
    ok, bad = [], list(card.unverified)
    for f in card.findings:
        if (f.rule_id is not None and f.rule_id not in rows) or (f.rule_id is None and f.field != "missing_rule"):
            bad.append(replace(f, reason=UNKNOWN_RULE))
            continue
        row = rows.get(f.rule_id, {})
        ok.append(replace(f, system=_system_value(row[f.field]) if f.field in row else None))
    return replace(card, findings=tuple(ok), unverified=tuple(bad))


def join_rules(result: TermsResult, rules: dict[int, tuple[dict, ...]]) -> TermsResult:
    """Join verified findings to the frozen input: a finding naming no live rule of its card (or a null rule_id
    outside ``missing_rule``) becomes unverified (``未知規則``); the ``system`` side is the input rule's value for a
    rule-row field and None otherwise — the model's ``system`` claim is never kept for a verified finding."""
    return replace(result, cards=tuple(_join_card(c, rules.get(c.account_id, ())) for c in result.cards))


def _q(text: str) -> str:
    return " ".join(text.split())


def _f(f: Finding) -> dict:
    return {"rule_id": f.rule_id, "field": f.field, "site": f.site, "system": f.system,
            "source": f.source, "quote": _q(f.quote), "reason": f.reason}


def _sorted(fs: tuple[Finding, ...]) -> list[dict]:
    return sorted((_f(f) for f in fs), key=lambda d: (
        d["rule_id"] if d["rule_id"] is not None else -1, d["field"], d["source"],
        d["site"] or "", d["system"] or "", d["quote"]))


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(card: CardResult, tier: str) -> str:
    if tier == "signal":
        obj: dict = {"notes": _q(card.notes)}
    else:
        pe, nt = card.current_period_end, card.next_terms
        obj = {
            "status": card.status,
            "current_period_end": None if pe is None else
            {"date": pe.date, "source": pe.source, "quote": _q(pe.quote)},
            "findings": _sorted(card.findings),
            "unverified": _sorted(card.unverified),
            "next_terms": None if nt is None else {
                "announced": nt.announced, "period": list(nt.period),
                "rules": sorted(({"name": r.get("name"), "rate": _dec(r.get("rate")),
                                  "cap": _dec(r.get("cap")), "notes": _q(r["notes"]) if r.get("notes") else None}
                                 for r in nt.rules), key=_canon),
                "source": nt.source, "quote": None if nt.quote is None else _q(nt.quote)},
            "next_terms_unverified_url": (card.next_terms_unverified_url or "").strip() or None,
        }
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()
