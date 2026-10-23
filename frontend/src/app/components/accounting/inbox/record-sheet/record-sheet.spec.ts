import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CategoryNode, Project } from '../../../../models/accounting.model';
import { makeCategory } from '../../testing/fixtures';
import { InboxCard } from '../inbox-view';
import { InboxItem } from '../inbox.model';
import { button, categoryTile, makeCard, makeItem, text } from '../testing';
import { AppInboxRecordSheetComponent, InboxRecordResult } from './record-sheet';

const CATEGORIES_URL = '/api/accounting/categories';
const PROJECTS_URL = '/api/accounting/projects';

const EXPENSE: CategoryNode[] = [
  makeCategory({
    id: 1,
    name: '飲食',
    children: [
      makeCategory({ id: 11, parent_id: 1, name: '早餐' }),
      makeCategory({ id: 12, parent_id: 1, name: '午餐' }),
    ],
  }),
  makeCategory({ id: 2, name: '交通' }),
];

const PROJECTS: Project[] = [
  { id: 4, name: '日本旅行', is_archived: false, sort_order: 0, moze_id: null },
  { id: 5, name: '舊專案', is_archived: true, sort_order: 1, moze_id: null },
];

function familyMember(caseId: number, date: string): InboxItem {
  return makeItem({
    case_id: caseId,
    group_key: 'g-family',
    line: {
      id: 50 + caseId,
      date,
      merchant: '全家便利商店',
      amount: '-120.0000',
      line_kind: 'purchase',
    },
  });
}

