import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { makeEntry } from '../testing/fixtures';
import { StatementDetail, StatementSummary } from './statement.model';
import {
  actionsAvailable,
  buildSegments,
  confirmBlockedReason,
  confirmRefusal,
  deltaWarns,
  headerComparison,
  periodConsistent,
  reconProgress,
  splitApiCode,
  statementChips,
  statementForPeriod,
} from './statement-view';

function summary(overrides: Partial<StatementSummary> = {}): StatementSummary {
  return {
    id: 1,
    account_id: 6,
    kind: 'card',
    currency: 'TWD',
    period_start: '2026-08-21',
    period_end: '2026-09-20',
    closing_date: '2026-09-20',
    due_date: '2026-10-05',
    opening_balance: null,
    statement_total: '35861.0000',
    minimum_payment: null,
    origin: 'import',
    mode: 'live',
    status: 'open',
    conflict_open: false,
    needs_recheck: false,
    current_revision_id: 1,
    matched_count: 2,
    explained_count: 1,
    open_case_count: 3,
    incoming_deferrals_pending: 0,
    ...overrides,
  };
}

function detail(overrides: Partial<StatementDetail> = {}): StatementDetail {
  return {
    ...summary(),
    lines: [],
    cases: [],
    stale_events_pending: false,
    revisions: [],
    ...overrides,
  };
}

describe('statement chips and progress', () => {
  it('labels a live open statement 對帳中 and stacks recheck and stale', () => {
    expect(statementChips(summary(), true).map((chip) => chip.label)).toEqual([
      '對帳中',
      '需重新同步',
    ]);
    expect(statementChips(summary({ needs_recheck: true })).map((chip) => chip.label)).toEqual([
      '對帳中',
      '需重新檢查',
    ]);
  });

  it('replaces 已確認 with 已確認（有新版本） while a conflict is open', () => {
    expect(statementChips(summary({ status: 'reconciled' })).map((chip) => chip.label)).toEqual([
      '已確認',
    ]);
    expect(
      statementChips(summary({ status: 'reconciled', conflict_open: true })).map(
        (chip) => chip.label,
      ),
    ).toEqual(['已確認（有新版本）']);
  });

  it('keeps 歷史 and still reports a conflict on a historical statement', () => {
    expect(
      statementChips(summary({ mode: 'historical', conflict_open: true })).map(
        (chip) => chip.label,
      ),
    ).toEqual(['歷史', '已確認（有新版本）']);
  });

  it('counts 對帳 k/n as matched over matched plus open cases', () => {
    expect(reconProgress(summary({ matched_count: 37, open_case_count: 14 }))).toEqual({
      k: 37,
      n: 51,
    });
  });

  it('matches an imported statement to the billing cycle by period end', () => {
    const rows = [summary({ id: 4, period_end: '2026-08-20' }), summary()];
    expect(statementForPeriod(rows, '2026-09-20')?.id).toBe(1);
    expect(statementForPeriod(rows, '2026-10-20')).toBeNull();
  });
});

describe('header comparison', () => {
  it('compares a card bill with the period spend, not the balance', () => {
    const comparison = headerComparison(summary(), {
      spend: '-35480.5255',
      end_balance: '-37930.5255',
    });
    expect(comparison.statementLabel).toBe('帳單應繳');
    expect(comparison.ledgerLabel).toBe('帳本本期');
    expect(comparison.statementAmount).toBe(35861);
    expect(comparison.ledgerAmount).toBeCloseTo(35480.5255);
    expect(comparison.delta).toBeCloseTo(380.4745);
    expect(deltaWarns(comparison.delta, 'TWD')).toBe(true);
    expect(deltaWarns(0.4, 'TWD')).toBe(false);
  });

  it('compares a bank ending balance with the ledger balance at period end', () => {
    const comparison = headerComparison(summary({ kind: 'bank', statement_total: '227189.0000' }), {
      spend: '-100.0000',
      end_balance: '227204.0000',
    });
    expect(comparison.statementLabel).toBe('期末餘額');
    expect(comparison.ledgerLabel).toBe('帳本餘額');
    expect(comparison.delta).toBe(-15);
  });
});

describe('actions and consistency', () => {
  it('hides actions on historical statements and blocks confirm while cases are open', () => {
    expect(actionsAvailable(summary())).toBe(true);
    expect(actionsAvailable(summary({ mode: 'historical' }))).toBe(false);
    expect(confirmBlockedReason(summary({ open_case_count: 2 }))).toBe('仍有未處理項目');
    expect(confirmBlockedReason(summary({ open_case_count: 0 }))).toBeNull();
  });

  it('marks a historical period consistent only when nothing is open or flagged', () => {
    expect(periodConsistent(summary({ open_case_count: 0 }))).toBe(true);
    expect(periodConsistent(summary({ open_case_count: 0, needs_recheck: true }))).toBe(false);
    expect(periodConsistent(summary({ open_case_count: 0, conflict_open: true }))).toBe(false);
  });
});

