import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { Component } from '@angular/core';
import { describe, expect, it } from 'vitest';

import { makeEinvoice } from '../../accounting/inbox/testing';
import {
  EinvoiceFlowSession,
  afterRead,
  answer,
  move,
  progress,
  resume,
  skip,
  startFlow,
} from './einvoice-flow-session';

const inv = (id: number, state: 'ambiguous' | 'near' | 'unmatched' = 'ambiguous') => makeEinvoice({ id, state });

describe('e-invoice flow state', () => {
  it('starts on the first listed ambiguous / near invoice; unmatched ones are not in the flow', () => {
    const s = startFlow([inv(1), inv(9, 'unmatched'), inv(2, 'near'), inv(3)]);
    expect(s.order).toEqual([1, 2, 3]);
    expect(s.cursor).toBe(1);
    expect(progress(s)).toEqual({ position: 1, total: 3, handled: 0, skipped: 0 });
  });

  it('an answer moves on: 2 / 3; the last answer ends the flow', () => {
    let s = answer(startFlow([inv(1), inv(2), inv(3)]), 1, 'linked');
    expect(s.cursor).toBe(2);
    expect(progress(s)).toEqual({ position: 2, total: 3, handled: 1, skipped: 0 });
    s = answer(answer(s, 2, 'dismissed'), 3, 'recorded');
    expect(s.cursor).toBeNull();
    expect([...s.outcome.values()]).toEqual(['linked', 'dismissed', 'recorded']);
  });

  it('skip goes to the next unskipped one, then the end; resume clears the skips and starts at the first open', () => {
    let s = skip(startFlow([inv(1), inv(2)]));
    expect(s.cursor).toBe(2);
    s = answer(s, 2, 'linked');
    expect(s.cursor).toBeNull();
    expect(progress(s)).toMatchObject({ handled: 1, skipped: 1 });
    s = resume(s);
    expect(s.cursor).toBe(1);
    expect(s.skipped.size).toBe(0);
  });

  it('← → step through the open ones (skipped included) and stop at the ends', () => {
    let s = startFlow([inv(1), inv(2), inv(3)]);
    expect(move(s, -1)).toBe(s);
    s = move(s, 1);
    expect(s.cursor).toBe(2);
    s = answer(s, 2, 'linked');
    expect(s.cursor).toBe(3);
    expect(move(s, -1).cursor).toBe(1);
    expect(move(s, 1)).toBe(s);
  });

  it('a later read drops invoices answered elsewhere (not counted as handled) and never adds new ones', () => {
    let s = startFlow([inv(1), inv(2), inv(3)]);
    s = afterRead(s, [inv(2), inv(3), inv(4)]);
    expect(s.order).toEqual([2, 3]);
    expect(s.cursor).toBe(2);
    expect(progress(s)).toEqual({ position: 1, total: 2, handled: 0, skipped: 0 });
  });

  it('a later read keeps invoices answered here even though they are still listed (記成新的一筆 waits for the matcher)', () => {
    let s = answer(startFlow([inv(1), inv(2)]), 1, 'recorded');
    s = afterRead(s, [inv(1), inv(2)]);
    expect(s.order).toEqual([1, 2]);
    expect(s.cursor).toBe(2);
    expect(s.outcome.get(1)).toBe('recorded');
  });

  it('a later read refreshes the open invoices\' data (candidates may change)', () => {
    const s = afterRead(startFlow([inv(1)]), [makeEinvoice({ id: 1, seller_name: '測試新名' })]);
    expect(s.open.get(1)?.seller_name).toBe('測試新名');
  });
});

@Component({ template: '' })
class Blank {}

describe('EinvoiceFlowSession', () => {
  it('a pending 記成新的一筆 survives the entry form round trip and is dropped by any other navigation', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: '**', component: Blank }])],
    });
    const session = TestBed.inject(EinvoiceFlowSession);
    const router = TestBed.inject(Router);
    session.state.set(startFlow([inv(1)]));
    session.awaitRecord(1, 4);
    await router.navigateByUrl('/accounting/entry?kind=expense');
    await router.navigateByUrl('/notifications/einvoices');
    expect(session.pendingRecord()).toEqual({ id: 1, mark: 4 });
    await router.navigateByUrl('/accounting/entry');
    await router.navigateByUrl('/portfolio');
    expect(session.pendingRecord()).toBeNull();
  });
});
