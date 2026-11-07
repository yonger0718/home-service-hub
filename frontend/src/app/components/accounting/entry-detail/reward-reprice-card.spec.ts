import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EntryDetail, RewardReprice, RewardRepriceItem } from '../../../models/accounting.model';
import { reviewTitle } from '../review-reason';
import { AppEntryRewardRepriceCardComponent } from './reward-reprice-card';

const URL = '/api/accounting/entries/42/reward-reprice';

function item(fields: Partial<RewardRepriceItem>): RewardRepriceItem {
  return { accrual_id: 1, rule_id: 9, rule_name: '基本回饋', reward_entry_id: 77, booked: '10', new: '20', status: 'reprice', ...fields };
}

function preview(items: RewardRepriceItem[]): RewardReprice {
  return { entry_id: 42, flagged: true, items, applied: [] };
}

describe('AppEntryRewardRepriceCardComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<AppEntryRewardRepriceCardComponent>;
  let changed: number;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppEntryRewardRepriceCardComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    changed = 0;
  });

  afterEach(() => http.verify());

  function render(fields: Record<string, unknown>): HTMLElement {
    fixture = TestBed.createComponent(AppEntryRewardRepriceCardComponent);
    fixture.componentRef.setInput('detail', { id: 42, locked: false, review_reason: 'reward_posted', ...fields } as unknown as EntryDetail);
    fixture.componentRef.setInput('amountText', (_id: number, value: string) => `${value} 點`);
    fixture.componentInstance.changed.subscribe(() => changed++);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function loaded(fields: Record<string, unknown>, body: RewardReprice): HTMLElement {
    const root = render(fields);
    http.expectOne(URL).flush(body);
    fixture.detectChanges();
    return root;
  }

  function button(root: HTMLElement, text: string): HTMLButtonElement | undefined {
    return [...root.querySelectorAll('button')].find(b => b.textContent?.includes(text)) as HTMLButtonElement | undefined;
  }

  it('stays hidden and reads nothing without the reward_posted flag', () => {
    const root = render({ review_reason: null });
    expect(root.textContent?.trim()).toBe('');
    http.expectNone(URL);
  });

  it('lists booked → new and reprices on tap', () => {
    const root = loaded({}, preview([item({}), item({ accrual_id: 2, rule_name: '自動扣繳', booked: '5', new: '5', status: 'same' })]));
    expect(root.textContent).toContain('回饋已入帳，消費金額已改');
    expect(root.textContent).toContain('基本回饋');
    expect(root.textContent).toContain('已入帳 10 點 → 新算 20 點');
    expect(root.textContent).toContain('已入帳 5 點，金額不變');
    button(root, '依新金額重算回饋')!.click();
    const req = http.expectOne(URL);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ keep: false });
    req.flush({ ...preview([]), flagged: false, applied: [1] });
    fixture.detectChanges();
    expect(changed).toBe(1);
    expect(button(root, '依新金額重算回饋')!.disabled).toBe(true);
  });

  it('保持原樣 posts keep and hides 重算 when nothing would change', () => {
    const root = loaded({}, preview([
      item({ status: 'row_edited', new: null }),
      item({ accrual_id: 2, status: 'confirmed', new: null }),
      item({ accrual_id: 3, status: 'window_moved', new: null }),
      item({ accrual_id: 4, status: 'rule_unlinked', new: null }),
      item({ accrual_id: 5, status: 'zero', new: '0' }),
    ]));
    expect(button(root, '依新金額重算回饋')).toBeUndefined();
    expect(root.textContent).toContain('回饋記錄已手動修改，不改');
    expect(root.textContent).toContain('銀行確認 10 點，以銀行為準');
    expect(root.textContent).toContain('消費已改到別期，請刪除該筆回饋或保持原樣');
    expect(root.textContent).toContain('這筆已不套用此規則');
    expect(root.textContent).toContain('已超過上限');
    expect(root.querySelector('.reprice-hint')).not.toBeNull();
    button(root, '保持原樣')!.click();
    const req = http.expectOne(URL);
    expect(req.request.body).toEqual({ keep: true });
    req.flush({ ...preview([]), flagged: false });
    expect(changed).toBe(1);
  });

  it('no hint when every row is settled, and a reloaded detail re-reads the preview', () => {
    const root = loaded({}, preview([item({ status: 'row_edited', new: null }), item({ accrual_id: 2, status: 'confirmed', new: null })]));
    expect(root.querySelector('.reprice-hint')).toBeNull();
    fixture.componentRef.setInput('detail', { id: 42, locked: false, review_reason: 'reward_posted' } as unknown as EntryDetail);
    fixture.detectChanges();
    http.expectOne(URL).flush(preview([item({ status: 'window_moved', new: null })]));
    fixture.detectChanges();
    expect(root.textContent).toContain('消費已改到別期，請刪除該筆回饋或保持原樣');
    expect(root.querySelector('.reprice-hint')).not.toBeNull();
  });

  it('a locked entry shows the lines without buttons', () => {
    const root = loaded({ locked: true }, preview([item({})]));
    expect(root.textContent).toContain('已入帳 10 點 → 新算 20 點');
    expect(root.querySelectorAll('button').length).toBe(0);
  });

  it('a failed read offers a retry; a failed write shows an error and re-enables', () => {
    const root = render({});
    http.expectOne(URL).flush('x', { status: 500, statusText: 'err' });
    fixture.detectChanges();
    expect(root.textContent).toContain('讀取失敗');
    button(root, '重新讀取')!.click();
    http.expectOne(URL).flush(preview([item({})]));
    fixture.detectChanges();
    button(root, '依新金額重算回饋')!.click();
    http.expectOne(URL).flush('x', { status: 409, statusText: 'locked' });
    fixture.detectChanges();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('重算回饋失敗');
    expect(button(root, '依新金額重算回饋')!.disabled).toBe(false);
    expect(changed).toBe(0);
  });
});

describe('reviewTitle', () => {
  it('names the reward_posted reason, else the generic text', () => {
    expect(reviewTitle({ review_reason: 'reward_posted' })).toContain('回饋已入帳');
    expect(reviewTitle({ review_reason: null })).toBe('請確認這筆記錄');
    expect(reviewTitle({})).toBe('請確認這筆記錄');
  });
});
