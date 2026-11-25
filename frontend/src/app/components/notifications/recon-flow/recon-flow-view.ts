import { HttpErrorResponse } from '@angular/common/http';

import { daysBetween, slashDate } from '../../accounting/dates';
import { formatMoney } from '../../accounting/format';
import { InboxCard, buttonLabel, learnableAnswer, questionText } from '../../accounting/inbox/inbox-view';
import {
  CaseAction,
  InboxAccount,
  InboxButton,
  InboxCandidate,
  InboxItem,
  InboxPeerCandidate,
  InboxQuestion,
} from '../../accounting/inbox/inbox.model';
import { apiErrorText, splitApiCode } from '../../accounting/statements/statement-view';

const CHIPS: Partial<Record<InboxQuestion, string>> = {
  bank_only: '銀行有，你沒記',
  ledger_only: '你記了，銀行沒有',
  amount_differs: '金額不同',
  date_differs: '日期不同',
  transfer: '轉帳／繳費',
  ambiguous: '多筆可能',
};

export function flowChip(question: InboxQuestion): string {
  return CHIPS[question] ?? '';
}

export interface FlowEntry {
  id: number;
  date: string;
  title: string;
  amount: string;
}

/** The ledger entry a step compares against: `item.entry`, else the first candidate for amount_differs / date_differs. */
export function flowEntry(item: InboxItem): FlowEntry | null {
  if (item.entry) {
    const { id, date, title, amount } = item.entry;
    return { id, date, title, amount };
  }
  if (item.question === 'amount_differs' || item.question === 'date_differs') {
    const first = item.candidates[0];
    if (first) {
      return { id: first.entry_id, date: first.date, title: first.title, amount: first.amount };
    }
  }
  return null;
}

export function flowHeadline(item: InboxItem, currency: string): string {
  switch (item.question) {
    case 'bank_only':
      return `${item.line?.merchant ?? ''} ${formatMoney(item.line?.amount, currency, { sign: true })}`;
    case 'ledger_only': {
      const entry = flowEntry(item);
      if (!entry) return questionText(item, currency);
      return `${entry.title} ${formatMoney(entry.amount, currency, { sign: true })}`;
    }
    case 'amount_differs': {
      const ledger = flowEntry(item);
      // Without both sides, or with no real difference, there is no sign to read and no amount to name.
      if (!item.line || !ledger || Number(item.line.amount) === Number(ledger.amount)) return '金額不同';
      const bank = Number(item.line.amount);
      const entry = Number(ledger.amount);
      const diff = formatMoney(Math.abs(bank - entry), currency);
      const more = Math.abs(bank) > Math.abs(entry);
      if (bank < 0) return `銀行${more ? '多' : '少'}扣了 ${diff}`;
      return `銀行${more ? '多' : '少'}入了 ${diff}`;
    }
    case 'date_differs': {
      const entry = flowEntry(item);
      if (!item.line || !entry) return '同一筆，日期不同';
      const days = daysBetween(entry.date.slice(0, 10), item.line.date.slice(0, 10));
      if (days > 0) return `同一筆，晚了 ${days} 天入帳`;
      if (days < 0) return `同一筆，早了 ${-days} 天入帳`;
      return '同一筆，日期不同';
    }
    case 'transfer':
      return '轉帳或繳費，你沒記';
    case 'ambiguous':
      return `有 ${item.candidates.length} 筆可能是它`;
    default:
      return questionText(item, currency);
  }
}

/** True for a bank-only line that may be one part of an entry already recorded (#392). */
export function flowPartOf(item: InboxItem): boolean {
  return item.question === 'bank_only' && (item.part_of ?? []).length > 0;
}

/**
 * Extra line under the grid: the part-of warning, the transfer hint, or the item's note; null when none. The transfer
 * hint says which answer fits: pair the two accounts when a peer line exists, else wait for the other statement. With
 * one transfer leg known, 照帳單改這筆轉帳 is one tap, so the note says what it changes (the item's note follows on a
 * new line).
 */
