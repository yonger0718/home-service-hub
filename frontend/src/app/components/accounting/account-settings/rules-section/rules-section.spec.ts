import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RewardRule, RewardUsage } from '../../../../models/accounting.model';
import { SwitchComponent } from '../../../ui/switch/switch';
import { postingSummary } from '../../reward-rule-text';
import { AccountRulesComponent } from './rules-section';

const TODAY = '2026-11-10';

function rule(overrides: Partial<RewardRule>): RewardRule {
  return {
    id: 1,
    account_id: 10,
    name: '測試規則',
    method: 'percent',
    rate: '1',
    fixed_amount: null,
    unit_spend: null,
    window: 'statement_cycle',
    posting: 'after_window',
    delay_days: 0,
    post_month_offset: 1,
    post_day: 5,
    txn_rounding: 'keep',
    total_rounding: 'round',
    total_cap: null,
    shared_cap_id: null,
    is_basic: false,
    reward_account_id: null,
    reward_project_id: null,
    starts_on: null,
    ends_on: null,
    is_enabled: true,
    description: null,
    sort_order: 0,
    moze_id: null,
    ...overrides,
  };
}

function usage(overrides: Partial<RewardUsage>): RewardUsage {
  return {
    rule_ids: [3],
    shared_cap_id: null,
    name: '玩數位 3%',
    window_start: '2026-10-26',
    window_end: '2026-11-25',
    used: '180',
    estimated: '13',
    cap: '200',
    remaining: '7',
    reward_account_id: 10,
    reward_account_name: '測試卡',
    reward_is_money: true,
    ...overrides,
  };
}

const A = rule({ id: 1, name: '一般消費 1%', rate: '1', is_basic: true, moze_id: 'mz-1', sort_order: 0 });
const B = rule({ id: 2, name: '海外消費 2.5%', rate: '2.5', is_enabled: false, sort_order: 1 });
const C = rule({ id: 3, name: '玩數位 3%', rate: '3', total_cap: '200', posting: 'after_transaction', delay_days: 30, sort_order: 2 });