describe('AppInboxRecordSheetComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppInboxRecordSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(card: InboxCard, kind: 'expense' | 'income' = 'expense', categories = EXPENSE) {
    const fixture = TestBed.createComponent(AppInboxRecordSheetComponent);
    fixture.componentRef.setInput('card', card);
    fixture.componentRef.setInput('currency', 'TWD');
    const confirmed = vi.fn<(result: InboxRecordResult) => void>();
    const closed = vi.fn();
    fixture.componentInstance.confirmed.subscribe(confirmed);
    fixture.componentInstance.closed.subscribe(closed);
    fixture.detectChanges();
    http
      .expectOne(
        (r) => r.method === 'GET' && r.url === CATEGORIES_URL && r.params.get('kind') === kind,
      )
      .flush(categories);
    http.expectOne((r) => r.method === 'GET' && r.url === PROJECTS_URL).flush(PROJECTS);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, confirmed, closed };
  }

  function type(
    fixture: ComponentFixture<AppInboxRecordSheetComponent>,
    input: HTMLInputElement,
    value: string,
  ) {
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  it('emits category, name and learn on 確定', () => {
    const { fixture, el, confirmed, closed } = render(makeCard([makeItem()]));
    expect(text(el.querySelector('.sheet h3'))).toBe('記為支出');
    expect(el.querySelector('.overlay')!.hasAttribute('data-overlay')).toBe(true);
    expect(el.querySelector('.sheet')!.getAttribute('role')).toBe('dialog');

    categoryTile(el, '飲食').click();
    fixture.detectChanges();
    categoryTile(el, '午餐').click();
    fixture.detectChanges();

    const name = el.querySelector<HTMLInputElement>('input.record-name')!;
    expect(name.placeholder).toBe('全家便利商店');
    type(fixture, name, '  早午餐  ');

    const project = el.querySelector<HTMLSelectElement>('select.record-project')!;
    // An empty default, then the open projects only.
    expect(Array.from(project.options).map((option) => [option.value, text(option)])).toEqual([
      ['', ''],
      ['4', '日本旅行'],
    ]);
    expect(project.value).toBe('');
    project.value = '4';
    project.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    el.querySelector<HTMLInputElement>('.learn input')!.click();
    fixture.detectChanges();
    expect(text(el.querySelector('.learn'))).toBe('以後「全家便利商店」都這樣');

    button(el, '確定').click();
    expect(confirmed).toHaveBeenCalledTimes(1);
    expect(confirmed).toHaveBeenCalledWith({
      category_id: 12,
      category_name: '午餐',
      name: '早午餐',
      project_id: 4,
      learn: { amount_exact: true },
    });

    button(el, '取消').click();
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it('確定 disabled without a category', () => {
    const income = makeItem({
      case_id: 3,
      line: {
        id: 53,
        date: '2026-09-20',
        merchant: '利息',
        amount: '35.0000',
        line_kind: 'interest',
      },
      rule_hint: null,
    });
    const incomeTree = [makeCategory({ id: 30, kind: 'income', name: '利息收入' })];
    const { fixture, el, confirmed } = render(makeCard([income]), 'income', incomeTree);
    expect(text(el.querySelector('.sheet h3'))).toBe('記為收入');
    // No rule hint: no learn controls at all.
    expect(el.querySelector('.learn')).toBeNull();

    const confirm = button(el, '確定');
    expect(confirm.disabled).toBe(true);
    confirm.click();
    expect(confirmed).not.toHaveBeenCalled();

    categoryTile(el, '利息收入').click();
    fixture.detectChanges();
    expect(confirm.disabled).toBe(false);
    confirm.click();
    // An empty name and no project are null.
    expect(confirmed).toHaveBeenCalledWith({
      category_id: 30,
      category_name: '利息收入',
      name: null,
      project_id: null,
      learn: null,
    });
  });

  it('presets the category from default_category_id', () => {
    const item = makeItem({
      rule_hint: {
        merchant_pattern: '全家便利商店',
        line_kind: 'purchase',
        flow_sign: '-',
        amount: '120.0000',
        default_category_id: 11,
      },
    });
    const { el, confirmed } = render(makeCard([item]));
    // A child of 飲食, found in the loaded tree: the picker shows it collapsed in its strip.
    expect(el.querySelector('app-category-picker .strip .sel')!.getAttribute('aria-label')).toBe(
      '更改類別 早餐',
    );
    const confirm = button(el, '確定');
    expect(confirm.disabled).toBe(false);
    confirm.click();
    expect(confirmed).toHaveBeenCalledWith({
      category_id: 11,
      category_name: '早餐',
      name: null,
      project_id: null,
      learn: null,
    });
  });

  it('merged card: learn ticked and no 金額不同也算; single card: unticked with the sub-option', () => {
    const merged = render(makeCard([familyMember(1, '2026-09-03'), familyMember(2, '2026-09-10')]));
    const mergedLearn = merged.el.querySelector<HTMLInputElement>('.learn input')!;
    expect(mergedLearn.checked).toBe(true);
    expect(mergedLearn.disabled).toBe(false);
    expect(merged.el.querySelector('.amount-any')).toBeNull();
    expect(text(merged.el.querySelector('.amount-locked'))).toBe('同金額 ×2');
    categoryTile(merged.el, '交通').click();
    merged.fixture.detectChanges();
    button(merged.el, '確定').click();
    expect(merged.confirmed).toHaveBeenCalledWith(
      expect.objectContaining({ category_id: 2, learn: { amount_exact: true } }),
    );
    // Still editable: unticked, the merged answer teaches nothing.
    mergedLearn.click();
    merged.fixture.detectChanges();
    button(merged.el, '確定').click();
    expect(merged.confirmed).toHaveBeenLastCalledWith(expect.objectContaining({ learn: null }));

    const single = render(makeCard([makeItem()]));
    const singleLearn = single.el.querySelector<HTMLInputElement>('.learn input')!;
    const amountAny = single.el.querySelector<HTMLInputElement>('.amount-any input')!;
    expect(singleLearn.checked).toBe(false);
    expect(single.el.querySelector('.amount-locked')).toBeNull();
    expect(text(single.el.querySelector('.amount-any'))).toBe('金額不同也算');
    expect(amountAny.checked).toBe(false);
    expect(amountAny.disabled).toBe(true);
    singleLearn.click();
    single.fixture.detectChanges();
    expect(amountAny.disabled).toBe(false);
    amountAny.click();
    single.fixture.detectChanges();
    categoryTile(single.el, '交通').click();
    single.fixture.detectChanges();
    button(single.el, '確定').click();
    expect(single.confirmed).toHaveBeenCalledWith(
      expect.objectContaining({ category_id: 2, learn: { amount_exact: false } }),
    );
  });
});