export function flowNote(item: InboxItem, currency: string): string | null {
  if (flowPartOf(item)) {
    return questionText(item, currency);
  }
  if (item.question === 'transfer') {
    // No pair_transfer at all: the API offers no pairing here (e.g. the case has ledger candidates), so do not claim
    // the other line is missing.
    const peers = item.transfer?.pair_transfer?.peer_candidates;
    if (!peers) return '已經記過的話，選「已記在別處，對到…」；還沒記就先略過。';
    return peers.length > 0
      ? '選「配成轉帳…」把兩個帳戶的這筆對起來。'
      : '另一個帳戶還沒有對得上的那一筆（帳單可能還沒匯入）。已經記過的話選「已記在別處，對到…」；不然先略過，另一邊進來後再配。';
  }
  const note = item.note || null;
  if (item.transfer?.adjust_transfer?.entry_id != null) {
    // Both legs take the line's magnitude (out negative, in positive), so the amount carries no sign; each leg takes
    // its own statement's dates (the peer keeps its dates when its statement prints no line).
    const bank = formatMoney(Math.abs(Number(item.line?.amount ?? 0)), currency);
    const sentence = `會同時改另一個帳戶的那一筆。兩邊金額都改成 ${bank}；日期各自跟自己的帳單，另一邊沒有帳單就不動。`;
    return note ? `${sentence}\n${note}` : sentence;
  }
  return note;
}

/**
 * A 配成轉帳… row: 「2026/09/30 測試卡 → 測試銀行 $20,000」. The peer line's sign says which way the money went: money
 * out of the peer account (< 0) reads peer → this account, else this account → peer. The arrow already says the
 * direction, so the amount shows without a sign.
 */
export function peerRowLabel(peer: InboxPeerCandidate, accountName: string, currency: string): string {
  const [from, to] = Number(peer.amount) < 0 ? [peer.account_name, accountName] : [accountName, peer.account_name];
  return `${slashDate(peer.posted_date)} ${from} → ${to} ${formatMoney(Math.abs(Number(peer.amount)), currency)}`;
}

/** An answer the flow lists: an inbox button or one of the case actions (`/cases/{id}/actions`). */
export type FlowAnswer = InboxButton | CaseAction;

const CASE_ACTIONS: ReadonlySet<string> = new Set<CaseAction>([
  'adjust_transfer',
  'pair_transfer',
  'adjust_amount',
]);

export function isCaseAction(answer: FlowAnswer): answer is CaseAction {
  return CASE_ACTIONS.has(answer);
}

/** The ledger legs an ambiguous question could mean for 照帳單改轉帳…; none when the entry is already known. */
export function transferLegs(item: InboxItem): InboxCandidate[] {
  const adjust = item.transfer?.adjust_transfer;
  if (!adjust || adjust.entry_id !== null) {
    return [];
  }
  const legs = new Set(adjust.candidate_entry_ids);
  return item.candidates.filter((candidate) => legs.has(candidate.entry_id));
}

/** The candidates 改金額… may change on an ambiguous question: not the transfer legs (refused as protected). */
export function adjustEntries(item: InboxItem): InboxCandidate[] {
  if (item.question !== 'ambiguous') {
    return [];
  }
  const legs = new Set(item.transfer?.adjust_transfer?.candidate_entry_ids ?? []);
  return item.candidates.filter((candidate) => !legs.has(candidate.entry_id));
}

/**
 * The answers in the order the flow lists them: the server's, except 記為… goes last on a part-of line, with the
 * transfer answers ahead of them. On an ambiguous question 對到… stays first and 照帳單改轉帳… then 改金額… follow it.
 */
export function flowActions(item: InboxItem): FlowAnswer[] {
  const base: FlowAnswer[] =
    flowPartOf(item) && item.actions.includes('record')
      ? [...item.actions.filter((action) => action !== 'record'), 'record']
      : [...item.actions];
  const adjust = item.transfer?.adjust_transfer;
  const peers = item.transfer?.pair_transfer?.peer_candidates ?? [];
  const ambiguous = item.question === 'ambiguous';
  const lead: FlowAnswer[] = [];
  const afterMatch: FlowAnswer[] = [];
  if (adjust && (adjust.entry_id !== null || transferLegs(item).length > 0)) {
    (ambiguous ? afterMatch : lead).push('adjust_transfer');
  }
  if (item.question === 'transfer' && peers.length > 0) {
    lead.push('pair_transfer');
  }
  if (ambiguous && adjustEntries(item).length > 0) {
    afterMatch.push('adjust_amount');
  }
  if (afterMatch.length > 0) {
    const at = base.indexOf('match_candidate');
    base.splice(at < 0 ? base.length : at + 1, 0, ...afterMatch);
  }
  return lead.length === 0 &&
    base.length === item.actions.length &&
    base.every((a, i) => a === item.actions[i])
    ? item.actions
    : [...lead, ...base];
}

