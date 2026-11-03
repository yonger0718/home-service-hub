"""Discord rendering of the reward-terms verdict (spec §5). Model text is defanged; unverified URLs print host only."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from notify.config import NotifyConfig
from notify.render import _settings_link, split_blocks
from notify.terms_input import TermsInput
from notify.terms_parse import CardResult, TermsResult
from notify.terms_state import ShowDecision

FIELD_ZH = {"ends_on": "結束日", "starts_on": "開始日", "rate": "回饋率", "total_cap": "上限", "is_basic": "基本",
            "name": "名稱", "description": "說明", "method": "方式", "fixed_amount": "固定金額", "window": "期間",
            "missing_rule": "缺規則"}
_ZW = "​"
_QUOTE_MAX = 80
_SHOW = ("show_findings", "show_announced", "show_signal", "show_not_found", "show_unverified",
         "show_period_only", "show_period", "show_truncated")


def _safe_text(s: object) -> str:
    """Defang model text: one line, no live URL scheme separator, mention or markdown link."""
    out = " ".join(str(s if s is not None else "").split())
    return out.replace("://", ":/" + _ZW + "/").replace("@", "@" + _ZW).replace("](", "]" + _ZW + "(")


def _host(url: str | None) -> str:
    try:
        host = urlsplit(url or "").hostname
    except ValueError:
        host = None
    return _safe_text((host or "").lower()) or "?"


def _quote(text: str) -> str:
    q = _safe_text(text)
    return f"　「{q[:_QUOTE_MAX]}{'…' if len(q) > _QUOTE_MAX else ''}」"


def _finding_lines(card: CardResult, names: dict, verified: bool) -> list[str]:
    out = []
    for f in (card.findings if verified else card.unverified):
        site, system = _safe_text(f.site), _safe_text(f.system)
        zh = FIELD_ZH.get(f.field, _safe_text(f.field))
        if not verified:
            out.append(f"· 未驗證（來源不在銀行網域）：{zh} {site} — {_host(f.source)}")
            continue
        if f.field == "missing_rule":
            out.append(f"· 缺規則：{site} — {f.source}")
        else:
            name = _safe_text(names.get(f.rule_id)) or "缺規則"
            out.append(f"· {name}：{zh} 官網 {site} / 系統 {system} — {f.source}")
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


def _block(card: CardResult, d: ShowDecision, head: str, names: dict, failed: list) -> str:
    lines = [head]
    if d.show_findings:
        lines += _finding_lines(card, names, True)
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
        lines += [f"· 來源文件過長，僅讀到前段（{u}）" for u in card.truncated]
    if d.show_unverified:
        lines += _finding_lines(card, names, False)
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
        if d is None or card is None or not any(getattr(d, f) for f in _SHOW):
            quiet.append(ci.card.official)
            continue
        names = {r.get("id"): r.get("name") for r in ci.rules}
        head = f"【{ci.card.official}】 → {_settings_link(cfg, aid)}"
        blocks.append(_block(card, d, head, names, failed))
    m = len(inp.cards)
    if not blocks and not inp.unregistered:
        return [f"📋 回饋條款查核 {inp.today}：{m} 張卡無變動"]
    out = [f"📋 回饋條款查核 {inp.today}（{m} 張卡，{len(blocks)} 張有事項）\narchive: {archive}"] + blocks
    if inp.unregistered:
        out.append("未登錄：" + "、".join(f"{n}（{i}）" for i, n in inp.unregistered))
    if quiet:
        out.append("無變動：" + "、".join(quiet))
    return split_blocks(out)
