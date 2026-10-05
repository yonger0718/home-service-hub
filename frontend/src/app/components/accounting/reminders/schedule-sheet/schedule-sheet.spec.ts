import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ScheduleDefinitionDetail } from '../../../../models/accounting.model';
import { AccountingToastService } from '../../accounting-toast';
import { makeDefinition, makeInstance } from '../../testing/fixtures';
import { ScheduleSheetComponent } from './schedule-sheet';

function detail(overrides: Partial<ScheduleDefinitionDetail> = {}): ScheduleDefinitionDetail {
  const base = makeDefinition({ id: 5, kind: 'installment', name: '信貸 每月還款', anchor_date: '2026-11-09', day_of_month: 9, times: 36 });
  const days = ['2026-11-09', '2026-12-09', '2027-01-09', '2027-02-09'];
  return {
    ...base,
    instances: days.map((day, index) =>
      makeInstance({ id: 100 + index, definition_id: 5, seq: index + 1, due_date: day, totals: [{ currency: 'TWD', amount: '-8953.0000' }] }),
    ),
    ...overrides,
  };
}

describe('ScheduleSheetComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<ScheduleSheetComponent>;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 3, 12, 0, 0));
    TestBed.configureTestingModule({
      imports: [ScheduleSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  function render(body: ScheduleDefinitionDetail): HTMLElement {
    fixture = TestBed.createComponent(ScheduleSheetComponent);
    fixture.componentRef.setInput('definitionId', 5);
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(body);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function click(el: HTMLElement, selector: string): void {
    (el.querySelector(selector) as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('shows the rule summary and the next three periods', () => {
    const el = render(detail());
    expect(text(el.querySelector('h3'))).toBe('信貸 每月還款');
    expect(text(el.querySelector('.rule-summary'))).toBe('每月 9號 · 36 期 · 自 2026/11/09');
    expect(Array.from(el.querySelectorAll('.next-period .date')).map(text)).toEqual(['11/09', '12/09', '01/09']);
    expect(text(el.querySelector('.next-period .amount'))).toBe('−$8,953');
  });

  it('pauses from the sheet', () => {
    // Spec "Pause from the sheet" (the row badge is checked in the reminder centre spec).
    const el = render(detail());
    click(el, '.sheet-pause');
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/5/pause').flush(makeDefinition({ id: 5, status: 'paused' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail({ status: 'paused' }));
    fixture.detectChanges();
    expect(text(el.querySelector('.badge.paused'))).toBe('已暫停');
    expect(el.querySelector('.sheet-resume')).not.toBeNull();
  });

  it('asks how to resume when paused periods are overdue', () => {
    const overdue = detail({
      status: 'paused',
      instances: [
        makeInstance({ id: 1, definition_id: 5, due_date: '2026-09-09' }),
        makeInstance({ id: 2, definition_id: 5, due_date: '2026-10-01' }),
        makeInstance({ id: 3, definition_id: 5, due_date: '2026-11-09' }),
      ],
    });
    const el = render(overdue);
    click(el, '.sheet-resume');
    http.expectNone(r => r.url.endsWith('/resume'));
    expect(text(el.querySelector('.resume-skip'))).toBe('略過期間的 2 期');
    click(el, '.resume-post');
    const req = http.expectOne('/api/accounting/schedules/definitions/5/resume');
    expect(req.request.body).toEqual({ backlog: 'post' });
    req.flush(makeDefinition({ id: 5 }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail());
  });

  it('switches 入帳方式', () => {
    const el = render(detail());
    click(el, '.sheet-mode[data-mode="confirm"]');
    const req = http.expectOne('/api/accounting/schedules/definitions/5/mode');
    expect([req.request.method, req.request.body]).toEqual(['PUT', { posting_mode: 'confirm' }]);
    req.flush(makeDefinition({ id: 5, posting_mode: 'confirm' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail({ posting_mode: 'confirm' }));
  });

  it('confirms before ending and says how many periods go', () => {
    const el = render(detail());
    click(el, '.sheet-end');
    expect(text(el.querySelector('.end-confirm'))).toBe('結束後未入帳的 4 期將刪除');
    http.expectNone(r => r.url.endsWith('/end'));
    click(el, '.sheet-end');
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/5/end').flush(makeDefinition({ id: 5, status: 'ended' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail({ status: 'ended', instances: [] }));
  });

  it('offers 刪除 only while nothing was posted, and closes after it', () => {
    const posted = render(detail({ posted_count: 2 }));
    expect(posted.querySelector('.sheet-delete')).toBeNull();
    fixture.destroy();
    const el = render(detail({ posted_count: 0 }));
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    click(el, '.sheet-delete');
    click(el, '.sheet-delete');
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/schedules/definitions/5').flush(null);
    fixture.detectChanges();
    expect(closed).toHaveBeenCalled();
  });

  it('closes on Esc and opens the entry form for 編輯', () => {
    const el = render(detail());
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    click(el, '.sheet-edit');
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { schedule: 5 } });
    el.querySelector('.sheet')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(closed).toHaveBeenCalledTimes(2);
  });

  it('offers neither 編輯 nor 入帳方式 changes for an ended schedule', () => {
    // Final review F3: an ended definition answers 409 definition_ended to a PUT and to …/mode.
    const el = render(detail({ status: 'ended', instances: [] }));
    expect(el.querySelector('.sheet-edit')).toBeNull();
    const modes = Array.from(el.querySelectorAll<HTMLButtonElement>('.sheet-mode'));
    expect(modes.length).toBe(2);
    expect(modes.every(button => button.disabled)).toBe(true);
    fixture.componentInstance.setMode('confirm');
    http.expectNone(r => r.url.endsWith('/mode'));
  });

  it('shows 金額格式不正確 for an empty amount and sends nothing', () => {
    // Final review F5: the period editor validates with the shared amount parser.
    const el = render(detail());
    click(el, '.next-period .period-edit');
    const input = el.querySelector('.period-editor input') as HTMLInputElement;
    input.value = '';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    click(el, '.period-save');
    expect(text(el.querySelector('.form-error'))).toBe('金額格式不正確');
    expect(el.querySelector('.scope-sheet')).toBeNull();
    http.expectNone(r => r.method === 'PUT');
  });

  it('moves focus into the sheet once loaded', async () => {
    const el = render(detail());
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);
    });
  });

  it('focuses the resume choice, and Esc closes it (returning focus to 繼續) before the sheet', async () => {
    const el = render(detail({ status: 'paused', instances: [makeInstance({ id: 1, definition_id: 5, due_date: '2026-09-09' })] }));
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    click(el, '.sheet-resume');
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(document.activeElement).toBe(el.querySelector('.resume-skip'));
    });
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    fixture.detectChanges();
    expect(el.querySelector('.resume-choice')).toBeNull();
    expect(closed).not.toHaveBeenCalled();
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(document.activeElement).toBe(el.querySelector('.sheet-resume'));
    });
    http.expectNone(r => r.url.endsWith('/resume'));
  });

  it('closes the end confirmation on Esc before the sheet', () => {
    const el = render(detail());
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    click(el, '.sheet-end');
    el.querySelector('.sheet-end')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    fixture.detectChanges();
    expect(el.querySelector('.end-confirm')).toBeNull();
    expect(closed).not.toHaveBeenCalled();
  });

  it('shows the toast on import_running and leaves the sheet unchanged', () => {
    const el = render(detail());
    click(el, '.sheet-pause');
    http
      .expectOne('/api/accounting/schedules/definitions/5/pause')
      .flush({ detail: 'import_running' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
    expect(el.querySelector('.form-error')).toBeNull();
    expect(el.querySelector('.sheet-pause')).not.toBeNull();
  });

  it('drops a stale load when the definition changes', () => {
    fixture = TestBed.createComponent(ScheduleSheetComponent);
    fixture.componentRef.setInput('definitionId', 5);
    fixture.detectChanges();
    const first = http.expectOne('/api/accounting/schedules/definitions/5');
    fixture.componentRef.setInput('definitionId', 6);
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/6').flush(detail({ id: 6, name: '房租' }));
    first.flush(detail());
    fixture.detectChanges();
    expect(text((fixture.nativeElement as HTMLElement).querySelector('h3'))).toBe('房租');
  });

  it('shows 金額格式不正確 for a malformed amount and sends nothing', () => {
    const el = render(detail());
    click(el, '.next-period .period-edit');
    const input = el.querySelector('.period-editor input') as HTMLInputElement;
    input.value = '4.1.2';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    click(el, '.period-save');
    expect(text(el.querySelector('.form-error'))).toBe('金額格式不正確');
    expect(el.querySelector('.scope-sheet')).toBeNull();
    http.expectNone(r => r.method === 'PUT');
  });

  it('focuses the sheet before the load answers, so Esc closes it at once', async () => {
    fixture = TestBed.createComponent(ScheduleSheetComponent);
    fixture.componentRef.setInput('definitionId', 5);
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(document.activeElement).toBe(el.querySelector('.sheet'));
    });
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    expect(closed).toHaveBeenCalledTimes(1);
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail());
  });

  it('ignores an action answer that lands after the sheet closed', () => {
    const el = render(detail({ posted_count: 0 }));
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    click(el, '.sheet-delete');
    click(el, '.sheet-delete');
    const req = http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/schedules/definitions/5');
    (el.querySelector('.overlay') as HTMLElement).click();
    expect(closed).toHaveBeenCalledTimes(1);
    fixture.destroy();
    expect(() => req.flush(null)).not.toThrow();
    expect(closed).toHaveBeenCalledTimes(1);
    // No reload of a destroyed sheet (http.verify in afterEach).
  });

  describe('period amounts and 套用範圍 (proposal decision 24)', () => {
    function openEditor(el: HTMLElement): HTMLInputElement {
      click(el, '.next-period .period-edit');
      return el.querySelector('.period-editor input') as HTMLInputElement;
    }

    function type(input: HTMLInputElement, value: string): void {
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    }

    it('asks 套用範圍 only when an amount changed', () => {
      const el = render(detail());
      openEditor(el);
      click(el, '.period-save');
      expect(el.querySelector('.period-editor')).toBeNull();
      expect(el.querySelector('.scope-sheet')).toBeNull();
      http.expectNone(r => r.method === 'PUT');
      type(openEditor(el), '420');
      click(el, '.period-save');
      expect(text(el.querySelector('.scope-sheet h4'))).toBe('套用範圍');
      expect(Array.from(el.querySelectorAll('.scope-sheet button')).map(text)).toEqual(['僅這一期', '這一期與之後', '全部週期']);
      http.expectNone(r => r.method === 'PUT');
    });

    for (const [selector, scope] of [
      ['.scope-this', 'this'],
      ['.scope-following', 'following'],
      ['.scope-all', 'all'],
    ] as const) {
      it(`sends scope ${scope} from ${selector}`, () => {
        // Spec "Price change from this period on" (following) and its siblings.
        const el = render(detail());
        type(openEditor(el), '420');
        click(el, '.period-save');
        click(el, selector);
        const req = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/instances/100');
        expect(req.request.body).toEqual({ amounts: ['420'], scope });
        req.flush(makeInstance({ id: 100, definition_id: 5, amounts: ['420'] }));
        fixture.detectChanges();
        http.expectOne('/api/accounting/schedules/definitions/5').flush(detail());
        fixture.detectChanges();
        expect(el.querySelector('.scope-sheet')).toBeNull();
        expect(el.querySelector('.period-editor')).toBeNull();
      });
    }

    it('checks the draft again before sending: reverted to the period amount sends nothing', () => {
      const el = render(detail());
      const input = openEditor(el);
      type(input, '420');
      click(el, '.period-save');
      expect(el.querySelector('.scope-sheet')).not.toBeNull();
      type(el.querySelector('.period-editor input') as HTMLInputElement, '390');
      click(el, '.scope-all');
      http.expectNone(r => r.method === 'PUT');
      expect(el.querySelector('.scope-sheet')).toBeNull();
      expect(el.querySelector('.period-editor')).toBeNull();
    });

    it('checks the draft again before sending: malformed shows 金額格式不正確', () => {
      const el = render(detail());
      type(openEditor(el), '420');
      click(el, '.period-save');
      type(el.querySelector('.period-editor input') as HTMLInputElement, 'abc');
      click(el, '.scope-this');
      http.expectNone(r => r.method === 'PUT');
      expect(text(el.querySelector('.form-error'))).toBe('金額格式不正確');
      expect(el.querySelector('.scope-sheet')).toBeNull();
    });

    it('focuses 僅這一期, and Esc cancels without saving or closing the sheet', async () => {
      // Spec "Scope question cancelled".
      const el = render(detail());
      const closed = vi.fn();
      fixture.componentInstance.closed.subscribe(closed);
      type(openEditor(el), '420');
      click(el, '.period-save');
      await vi.waitFor(() => {
        fixture.detectChanges();
        expect(document.activeElement).toBe(el.querySelector('.scope-sheet .scope-this'));
      });
      el.querySelector('.scope-sheet')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(el.querySelector('.scope-sheet')).toBeNull();
      expect(el.querySelector('.period-editor')).not.toBeNull();
      expect(closed).not.toHaveBeenCalled();
      http.expectNone(r => r.method === 'PUT');
    });
  });
});