export function flowAnswerLabel(answer: FlowAnswer, item: InboxItem, currency: string): string {
  switch (answer) {
    case 'adjust_transfer':
      return item.transfer?.adjust_transfer?.entry_id === null
        ? '照帳單改轉帳…'
        : '照帳單改這筆轉帳';
    case 'pair_transfer':
      return '配成轉帳…';
    case 'adjust_amount':
      return '改金額…';
    default:
      return buttonLabel(answer, item, currency);
  }
}

/** The answers 以後都這樣 applies to, as the buttons name them: 只有「記為支出…」「忽略」會記住. Null when none. */
export function flowLearnScope(item: InboxItem, currency: string): string | null {
  if (item.rule_hint === null) {
    return null;
  }
  const answers = flowActions(item);
  const learning = answers.filter(
    (answer): answer is InboxButton => !isCaseAction(answer) && learnableAnswer(item.question, answer),
  );
  if (learning.length === 0) {
    return null;
  }
  const names = learning.map((action) => `「${buttonLabel(action, item, currency)}」`).join('');
  return learning.length === answers.length ? `${names}都會記住` : `只有${names}會記住`;
}

export interface FlowError {
  readonly text: string;
  /** Offer 重新讀取: the question changed under the owner, a fresh read shows what it is now. */
  readonly reload: boolean;
}

const CHANGED: FlowError = { text: '這一題的資料剛剛變了', reload: true };
/** No 重新讀取 here: the sentence asks for the answer again, and the answers are right above it. */
const UNKNOWN: FlowError = { text: '沒成功：答案沒有送出去，請再試一次', reload: false };

/** Codes that mean the case, its line or its candidates moved since the read. */
const CHANGED_CODES: ReadonlySet<string> = new Set([
  'stale_case',
  'candidate_changed',
  'case_moved',
  'not_offered',
  'no_line',
  'snapshot_conflict',
  'conflict_stale',
  'needs_recheck',
  'accrual_not_estimated',
  'proposal_superseded',
  'entry_out_of_scope',
  'peer_out_of_scope',
  'line_claimed',
  'line_not_in_case',
  'line_not_printed',
]);

/** Every other refusal in the owner's words (no code, no English). */
const PLAIN: Readonly<Record<string, string>> = {
  case_closed: '這筆已經處理過了',
  not_conserved: '金額對不上，不能直接對到這筆',
  protected: '這筆是轉帳或交割款，不能改金額',
  owner_may_record: '這筆你已經記過了',
  historical_statement: '這是舊帳單，不能再改',
  locked_until_cutover: '這筆是匯入的舊記錄，現在還不能改',
  rule_pattern_mismatch: '這幾筆描述不同，不能一起記住',
  rule_pattern_empty: '描述只有數字，沒辦法記住',
  not_learnable: '這種答案沒辦法記住',
  entry_claimed: '這筆記錄已經對到別的行了',
  entry_in_group: '這筆是分期或回饋的一部分，不能直接對',
  line_outside_period: '這筆的入帳日不在這期帳單內',
  action_busy: '系統正在處理這張帳單，等一下再試',
  reconcile_busy: '系統正在處理這張帳單，等一下再試',
  sweep_barrier_busy: '系統正在處理這張帳單，等一下再試',
  peer_leg_claimed: '另一個帳戶的那一筆已經配給別的轉帳了',
  peer_ambiguous: '另一個帳戶有好幾筆可能，沒辦法自動配',
  not_standalone: '這筆是分筆或群組的一部分，不能直接改',
  cross_currency: '兩邊幣別不同，不能直接配',
  peer_is_card: '另一邊是信用卡，不能這樣配',
  kind_sign: '金額正負方向不對，不能這樣改',
  fx_pair_required: '缺少外幣金額資料，不能直接改',
  has_settlements: '這筆已經有沖銷記錄，不能改',
  peer_not_live: '另一個帳戶的帳單還沒開始對帳',
  peer_statement_busy: '另一個帳戶的帳單正在處理，等一下再試',
  owner_deleted: '那一筆已經被刪掉了',
  duplicate_effect: '這個答案剛剛已經做過了',
  wrong_account: '那一筆記在別的帳戶',
  wrong_currency: '那一筆的幣別不同',
  not_member: '那一筆不在這個群組裡',
  foreign_zero: '外幣金額是 0，不能改',
  not_found: '找不到那一筆記錄',
  dirty_disabled: '這筆轉帳現在不能自動配，先略過或到帳單頁處理',
};

