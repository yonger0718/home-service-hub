import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { InboxCard } from '../inbox-view';
import { InboxCandidate, InboxItem } from '../inbox.model';
import { button, makeCard, makeInboxAccount, makeItem, text } from '../testing';
import { AppInboxMatchSheetComponent, InboxMatchResult } from './match-sheet';

function candidate(entryId: number, date: string, title: string, amount: string): InboxCandidate {
  return { entry_id: entryId, date, title, amount, reason: null, residual: null, date_distance: 0 };
}

function powerMember(caseId: number, date: string, candidates: InboxCandidate[]): InboxItem {
  return makeItem({
    case_id: caseId,
    question: 'ambiguous',
    group_key: 'g-power',
    line: { id: 60 + caseId, date, merchant: '台電', amount: '-1300.0000', line_kind: 'purchase' },
    candidates,
    actions: ['match_candidate', 'dismiss'],
  });
}

describe('AppInboxMatchSheetComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppInboxMatchSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(card: InboxCard) {
    const fixture = TestBed.createComponent(AppInboxMatchSheetComponent);
    fixture.componentRef.setInput('card', card);
    fixture.componentRef.setInput('account', card.account);
    const confirmed = vi.fn<(result: InboxMatchResult) => void>();
    const closed = vi.fn();
    fixture.componentInstance.confirmed.subscribe(confirmed);
    fixture.componentInstance.closed.subscribe(closed);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, confirmed, closed };
  }

  function rows(el: HTMLElement, section: '.candidates' | '.others'): HTMLLabelElement[] {
    return Array.from(el.querySelectorAll<HTMLLabelElement>(`${section} label.match-row`));
  }

  function choose(fixture: ComponentFixture<AppInboxMatchSheetComponent>, row: HTMLLabelElement) {
    row.querySelector<HTMLInputElement>('input[type="radio"]')!.click();
    fixture.detectChanges();
  }

  it('lists candidates and emits the picked entry and title per case', () => {
    const card = makeCard([
      powerMember(1, '2026-08-05', [
        candidate(3, '2026-08-05', '電費', '-1300.0000'),
        candidate(4, '2026-08-07', '電費 8 月', '-1300.0000'),
      ]),
      powerMember(2, '2026-09-05', [candidate(6, '2026-09-04', '電費 9 月', '-1300.0000')]),
    ]);
    const { fixture, el, confirmed, closed } = render(card);
    expect(el.querySelector('.overlay')!.hasAttribute('data-overlay')).toBe(true);
    expect(text(el.querySelector('.step'))).toBe('第 1/2 筆');
    expect(text(el.querySelector('.match-line'))).toBe('2026/08/05 台電 −$1,300');
    expect(rows(el, '.candidates').map(text)).toEqual([
      '2026/08/05 電費 −$1,300',
      '2026/08/07 電費 8 月 −$1,300',
    ]);
    // Nothing picked: no way forward.
    expect(button(el, '下一筆').disabled).toBe(true);
    expect(button(el, '確定')).toBeUndefined();

    choose(fixture, rows(el, '.candidates')[1]);
    button(el, '下一筆').click();
    fixture.detectChanges();

    expect(text(el.querySelector('.step'))).toBe('第 2/2 筆');
    expect(text(el.querySelector('.match-line'))).toBe('2026/09/05 台電 −$1,300');
    expect(rows(el, '.candidates').map(text)).toEqual(['2026/09/04 電費 9 月 −$1,300']);
    expect(button(el, '確定').disabled).toBe(true);
    // 上一筆 keeps the first pick.
    button(el, '上一筆').click();
    fixture.detectChanges();
    expect(rows(el, '.candidates')[1].querySelector<HTMLInputElement>('input')!.checked).toBe(true);
    button(el, '下一筆').click();
    fixture.detectChanges();

    choose(fixture, rows(el, '.candidates')[0]);
    button(el, '確定').click();
    expect(confirmed).toHaveBeenCalledTimes(1);
    expect(confirmed).toHaveBeenCalledWith({
      entry_ids: { '1': 4, '2': 6 },
      titles: { '1': '電費 8 月', '2': '電費 9 月' },
    });

    button(el, '取消').click();
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it('search calls the search route and appends its rows', () => {
    const item = makeItem({
      case_id: 8,
      candidates: [candidate(3, '2026-09-03', '全家', '-120.0000')],
    });
    const card = makeCard([item], makeInboxAccount({ id: 2, currency: 'TWD' }));
    const { fixture, el, confirmed } = render(card);
    // A single card has no step counter, and 確定 at once.
    expect(el.querySelector('.step')).toBeNull();
    expect(el.querySelector('.others')).toBeNull();

    button(el, '搜尋這個帳戶的記錄').click();
    fixture.detectChanges();
    const req = http.expectOne(
      (r) => r.method === 'GET' && r.url === '/api/accounting/reconciliation/inbox/cases/8/search',
    );
    expect(req.request.params.get('days')).toBe('14');
    req.flush([
      candidate(3, '2026-09-03', '全家', '-120.0000'),
      candidate(9, '2026-09-10', '7-11 早餐', '-120.0000'),
      candidate(10, '2026-08-28', '超商', '-118.0000'),
    ]);
    fixture.detectChanges();

    expect(text(el.querySelector('.others h4'))).toBe('其他記錄');
    // The entry already listed as a candidate is not repeated.
    expect(rows(el, '.candidates').map(text)).toEqual(['2026/09/03 全家 −$120']);
    expect(rows(el, '.others').map(text)).toEqual([
      '2026/09/10 7-11 早餐 −$120',
      '2026/08/28 超商 −$118',
    ]);
    expect(button(el, '搜尋這個帳戶的記錄')).toBeUndefined();

    choose(fixture, rows(el, '.others')[0]);
    button(el, '確定').click();
    expect(confirmed).toHaveBeenCalledWith({ entry_ids: { '8': 9 }, titles: { '8': '7-11 早餐' } });
  });

  it('a failed search shows the error and keeps the search button for a retry', () => {
    const item = makeItem({ case_id: 8, candidates: [] });
    const { fixture, el } = render(makeCard([item], makeInboxAccount({ id: 2, currency: 'TWD' })));
    const SEARCH_URL = '/api/accounting/reconciliation/inbox/cases/8/search';

    button(el, '搜尋這個帳戶的記錄').click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.method === 'GET' && r.url === SEARCH_URL)
      .flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.search-error')).not.toBeNull();
    const retry = button(el, '搜尋這個帳戶的記錄');
    expect(retry).toBeDefined();
    expect(retry.disabled).toBe(false);

    retry.click();
    fixture.detectChanges();
    http
      .expectOne((r) => r.method === 'GET' && r.url === SEARCH_URL)
      .flush([candidate(9, '2026-09-10', '7-11 早餐', '-120.0000')]);
    fixture.detectChanges();
    expect(el.querySelector('.search-error')).toBeNull();
    expect(rows(el, '.others').map(text)).toEqual(['2026/09/10 7-11 早餐 −$120']);
    expect(button(el, '搜尋這個帳戶的記錄')).toBeUndefined();
  });
});
