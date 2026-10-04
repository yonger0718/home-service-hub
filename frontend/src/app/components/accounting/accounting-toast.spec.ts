import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingToastComponent, AccountingToastService, scheduleActionError } from './accounting-toast';

describe('AccountingToast', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows a message and clears it after the delay', () => {
    TestBed.configureTestingModule({ imports: [AccountingToastComponent] });
    const fixture = TestBed.createComponent(AccountingToastComponent);
    const toast = TestBed.inject(AccountingToastService);
    toast.show('匯入進行中，請稍後再試');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[role="status"]')?.textContent?.trim()).toBe('匯入進行中，請稍後再試');
    vi.advanceTimersByTime(3000);
    fixture.detectChanges();
    expect(el.querySelector('[role="status"]')).toBeNull();
  });

  it('turns import_running into the toast and anything else into an error line', () => {
    const toast = TestBed.inject(AccountingToastService);
    const running = new HttpErrorResponse({ status: 409, error: { message: 'import_running' } });
    expect(scheduleActionError(running, toast)).toBeNull();
    expect(toast.message()).toBe('匯入進行中，請稍後再試');
    const other = new HttpErrorResponse({ status: 409, error: { message: '只有進行中的排程可以暫停（目前 paused）' } });
    expect(scheduleActionError(other, toast)).toBe('只有進行中的排程可以暫停（目前 paused）');
  });
});
