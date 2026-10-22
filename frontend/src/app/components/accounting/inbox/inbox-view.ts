import { HttpErrorResponse } from '@angular/common/http';

import { slashDate } from '../dates';
import { formatMoney } from '../format';
import { actionErrorMessage, apiErrorText, splitApiCode } from '../statements/statement-view';
import { InboxAccount, InboxButton, InboxItem, InboxQuestion, InboxRuleHint } from './inbox.model';

/** One row of the inbox: a single case, or the unfolded cases that share a `group_key`. */
export interface InboxCard {
  key: string;
  question: InboxQuestion;
  account: InboxAccount;
  items: InboxItem[];
  actions: InboxButton[];
  rule_hint: InboxRuleHint | null;
  /** True when "以後都這樣" can be offered: a rule hint exists and the card can be recorded or dismissed. */
  learnable: boolean;
}

export interface InboxCards {
  ask: InboxCard[];
  proposed: InboxCard[];
  balanceGap: InboxCard[];
  settlement: InboxCard[];
  other: InboxCard[];
}

export const AMOUNT_ANY_LABEL = '金額不同也算';

export const INBOX_ERRORS: Record<string, string> = {
  stale_case: '對象已變，重新整理',
  candidate_changed: '對象已變，重新整理',
  case_closed: '已經處理過了',
  not_offered: '這個答案已不適用',
  not_conserved: '金額對不上，不能直接對',
  protected: '這筆是轉帳／交割，不能改金額',
  owner_may_record: '你已經記過了',
  historical_statement: '舊對帳單不能改',
  locked_until_cutover: 'cutover 後才能改',
  rule_pattern_mismatch: '這幾筆描述不同，不能一起學',
  rule_pattern_empty: '描述只有數字，不能學',
  not_learnable: '這種答案不能學',
};

function makeCard(account: InboxAccount, key: string, first: InboxItem): InboxCard {
  return {
    key,
    question: first.question,
    account,
    items: [first],
    actions: first.actions,
    rule_hint: first.rule_hint,
    learnable: first.rule_hint !== null && (first.actions.includes('record') || first.actions.includes('dismiss')),
  };
}

/** Unfolded items merge by `group_key` within an account; everything else is one card each. Order is preserved. */
export function buildCards(accounts: InboxAccount[]): InboxCards {
  const out: InboxCards = { ask: [], proposed: [], balanceGap: [], settlement: [], other: [] };
  for (const account of accounts) {
    const grouped = new Map<string, InboxCard>();
    for (const item of account.items) {
      const merge = !item.folded && item.group_key !== null;
      const existing = merge ? grouped.get(item.group_key as string) : undefined;
      if (existing) {
        existing.items.push(item);
        continue;
      }
      const card = makeCard(account, merge ? (item.group_key as string) : `case:${item.case_id}`, item);
      if (merge) {
        grouped.set(item.group_key as string, card);
      }
      bucket(out, item.question).push(card);
    }
  }
  return out;
}

function bucket(cards: InboxCards, question: InboxQuestion): InboxCard[] {
  switch (question) {
    case 'proposed':
      return cards.proposed;
    case 'balance_gap':
      return cards.balanceGap;
    case 'settlement':
      return cards.settlement;
    case 'other':
      return cards.other;
    default:
      return cards.ask;
  }
}