const KNOWN_CODES = [...CHANGED_CODES, ...Object.keys(PLAIN)];

/** The business code of a refusal: the string itself, a 409 / string `detail` text, or one named in a 422 `detail[].msg`. */
export function errorCode(codeOrErr: unknown): string | null {
  if (typeof codeOrErr === 'string') {
    return codeOrErr;
  }
  const text = apiErrorText(codeOrErr);
  if (text) {
    return splitApiCode(text).code;
  }
  if (codeOrErr instanceof HttpErrorResponse) {
    const detail = (codeOrErr.error as { detail?: unknown } | null)?.detail;
    if (Array.isArray(detail)) {
      for (const part of detail as { msg?: unknown }[]) {
        const msg = typeof part?.msg === 'string' ? part.msg : '';
        const code = KNOWN_CODES.find((key) => msg.includes(key));
        if (code) {
          return code;
        }
      }
    }
  }
  return null;
}

/** A refused or failed answer as the flow shows it under the buttons: never a raw code. */
export function flowError(codeOrErr: unknown): FlowError {
  const code = errorCode(codeOrErr);
  if (code !== null && CHANGED_CODES.has(code)) {
    return CHANGED;
  }
  const plain = code === null ? undefined : PLAIN[code];
  return plain ? { text: `沒成功：${plain}`, reload: false } : UNKNOWN;
}

export interface CompareRow {
  readonly label: '日期' | '商家' | '金額';
  readonly bank: string;
  readonly ledger: string;
  readonly differs: boolean;
  readonly bankMissing: boolean;
  readonly ledgerMissing: boolean;
}

export function compareRows(item: InboxItem, currency: string): readonly CompareRow[] {
  const line = item.line;
  const entry = flowEntry(item);
  const both = line !== null && entry !== null;
  const side = (present: boolean, text: string, label: CompareRow['label']): string =>
    present ? text : label === '商家' ? '沒有對應記錄' : '—';
  const row = (label: CompareRow['label'], bank: string, ledger: string, differs: boolean): CompareRow => ({
    label,
    bank: side(line !== null, bank, label),
    ledger: side(entry !== null, ledger, label),
    differs: both && differs,
    bankMissing: line === null,
    ledgerMissing: entry === null,
  });
  return [
    row(
      '日期',
      line ? slashDate(line.date) : '',
      entry ? slashDate(entry.date) : '',
      both && line.date.slice(0, 10) !== entry.date.slice(0, 10),
    ),
    row('商家', line?.merchant ?? '', entry?.title ?? '', false),
    row(
      '金額',
      line ? formatMoney(line.amount, currency, { sign: true }) : '',
      entry ? formatMoney(entry.amount, currency, { sign: true }) : '',
      both && Number(line.amount) !== Number(entry.amount),
    ),
  ];
}

export function flowLearnable(item: InboxItem): boolean {
  return item.rule_hint !== null && item.actions.some((a) => learnableAnswer(item.question, a));
}

/** One-item card for the match and record sheets. */
export function flowCard(item: InboxItem, account: InboxAccount): InboxCard {
  return {
    key: String(item.case_id),
    question: item.question,
    account,
    items: [item],
    actions: item.actions,
    rule_hint: item.rule_hint,
    learnable: flowLearnable(item),
  };
}

/**
 * The entry 對到… must not offer: 「已記在別處，對到…」 on an amount question means another entry, not the one the step
 * is already comparing. Null otherwise.
 */
export function flowExcluded(item: InboxItem): number | null {
  return item.question === 'amount_differs' ? (flowEntry(item)?.id ?? null) : null;
}