@Component({
  imports: [AccountRulesComponent],
  template: `
    <app-account-rules
      [rules]="rules()"
      [usage]="usage()"
      [today]="today()"
      [busy]="busy()"
      (open)="onOpen($event)"
      (toggle)="onToggle($event)"
    />
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class HostComponent {
  readonly rules = signal<RewardRule[]>([A, B, C]);
  readonly usage = signal<ReadonlyMap<number, RewardUsage>>(new Map([[3, usage({})]]));
  readonly today = signal(TODAY);
  readonly busy = signal(false);
  onOpen = vi.fn<(rule: RewardRule) => void>();
  onToggle = vi.fn<(event: { rule: RewardRule; enabled: boolean }) => void>();
}

describe('AccountRulesComponent', () => {
  let fixture: ComponentFixture<HostComponent>;
  let host: HostComponent;

  beforeEach(() => {
    fixture = TestBed.createComponent(HostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  const root = () => fixture.nativeElement as HTMLElement;
  const rowOf = (id: number) => root().querySelector<HTMLElement>(`.rule-row[data-rule-id="${id}"]`)!;
  const text = (el: Element | null) => el?.textContent?.trim() ?? null;
  function update(fn: () => void): void {
    fn();
    fixture.detectChanges();
  }

  it('renders one .rule-row per rule in input order with data-rule-id', () => {
    const rows = Array.from(root().querySelectorAll('.rule-row'));
    expect(rows.map(r => r.getAttribute('data-rule-id'))).toEqual(['1', '2', '3']);
  });

  it('title drops the repeated rate and the chip carries it', () => {
    expect(text(rowOf(3).querySelector('.rule-label'))).toBe('玩數位');
    expect(text(rowOf(3).querySelector('.rule-rate'))).toBe('3%');
  });

  it('an active non-basic rule has no state tag', () => {
    expect(rowOf(3).querySelector('.rule-state')).toBeNull();
    expect(rowOf(3).querySelector('.rule-basic')).toBeNull();
  });

  it('基本 and 來自 MOZE tags', () => {
    expect(text(rowOf(1).querySelector('.rule-basic'))).toBe('基本');
    expect(text(rowOf(1).querySelector('.rule-moze'))).toBe('來自 MOZE');
    expect(rowOf(1).querySelector('.rule-state')).toBeNull();
  });

  it('a disabled 基本 rule shows 已停用 only', () => {
    update(() => host.rules.set([rule({ id: 5, name: '測試基本 1%', is_basic: true, is_enabled: false })]));
    const row = rowOf(5);
    expect(row.querySelectorAll('.badge').length).toBe(1);
    expect(text(row.querySelector('.rule-state'))).toBe('已停用');
    expect(row.querySelector('.rule-basic')).toBeNull();
  });

  it('an expired rule\'s tag carries data-state="expired" and .warn', () => {
    update(() => host.rules.set([rule({ id: 6, ends_on: '2026-10-31' })]));
    const tag = rowOf(6).querySelector('.rule-state')!;
    expect(tag.getAttribute('data-state')).toBe('expired');
    expect(tag.classList).toContain('warn');
  });

  it('an active rule without a cap still shows the cap bar\'s 無上限 line', () => {
    update(() => host.usage.set(new Map([[3, usage({ cap: null, remaining: null, used: '40', estimated: '5' })]])));
    const bar = rowOf(3).querySelector('app-cap-bar');
    expect(bar).not.toBeNull();
    expect(text(bar)).toContain('無上限');
  });

  it('a points usage passes a points unit to the bar', () => {
    update(() => host.usage.set(new Map([[3, usage({ reward_is_money: false, reward_account_name: '測試里程' })]])));
    const barText = text(rowOf(3).querySelector('app-cap-bar'))!;
    expect(barText).toContain('測試里程');
    expect(barText).not.toContain('$');
  });

  it('disabled rule row is dimmed, shows 已停用 and 停用中，不估算回饋, and no cap bar', () => {
    const row = rowOf(2);
    expect(row.classList).toContain('inactive');
    expect(text(row.querySelector('.rule-state'))).toBe('已停用');
    expect(text(row.querySelector('.rule-muted'))).toBe('停用中，不估算回饋');
    expect(row.querySelector('app-cap-bar')).toBeNull();
    expect(rowOf(3).classList).not.toContain('inactive');
  });

  it('expired and upcoming rules show their tag and muted line', () => {
    update(() => host.rules.set([
      rule({ id: 6, ends_on: '2026-10-31' }),
      rule({ id: 7, starts_on: '2026-12-01' }),
    ]));
    expect(text(rowOf(6).querySelector('.rule-state'))).toBe('已過期');
    expect(text(rowOf(6).querySelector('.rule-muted'))).toBe('已結束，不再估算');
    expect(text(rowOf(7).querySelector('.rule-state'))).toBe('未開始');
    expect(text(rowOf(7).querySelector('.rule-muted'))).toBe('2026/12/1 起才估算');
  });

  it('an active capped rule shows the cap bar', () => {
    const track = rowOf(3).querySelector('app-cap-bar [role="img"]');
    expect(track).not.toBeNull();
    expect(track!.getAttribute('aria-label')).toContain('180');
    expect(rowOf(3).querySelector('.rule-muted')).toBeNull();
  });

  it('usage naming a non-active rule shows no bar', () => {
    update(() => host.usage.set(new Map([[2, usage({ rule_ids: [2] })]])));
    expect(rowOf(2).querySelector('app-cap-bar')).toBeNull();
    expect(root().querySelector('app-cap-bar')).toBeNull();
  });

  it('posting summary reads · <postingSummary>', () => {
    for (const r of [A, B, C]) {
      expect(text(rowOf(r.id).querySelector('.rule-posting'))).toBe(`· ${postingSummary(r)}`);
    }
  });

  it('tapping the row emits open with the rule', () => {
    rowOf(3).querySelector<HTMLButtonElement>('.rule-open')!.click();
    expect(host.onOpen).toHaveBeenCalledTimes(1);
    expect(host.onOpen).toHaveBeenCalledWith(C);
  });

  it('the switch emits toggle and does not emit open', () => {
    const switches = fixture.debugElement.queryAll(By.directive(SwitchComponent));
    const cSwitch = switches.find(d => d.nativeElement.closest('.rule-row')?.getAttribute('data-rule-id') === '3')!;
    (cSwitch.componentInstance as SwitchComponent).checkedChange.emit(false);
    expect(host.onToggle).toHaveBeenCalledWith({ rule: C, enabled: false });
    rowOf(3).querySelector<HTMLButtonElement>('.rule-enabled button')!.click();
    expect(host.onToggle).toHaveBeenCalledTimes(2);
    expect(host.onOpen).not.toHaveBeenCalled();
  });

  it('busy disables the switches', () => {
    const buttons = () => Array.from(root().querySelectorAll<HTMLButtonElement>('.rule-enabled button'));
    expect(buttons().every(b => !b.disabled)).toBe(true);
    update(() => host.busy.set(true));
    expect(buttons().length).toBe(3);
    expect(buttons().every(b => b.disabled)).toBe(true);
  });

  it('the handle and ⋯ buttons are labelled with the rule name and do not open the sheet', () => {
    const handle = rowOf(3).querySelector<HTMLButtonElement>('.rule-handle')!;
    const more = rowOf(3).querySelector<HTMLButtonElement>('.rule-more')!;
    expect(handle.getAttribute('aria-label')).toBe('拖曳排序 玩數位 3%');
    expect(more.getAttribute('aria-label')).toBe('玩數位 3% 更多動作');
    expect(rowOf(3).querySelector('.rule-enabled button')!.getAttribute('aria-label')).toBe('玩數位 3% 啟用');
    handle.click();
    more.click();
    expect(host.onOpen).not.toHaveBeenCalled();
  });
});
