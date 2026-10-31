"""Discord text for the daily and weekly messages (spec §5.4, §5.7, §6): pure functions, no I/O."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .api import TZ
from .config import NotifyConfig
from .debits import REASON_TEXT, DailyReport, DebitItem, Skip, SourceBlock
from .digest import AccountCases, CaseLine, WeeklyReport

LIMIT = 2000
_WEEKDAYS = "一二三四五六日"
_MINUS = "−"


def fmt_amount(d: Decimal) -> str:
    """48,200 — two decimals only when not integral; negatives use the typographic minus."""
    sign = _MINUS if d < 0 else ""
    d = abs(d)
    return sign + (f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}")


def weekday_zh(d: date) -> str:
    return f"（{_WEEKDAYS[d.weekday()]}）"


def _md(d: date) -> str:
    return f"{d:%m/%d}"


def _statement_link(cfg: NotifyConfig, account_id: int, statement_id: int) -> str:
    return f"{cfg.hub_base_url}/accounting/accounts/{account_id}/statements/{statement_id}"


def _settings_link(cfg: NotifyConfig, account_id: int) -> str:
    return f"{cfg.hub_base_url}/accounting/accounts/{account_id}/settings"


# --- daily ---------------------------------------------------------------------------------------------------


def _join(segs: list[str]) -> str:
    """Segments joined by ' · ', except that a full-width ） closes the gap before the dot (spec §5.7)."""
    out = segs[0]
    for seg in segs[1:]:
        out += ("· " if out.endswith("）") else " · ") + seg
    return out


def _item_line(it: DebitItem, today: date, cfg: NotifyConfig) -> str:
    link = ""
    if it.from_rule:
        segs = ["📄 " + it.card_name]
        due = f"到期 {_md(it.due)}（規則）"
        segs += [due, "⚠ 逾期未繳 · 帳單未到"] if it.overdue_days > 0 else ["帳單未到", due]
        segs += ["金額未知，請自行確認", "不會自動扣繳"]
        return "• " + _join(segs)
    amount = fmt_amount(it.amount)
    segs = [it.card_name, f"已繳 {fmt_amount(it.paid)} / {amount}" if it.paid > 0 else amount]
    if it.due is None:
        segs.append("到期日未辨識")
    elif it.cls == "manual":
        segs.append(f"到期 {_md(it.due)}{weekday_zh(it.due)}")
    elif it.due != it.debit_day or it.unpaid_reason is not None:  # a 未扣款 line always names its due date
        segs.append(f"到期 {_md(it.due)}")
    if it.cls == "auto" and it.debit_day is not None and it.debit_day >= today:
        segs.append(f"扣款 {_md(it.debit_day)}{weekday_zh(it.debit_day)}")
        if it.debit_day == today:
            segs.append("今天扣款")
    if it.overdue_days > 0:
        segs.append(f"⚠ 逾期未繳 {it.overdue_days} 天")
    if it.unpaid_reason is not None:
        segs.append("⚠ 未扣款 · 原因 " + REASON_TEXT.get(it.unpaid_reason, "未知"))
    if it.note and it.due is not None:  # 到期日未辨識 is already the due segment
        segs.append(it.note)
    if it.open_cases > 0 and it.statement_id is not None:
        segs.append(f"這期還有 {it.open_cases} 筆對不上")
        link = " → " + _statement_link(cfg, it.card_id, it.statement_id)
    return "• " + _join(segs) + link


def _source_block(b: SourceBlock, today: date, cfg: NotifyConfig) -> str:
    if b.balance is None or b.projected is None:  # valuation source without a value (debits.SourceBlock)
        return "\n".join([f"**{b.name}**  餘額 市值暫不可得"] + [_item_line(i, today, cfg) for i in b.items])
    head = f"**{b.name}**  餘額 {fmt_amount(b.balance)} → 扣款後 {fmt_amount(b.projected)}"
    if b.shortfall is not None:
        head += f" ⚠ 缺 {fmt_amount(b.shortfall)}"
    return "\n".join([head] + [_item_line(i, today, cfg) for i in b.items])


def _skip_line(s: Skip, cfg: NotifyConfig) -> str:
    line = f"• {s.card_name} · {fmt_amount(s.amount)} · {REASON_TEXT.get(s.reason, s.reason)}"
    return line + (" → " + _settings_link(cfg, s.card_id) if s.card_id is not None else "")


def render_daily(rep: DailyReport, cfg: NotifyConfig) -> list[str]:
    blocks = [_source_block(b, rep.today, cfg) for b in rep.blocks]
    if rep.cleared:
        blocks.append("\n".join(f"✅ {b.name} 已補足" for b in rep.cleared))
    if rep.manual:
        blocks.append("\n".join(["**手動繳**"] + [_item_line(i, rep.today, cfg) for i in rep.manual]))
    if rep.skips:
        blocks.append("\n".join(["⏭ 自動扣繳跳過"] + [_skip_line(s, cfg) for s in rep.skips]))
    if rep.cathay:
        blocks.append("\n".join(["**國泰對帳單**"] + [f"• {line.text}" for line in rep.cathay]))
    if not blocks:
        return []
    return split_blocks([f"🔔 扣款提醒 {rep.today.isoformat()}{weekday_zh(rep.today)}"] + blocks)


# --- weekly --------------------------------------------------------------------------------------------------


def _case_line(c: CaseLine, account_id: int, cfg: NotifyConfig) -> str:
    segs = [_md(c.date) if c.date else None, fmt_amount(c.amount) if c.amount is not None else None, c.text,
            c.question_zh]
    return "• " + " · ".join(s for s in segs if s) + " → " + _statement_link(cfg, account_id, c.statement_id)


def _account_cases(a: AccountCases, cfg: NotifyConfig) -> str:
    lines = [f"**{a.name}** · {a.count} 筆 · 最久 {a.oldest_days} 天"]
    lines += [_case_line(c, a.account_id, cfg) for c in a.items]
    if a.truncated > 0:
        lines.append(f"+{a.truncated} 筆")
    return "\n".join(lines)


def render_weekly(rep: WeeklyReport, cfg: NotifyConfig) -> list[str]:
    header = f"📋 帳務週報 {rep.today.isoformat()}"
    blocks: list[str] = []
    for n, a in enumerate(rep.open_cases):
        text = _account_cases(a, cfg)
        blocks.append(("**對不上的交易**\n" if n == 0 else "") + text)
    if rep.missing:
        blocks.append("\n".join(["**該來沒來的帳單**"] + [
            f"• {m.name} · 上次截止 {_md(m.last_period_end)} · 預期 {_md(m.expected)} · 晚了 {m.days_late} 天"
            for m in rep.missing]))
    if rep.skips:
        blocks.append("\n".join(["**自動扣繳本週跳過**"] + [_skip_line(s, cfg) for s in rep.skips]))
    if rep.cathay:
        blocks.append("\n".join(["**國泰對帳單待處理**"] + [f"• {line.text}" for line in rep.cathay]))
    gaps = rep.gaps
    gap_lines = ([f"• {n} · 沒有設定扣款帳戶" for n in gaps.unlinked_cards]
                 + [f"• {n} · 缺結帳日或繳款規則，每日提醒無法涵蓋" for n in gaps.cards_without_rule]
                 + [f"• {n} 帳單 #{sid} 沒有到期日" for n, sid in gaps.statements_without_due])
    if gap_lines:
        blocks.append("\n".join(["**設定缺漏**"] + gap_lines))
    if not blocks:
        stamp = rep.last_daily_run.astimezone(TZ).strftime("%m/%d %H:%M") if rep.last_daily_run else "尚無"
        blocks = [f"✅ 本週沒有待處理 · 最近一次每日檢查 {stamp}"]
    return split_blocks([header] + blocks)


def render_failure(reason: str) -> str:
    return f"❌ 帳務通知失敗：{reason}"


# --- splitting -----------------------------------------------------------------------------------------------


def _split_lines(block: str, limit: int) -> list[str]:
    """Break an oversized block on line boundaries (a single over-long line stays whole)."""
    out: list[str] = []
    cur = ""
    for line in block.split("\n"):
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    out.append(cur)
    return out


def split_blocks(blocks: list[str], limit: int = LIMIT) -> list[str]:
    """Pack blocks, in order, into messages of at most `limit` characters joined by a blank line."""
    pieces = [p for b in blocks for p in (_split_lines(b, limit) if len(b) > limit else [b])]
    out: list[str] = []
    for piece in pieces:
        if out and len(out[-1]) + 2 + len(piece) <= limit:
            out[-1] += "\n\n" + piece
        else:
            out.append(piece)
    return out