describe('confirm refusal', () => {
  it('reads checks from a code: detail message and lists the ones that failed', () => {
    const checks = JSON.stringify({ open_cases: false, conserved: true, conflict_open: false });
    const err = new HttpErrorResponse({
      status: 409,
      error: { message: `confirm_refused: ${checks}` },
    });
    expect(confirmRefusal(err)?.map((check) => check.label)).toEqual([
      '仍有未處理項目',
      '有未解決的新版本',
    ]);
  });

  it('accepts a FastAPI string detail and ignores other codes', () => {
    const err = new HttpErrorResponse({ status: 409, error: { detail: 'confirm_retry' } });
    expect(splitApiCode('confirm_retry')).toEqual({ code: 'confirm_retry', detail: null });
    expect(confirmRefusal(err)).toBeNull();
  });
});

describe('segments', () => {
  const entry = makeEntry({ id: 40, name: '午餐', amount: '-80.0000', posted_date: '2026-09-02' });

  it('splits matched lines, statement-only lines and ledger-only cases', () => {
    const segments = buildSegments(
      detail({
        lines: [
          {
            id: 1,
            event_id: 10,
            seq: 1,
            txn_date: null,
            posted_date: '2026-09-01',
            merchant_raw: '誠品書店',
            merchant_norm: '誠品書店',
            printed_amount: '-100.0000',
            flow_amount: '-100.0000',
            foreign_amount: null,
            foreign_currency: null,
            line_kind: 'purchase',
            installment_seq: null,
            installment_total: null,
            matched: true,
            flag: 'text_changed',
            coverage: [
              {
                entry_id: 40,
                group_id: null,
                role: 'principal',
                flow: '-100.0000',
                match_rule: 'exact',
                match_kind: 'auto',
                status: 'active',
              },
            ],
          },
          {
            id: 2,
            event_id: 11,
            seq: 2,
            txn_date: null,
            posted_date: '2026-09-03',
            merchant_raw: '屈臣氏',
            merchant_norm: '屈臣氏',
            printed_amount: '-50.0000',
            flow_amount: '-50.0000',
            foreign_amount: null,
            foreign_currency: null,
            line_kind: 'purchase',
            installment_seq: null,
            installment_total: null,
            matched: false,
            coverage: [],
          },
        ],
        cases: [
          {
            id: 4,
            kind: 'line_unmatched',
            status: 'open',
            event_id: 11,
            line_id: 2,
            entry_id: null,
            explanation: null,
            candidates: [],
            context: {},
            version: 1,
            created_at: '2026-09-21T00:00:00Z',
          },
          {
            id: 8,
            kind: 'entry_unmatched',
            status: 'open',
            event_id: null,
            line_id: null,
            entry_id: 40,
            explanation: null,
            candidates: [],
            context: {},
            version: 1,
            created_at: '2026-09-21T00:00:00Z',
          },
          {
            id: 9,
            kind: 'balance_gap',
            status: 'open',
            event_id: null,
            line_id: null,
            entry_id: null,
            explanation: null,
            candidates: [],
            context: { gap: '-15.0000' },
            version: 1,
            created_at: '2026-09-21T00:00:00Z',
          },
          {
            id: 3,
            kind: 'parse_review',
            status: 'open',
            event_id: 11,
            line_id: 2,
            entry_id: null,
            explanation: null,
            candidates: [],
            context: { quarantined_event_id: 11, equivalence: 'changed' },
            version: 1,
            created_at: '2026-09-21T00:00:00Z',
          },
        ],
      }),
      new Map([[40, entry]]),
    );

    expect(segments.matched.map((row) => row.title)).toEqual(['誠品書店']);
    expect(segments.matched[0].textChanged).toBe(true);
    expect(segments.matched[0].pills).toContain('文字已變更');
    expect(segments.matched[0].matchRules).toEqual(['exact']);
    expect(segments.matched[0].covered[0].title).toBe('午餐');

    expect(segments.statementOnly.map((row) => row.title)).toEqual(['屈臣氏']);
    expect(segments.statementOnly[0].quarantined).toBe(true);
    expect(segments.statementOnly[0].pills).toContain('資料已變更，待確認');
    expect(segments.statementOnly[0].pills).toContain('僅帳單有');

    expect(segments.ledgerOnly.map((row) => row.title)).toEqual(['午餐', '餘額差異']);
    expect(segments.ledgerOnly[1].amount).toBe('-15.0000');
    expect(segments.ledgerOnly[0].caseKind).toBe('entry_unmatched');
  });

  it('keeps a quarantine whose line is no longer in the current revision', () => {
    const segments = buildSegments(
      detail({
        cases: [
          {
            id: 1,
            kind: 'parse_review',
            status: 'open',
            event_id: 99,
            line_id: null,
            entry_id: null,
            explanation: null,
            candidates: [],
            context: { quarantined_event_id: 99, equivalence: 'unpaired' },
            version: 1,
            created_at: '2026-09-21T00:00:00Z',
          },
        ],
      }),
      new Map(),
    );
    expect(segments.statementOnly.map((row) => row.title)).toEqual(['資料已變更，待確認']);
    expect(segments.statementOnly[0].quarantined).toBe(true);
  });
});