export function questionText(item: InboxItem, currency: string): string {
  switch (item.question) {
    case 'bank_only':
      return '銀行有、你沒記';
    case 'transfer':
      return '轉帳／繳費，你沒記 — 在時間軸記一筆轉帳，或對到已記的';
    case 'ledger_only':
      return '你記了，銀行沒有';
    case 'amount_differs': {
      const bank = Number(item.line?.amount ?? 0);
      const entry = Number((item.entry ?? item.candidates[0])?.amount ?? 0);
      return `銀行 ${formatMoney(bank, currency)}，你記 ${formatMoney(entry, currency)}（差 ${formatMoney(Math.abs(bank - entry), currency)}）`;
    }
    case 'date_differs': {
      const entryDate = (item.entry ?? item.candidates[0])?.date;
      return `你記 ${entryDate ? shortSlash(entryDate) : ''}，銀行 ${item.line ? shortSlash(item.line.date) : ''} 扣`;
    }
    case 'ambiguous':
      return `有 ${item.candidates.length} 筆可能是它`;
    case 'other':
      return `需要在 API 處理（案件 #${item.case_id}）`;
    case 'balance_gap':
      return '期初差額';
    case 'settlement':
      return '交割款，等證券連動';
    case 'proposed':
      return `系統有提議：${item.proposal?.action ?? ''}`;
  }
}

/** `2026-10-02` → `10/2` (M/D, no zero padding). */
function shortSlash(iso: string): string {
  const [, month, day] = slashDate(iso).split('/');
  return `${Number(month)}/${Number(day)}`;
}

export function cardTitle(card: InboxCard, currency: string): string {
  const first = card.items[0];
  const base = first.line
    ? `${slashDate(first.line.date)} ${first.line.merchant} ${formatMoney(first.line.amount, currency, { sign: true })}`
    : first.entry
      ? `${slashDate(first.entry.date)} ${first.entry.title} ${formatMoney(first.entry.amount, currency, { sign: true })}`
      : `案件 #${first.case_id}`;
  return card.items.length > 1 ? `${base} ×${card.items.length} 筆` : base;
}

export function lineKind(item: InboxItem): 'expense' | 'income' {
  return Number(item.line?.amount ?? 0) < 0 ? 'expense' : 'income';
}

export function buttonLabel(button: InboxButton, item: InboxItem, currency: string): string {
  switch (button) {
    case 'record':
      return lineKind(item) === 'expense' ? '記為支出…' : '記為收入…';
    case 'match_candidate':
      return item.question === 'date_differs' && item.line ? `對到 ${shortSlash(item.line.date)} 那行` : '已記在別處，對到…';
    case 'accept_bank_amount':
      return `改成銀行的 ${formatMoney(item.line?.amount, currency, { sign: true })}`;
    case 'defer':
      return '等下期';
    case 'dismiss':
      return ['amount_differs', 'date_differs', 'ambiguous'].includes(item.question) ? '不是同一筆' : '忽略';
    case 'anchor':
      return '對齊銀行期初';
    case 'apply_proposal':
      return '套用';
    case 'reject_proposal':
      return '退回';
  }
}

export function learnLabel(hint: InboxRuleHint, _button: InboxButton): string {
  return `以後「${hint.merchant_pattern}」都這樣`;
}

export function recordAnswer(kind: 'expense' | 'income', categoryName: string): string {
  return `${kind === 'expense' ? '記為支出' : '記為收入'} ${categoryName}`;
}

export function matchAnswer(title: string): string {
  return `對到 ${title}`;
}

/** A business code, or an `HttpErrorResponse` carrying one (409 text, 422 `detail[].msg`), in the owner's words. */
export function inboxErrorMessage(codeOrErr: string | null | unknown): string {
  if (typeof codeOrErr === 'string') {
    return INBOX_ERRORS[codeOrErr] ?? '沒成功：' + codeOrErr;
  }
  const text = apiErrorText(codeOrErr);
  if (text) {
    const known = INBOX_ERRORS[splitApiCode(text).code];
    if (known) {
      return known;
    }
  } else if (codeOrErr instanceof HttpErrorResponse) {
    const detail = (codeOrErr.error as { detail?: unknown } | null)?.detail;
    if (Array.isArray(detail)) {
      for (const item of detail as { msg?: unknown }[]) {
        const msg = typeof item?.msg === 'string' ? item.msg : '';
        const code = Object.keys(INBOX_ERRORS).find((key) => msg.includes(key));
        if (code) {
          return INBOX_ERRORS[code];
        }
      }
    }
  }
  return actionErrorMessage(codeOrErr);
}
