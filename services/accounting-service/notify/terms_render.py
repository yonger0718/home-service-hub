"""Discord rendering of the reward-terms verdict (spec §5). Model text is defanged; unverified URLs print host only;
every full URL prints as ``<url>`` (one Discord token, no embed) and has passed ``source_ok``."""
from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlsplit

from notify.config import NotifyConfig
from notify.render import _settings_link, split_blocks
from notify.terms_input import TermsInput
from notify.terms_parse import CardResult, TermsResult, TruncatedSource, source_ok
from notify.terms_state import ShowDecision

FIELD_ZH = {"ends_on": "結束日", "starts_on": "開始日", "rate": "回饋率", "total_cap": "上限", "is_basic": "基本",
            "name": "名稱", "description": "說明", "method": "方式", "fixed_amount": "固定金額", "window": "期間",
            "missing_rule": "缺規則"}
_ZW = "​"
_QUOTE_MAX = 80
_SHOW = tuple(f.name for f in fields(ShowDecision) if f.name.startswith("show_"))


def has_items(d: ShowDecision | None) -> bool:
    """A card has something to show (and gets its own block); the CLI summary counts with this too."""
    return d is not None and any(getattr(d, name) for name in _SHOW)


_MARKDOWN = re.compile(r"([\\*_~|`])")
_DISCORD = re.compile(r"(?i)(disc)(ord)")


def _safe_text(s: object) -> str:
    """Defang model text: one line, Discord markdown escaped, no live URL scheme separator, mention, markdown link
    or ``<…>`` token (channel, timestamp, emoji); the word ``discord`` is broken so no invite form (``discord.gg/x``,
    ``discordapp.com/invite/x``, …) survives."""
    out = _MARKDOWN.sub(r"\\\1", " ".join(str(s if s is not None else "").split()))
    out = (out.replace("://", ":/" + _ZW + "/").replace("@", "@" + _ZW).replace("](", "]" + _ZW + "(")
           .replace("<", "<" + _ZW))
    return _DISCORD.sub("\\1" + _ZW + "\\2", out)


def _link(url: str) -> str:
    return f"<{url}>"


def _host(url: str | None) -> str:
    try:
        host = urlsplit(url or "").hostname
    except ValueError:
        host = None
    return _safe_text((host or "").lower()) or "?"


def _quote(text: str) -> str:
    q = _safe_text(text)
    return f"　「{q[:_QUOTE_MAX]}{'…' if len(q) > _QUOTE_MAX else ''}」"


def _finding_lines(card: CardResult, rows: dict, verified: bool) -> list[str]:
    """``rows`` maps rule id → the frozen input rule row; the 系統 side prints only for a field of that row."""
    out = []
    for f in (card.findings if verified else card.unverified):
        site = _safe_text(f.site)
        zh = FIELD_ZH.get(f.field, _safe_text(f.field))
        if not verified:
            why = _safe_text(f.reason) or "來源不在銀行網域"
            out.append(f"· 未驗證（{why}）：{zh} {site} — {_host(f.source)}")
            continue
        if f.field == "missing_rule":
            out.append(f"· 缺規則：{site} — {_link(f.source)}")
        else:
            row = rows.get(f.rule_id) or {}
            name = _safe_text(row.get("name")) or "?"
            system = f" / 系統 {_safe_text(f.system) or '無'}" if f.field in row else ""
            out.append(f"· {name}：{zh} 官網 {site}{system} — {_link(f.source)}")
        out.append(_quote(f.quote))
    return out


def _announced(card: CardResult) -> list[str]:
    nt = card.next_terms
    if nt is None or not nt.announced:
        return []
    a, b = (nt.period[0] or "?"), (nt.period[1] or "?")
    out = [f"· 下期條款已公告 {a}～{b}："]
    for r in nt.rules:
        rate, cap = _safe_text(r.get("rate")), _safe_text(r.get("cap"))
        line = f"　- {_safe_text(r.get('name'))} {rate + '%' if rate else '?'} 上限 {cap or '無'}"
        notes = _safe_text(r.get("notes"))
        out.append(f"{line} {notes}" if notes else line)
    return out


def _card_failures(failed: list, domains: tuple[str, ...]) -> list[str]:
    """fetch_log failures whose URL passes this card's own source rule (parser keeps a ' (NNN)' suffix)."""
    out = []
    for entry in failed:
        url, _, rest = str(entry).strip().partition(" ")
        if source_ok(url, domains):
            suffix = _safe_text(rest)
            out.append(f"{_link(url)} {suffix}" if suffix else _link(url))
    return out


def _truncated(t: TruncatedSource) -> str:
    where = _link(t.url) if t.verified else _host(t.url)
    read = _safe_text(t.read)
    return f"{where} ({read})" if read else where


def _block(card: CardResult, d: ShowDecision, head: str, rows: dict, failed: list) -> str:
    lines = [head]
    if d.show_findings:
        lines += _finding_lines(card, rows, True)
    if d.show_period and card.current_period_end:
        lines.append(f"· 條款期間至 {card.current_period_end.date}")
    if d.show_announced:
        lines += _announced(card)
    if d.show_signal:
        notes = [s for s in (_safe_text(x) for x in card.notes.splitlines()) if s]
        lines += [f"· {s}" for s in notes[:3]]
    if d.show_not_found:
        n = len(card.unverified)
        lines.append("· 找不到官網來源" + (f"（{n} 項未驗證）" if n else ""))
        lines += [f"　- {u}" for u in failed]
    if d.show_truncated:
        lines += [f"· 來源文件過長，僅讀到前段（{_truncated(t)}）" for t in card.truncated]
    if d.show_unverified:
        lines += _finding_lines(card, rows, False)
        if card.next_terms_unverified_url:
            lines.append(f"· 下期條款：來源未驗證（{_host(card.next_terms_unverified_url)}）")
    if d.show_period_only and card.current_period_end:
        lines.append(f"· 無差異，條款期間至 {card.current_period_end.date}")
    return "\n".join(lines)


def render_terms(inp: TermsInput, result: TermsResult, decisions: dict[int, ShowDecision],
                 cfg: NotifyConfig, archive: Path) -> list[str]:
    by_id = {c.account_id: c for c in result.cards}
    failed = list(result.fetch_log.get("failed", []))
    blocks: list[str] = []
    quiet: list[str] = []
    for ci in inp.cards:
        aid, d, card = ci.card.account_id, decisions.get(ci.card.account_id), by_id.get(ci.card.account_id)
        if card is None or not has_items(d):
            quiet.append(ci.card.official)
            continue
        rows = {r.get("id"): r for r in ci.rules}
        head = f"【{ci.card.official}】 → {_link(_settings_link(cfg, aid))}"
        blocks.append(_block(card, d, head, rows, _card_failures(failed, ci.card.bank_domains)))
    m = len(inp.cards)
    if not blocks and not inp.unregistered:
        return [f"📋 回饋條款查核 {inp.today}：{m} 張卡無變動"]
    out = [f"📋 回饋條款查核 {inp.today}（{m} 張卡，{len(blocks)} 張有事項）\narchive: {archive}"] + blocks
    if inp.unregistered:
        out.append("未登錄：" + "、".join(f"{n}（{i}）" for i, n in inp.unregistered))
    if quiet:
        out.append("無變動：" + "、".join(quiet))
    return split_blocks(out)
