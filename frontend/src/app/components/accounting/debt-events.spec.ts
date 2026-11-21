import { describe, expect, it } from 'vitest';

import { DebtEvent } from '../../models/accounting.model';
import { debtActorLabel, debtEventLine } from './debt-events';

function event(fields: Partial<DebtEvent> = {}): DebtEvent {
  return {
    id: 1, action: 'close', actor: 'hermes', open_amount: '220.0000', currency: 'TWD', event_date: '2026-11-20',
    needs_review: false, ...fields,
  };
}

describe('debt event wording (#401)', () => {
  it('names the two known callers and keeps any other label', () => {
    expect([debtActorLabel('spa'), debtActorLabel('hermes'), debtActorLabel('ops'), debtActorLabel(null)]).toEqual([
      '網頁', 'Hermes', 'ops', '',
    ]);
  });

  it('keeps a label that happens to be the name of an object member', () => {
    expect(['constructor', '__proto__', 'toString', 'hasOwnProperty'].map(debtActorLabel)).toEqual([
      'constructor', '__proto__', 'toString', 'hasOwnProperty',
    ]);
  });

  it('says what a close took out and what a reopen brought back', () => {
    expect(debtEventLine(event())).toBe('2026/11/20 標記結清 · Hermes · 當時剩餘 $220');
    expect(debtEventLine(event({ action: 'reopen', actor: 'spa' }))).toBe('2026/11/20 重新開啟 · 網頁 · 回到剩餘 $220');
  });

  it('leaves out an amount of zero and a missing actor', () => {
    expect(debtEventLine(event({ open_amount: '0.0000' }))).toBe('2026/11/20 標記結清 · Hermes');
    expect(debtEventLine(event({ actor: null }))).toBe('2026/11/20 標記結清 · 當時剩餘 $220');
    expect(debtEventLine(event({ action: 'reopen', actor: null, open_amount: '0.0000' }))).toBe('2026/11/20 重新開啟');
  });

  it('marks the close that waits for the owner', () => {
    expect(debtEventLine(event({ needs_review: true }))).toBe('2026/11/20 標記結清 · Hermes · 當時剩餘 $220 · 待確認');
  });
});
