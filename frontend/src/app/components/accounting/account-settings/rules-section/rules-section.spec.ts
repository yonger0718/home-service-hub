import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RewardRule, RewardUsage } from '../../../../models/accounting.model';
import { SwitchComponent } from '../../../ui/switch/switch';
import { postingSummary } from '../../reward-rule-text';
import { AccountRulesComponent, movedIds } from './rules-section';

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
      (reorder)="onReorder($event)"
      (remove)="onRemove($event)"
    />
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class HostComponent {
  readonly rules = signal<RewardRule[]>([A, B, C]);
  readonly usage = signal<ReadonlyMap<number, RewardUsage>>(new Map([[3, usage({})]]));
  readonly today = signal(TODAY);
  readonly busy = signal(false);
  /** When set, a reorder is applied to `rules` at once, as the page does optimistically. */
  applyOrder = false;
  onOpen = vi.fn<(rule: RewardRule) => void>();
  onToggle = vi.fn<(event: { rule: RewardRule; enabled: boolean }) => void>();
  onReorder = vi.fn<(ids: number[]) => void>((ids) => {
    if (!this.applyOrder) return;
    const byId = new Map(this.rules().map(r => [r.id, r]));
    this.rules.set(ids.map(id => byId.get(id)!));
  });
  onRemove = vi.fn<(rule: RewardRule) => void>();
}

describe('movedIds', () => {
  it('movedIds moves one id', () => {
    expect(movedIds([1, 2, 3], 2, 0)).toEqual([3, 1, 2]);
    expect(movedIds([1, 2, 3], 0, 2)).toEqual([2, 3, 1]);
    expect(movedIds([1, 2, 3], 1, 1)).toEqual([1, 2, 3]);
  });
});

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

  /** Waits for afterNextRender hooks (menu focus, focus after a move). */
  async function flush(): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise<void>(r => setTimeout(r));
    fixture.detectChanges();
  }

  describe('drag', () => {
    const handle = (id: number) => rowOf(id).querySelector<HTMLButtonElement>('.rule-handle')!;
    const ruleRows = () => Array.from(root().querySelectorAll<HTMLElement>('.rule-row'));
    const lines = () => Array.from(root().querySelectorAll<HTMLElement>('.rule-drop-line'));
    const dragging = () => ruleRows().filter(r => r.classList.contains('dragging'));

    /** Rows 60 px tall at tops 0 / 60 / 120, by their current place in the list. */
    function stubRects(): void {
      for (const row of ruleRows()) {
        row.getBoundingClientRect = () => {
          const top = ruleRows().indexOf(row) * 60;
          return { top, bottom: top + 60, height: 60, left: 0, right: 320, width: 320, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
        };
      }
    }

    /** jsdom may lack PointerEvent: a MouseEvent with the pointer type and a pointerId. */
    function pointer(target: Element, type: string, clientY: number, button = 0): MouseEvent {
      const event = new MouseEvent(type, { clientY, button, bubbles: true, cancelable: true });
      Object.defineProperty(event, 'pointerId', { value: 1 });
      target.dispatchEvent(event);
      fixture.detectChanges();
      return event;
    }

    function dragRule(id: number, ...ys: number[]): void {
      const h = handle(id);
      pointer(h, 'pointerdown', ys[0]);
      for (const y of ys.slice(1)) pointer(h, 'pointermove', y);
      pointer(h, 'pointerup', ys[ys.length - 1]);
    }

    beforeEach(() => stubRects());

    it('dragging C above A emits reorder once with C, A, B', () => {
      dragRule(3, 150, 10);
      expect(host.onReorder).toHaveBeenCalledTimes(1);
      expect(host.onReorder).toHaveBeenCalledWith([3, 1, 2]);
      expect(dragging()).toEqual([]);
      expect(lines()).toEqual([]);
    });

    it('dragging A below B emits B, A, C and draws the drop line between B and C', () => {
      const h = handle(1);
      const down = pointer(h, 'pointerdown', 30);
      expect(down.defaultPrevented).toBe(true);
      pointer(h, 'pointermove', 100);
      expect(lines().length).toBe(1);
      expect(lines()[0].nextElementSibling).toBe(rowOf(3));
      expect(lines()[0].previousElementSibling).toBe(rowOf(2));
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).toHaveBeenCalledTimes(1);
      expect(host.onReorder).toHaveBeenCalledWith([2, 1, 3]);
    });

    it('a release below the list moves the row to the end', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 400);
      expect(lines().length).toBe(1);
      expect(lines()[0].previousElementSibling).toBe(rowOf(3));
      expect(lines()[0].nextElementSibling?.classList.contains('rule-row') ?? false).toBe(false);
      pointer(h, 'pointerup', 400);
      expect(host.onReorder).toHaveBeenCalledWith([2, 3, 1]);
    });

    it('a release above the list moves the row to the start', () => {
      dragRule(2, 90, -200);
      expect(host.onReorder).toHaveBeenCalledWith([2, 1, 3]);
    });

    it('a drag released where it started emits nothing', () => {
      dragRule(1, 30, 100, 30);
      expect(host.onReorder).not.toHaveBeenCalled();
      expect(dragging()).toEqual([]);
    });

    it('a press that moves 3 px is a tap: no .dragging, no emit', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 33);
      expect(dragging()).toEqual([]);
      expect(lines()).toEqual([]);
      pointer(h, 'pointerup', 33);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('a non-primary button does not start a drag', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30, 2);
      pointer(h, 'pointermove', 100);
      expect(dragging()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('Escape during a drag cancels it', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      expect(dragging().length).toBe(1);
      const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      document.dispatchEvent(esc);
      fixture.detectChanges();
      expect(esc.defaultPrevented).toBe(true);
      expect(dragging()).toEqual([]);
      expect(lines()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('pointercancel cancels it', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      pointer(h, 'pointercancel', 100);
      expect(dragging()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
      expect(dragging()).toEqual([]);
    });

    it('a scroll during a drag cancels it', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      document.dispatchEvent(new Event('scroll'));
      fixture.detectChanges();
      expect(dragging()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
      expect(dragging()).toEqual([]);
    });

    it('a wheel during a drag cancels it', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      document.dispatchEvent(new WheelEvent('wheel', { bubbles: true }));
      fixture.detectChanges();
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
      expect(dragging()).toEqual([]);
    });

    it('the handle does not start a drag while busy', () => {
      update(() => host.busy.set(true));
      expect(handle(1).disabled).toBe(false);
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      expect(dragging()).toEqual([]);
      expect(lines()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('a drop while busy emits nothing', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      update(() => host.busy.set(true));
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
      expect(dragging()).toEqual([]);
      expect(lines()).toEqual([]);
    });

    it('a single rule does not start a drag', () => {
      update(() => host.rules.set([A]));
      stubRects();
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      expect(dragging()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('a rules change during a drag cancels it', () => {
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      pointer(h, 'pointermove', 100);
      expect(dragging().length).toBe(1);
      update(() => host.rules.set([A, B, C]));
      expect(dragging()).toEqual([]);
      pointer(h, 'pointerup', 100);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('dragging shows .dragging on the row and one .rule-drop-line at the target', () => {
      const h = handle(3);
      pointer(h, 'pointerdown', 150);
      expect(dragging()).toEqual([]);
      pointer(h, 'pointermove', 10);
      expect(dragging()).toEqual([rowOf(3)]);
      expect(lines().length).toBe(1);
      expect(lines()[0].nextElementSibling).toBe(rowOf(1));
      expect(lines()[0].getAttribute('aria-hidden')).toBe('true');
      // The rows keep their DOM order while dragging.
      expect(ruleRows().map(r => r.getAttribute('data-rule-id'))).toEqual(['1', '2', '3']);
      pointer(h, 'pointermove', 150);
      expect(lines()).toEqual([]);
      expect(dragging()).toEqual([rowOf(3)]);
      pointer(h, 'pointerup', 150);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('a drag does not emit open', () => {
      dragRule(3, 150, 10);
      handle(3).click();
      expect(host.onOpen).not.toHaveBeenCalled();
    });

    it('a move announces 已移到第 2 項，共 3 項 in the live region', () => {
      const live = root().querySelector('.visually-hidden[aria-live="polite"]')!;
      expect(live).not.toBeNull();
      expect(text(live)).toBe('');
      dragRule(1, 30, 100);
      expect(text(live)).toBe('已移到第 2 項，共 3 項');
    });

    it('document listeners exist only during a drag', () => {
      const docAdd = vi.spyOn(document, 'addEventListener');
      const docRemove = vi.spyOn(document, 'removeEventListener');
      const winAdd = vi.spyOn(window, 'addEventListener');
      const winRemove = vi.spyOn(window, 'removeEventListener');
      const types = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.map(c => c[0]);
      const h = handle(1);
      pointer(h, 'pointerdown', 30);
      expect(types(docAdd)).toEqual(expect.arrayContaining(['keydown', 'wheel']));
      expect(types(winAdd)).toContain('scroll');
      pointer(h, 'pointerup', 30);
      expect(types(docRemove)).toEqual(expect.arrayContaining(['keydown', 'wheel']));
      expect(types(winRemove)).toContain('scroll');

      docRemove.mockClear();
      winRemove.mockClear();
      pointer(handle(1), 'pointerdown', 30);
      fixture.destroy();
      expect(types(docRemove)).toEqual(expect.arrayContaining(['keydown', 'wheel']));
      expect(types(winRemove)).toContain('scroll');
      vi.restoreAllMocks();
    });
  });

  describe('⋯ menu', () => {
    const more = (id: number) => rowOf(id).querySelector<HTMLButtonElement>('.rule-more')!;
    const menus = () => Array.from(root().querySelectorAll<HTMLElement>('.rule-menu'));
    const items = (id: number) => Array.from(rowOf(id).querySelectorAll<HTMLButtonElement>('.rule-menu [role="menuitem"]'));
    const item = (id: number, label: string) => items(id).find(b => text(b) === label)!;
    async function openMenu(id: number): Promise<void> {
      more(id).click();
      await flush();
    }
    async function key(k: string): Promise<KeyboardEvent> {
      const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
      (document.activeElement ?? document.body).dispatchEvent(event);
      await flush();
      return event;
    }

    it('⋯ opens a menu with 編輯, 上移, 下移, 刪除 and focuses 編輯', async () => {
      expect(more(2).getAttribute('aria-haspopup')).toBe('menu');
      expect(more(2).getAttribute('aria-expanded')).toBe('false');
      expect(more(2).getAttribute('aria-controls')).toBeNull();
      await openMenu(2);
      const menu = rowOf(2).querySelector('.rule-menu')!;
      expect(menu.getAttribute('role')).toBe('menu');
      expect(more(2).getAttribute('aria-expanded')).toBe('true');
      expect(more(2).getAttribute('aria-controls')).toBe(menu.id);
      expect(items(2).map(b => text(b))).toEqual(['編輯', '上移', '下移', '刪除']);
      expect(item(2, '刪除').classList).toContain('danger');
      expect(document.activeElement).toBe(item(2, '編輯'));
      expect(rowOf(2).querySelectorAll('.rule-menu svg:not([aria-hidden="true"])').length).toBe(0);
    });

    it('上移 is disabled on the first row, 下移 on the last; both while busy', async () => {
      await openMenu(1);
      expect(item(1, '上移').disabled).toBe(true);
      expect(item(1, '下移').disabled).toBe(false);
      await openMenu(3);
      expect(item(3, '上移').disabled).toBe(false);
      expect(item(3, '下移').disabled).toBe(true);
      await openMenu(2);
      expect(item(2, '上移').disabled).toBe(false);
      expect(item(2, '下移').disabled).toBe(false);
      update(() => host.busy.set(true));
      expect(item(2, '上移').disabled).toBe(true);
      expect(item(2, '下移').disabled).toBe(true);
      expect(item(2, '編輯').disabled).toBe(false);
    });

    it('編輯 emits open; 刪除 emits remove; each closes the menu', async () => {
      await openMenu(3);
      item(3, '編輯').click();
      await flush();
      expect(host.onOpen).toHaveBeenCalledTimes(1);
      expect(host.onOpen).toHaveBeenCalledWith(C);
      expect(menus()).toEqual([]);

      await openMenu(2);
      item(2, '刪除').click();
      await flush();
      expect(host.onRemove).toHaveBeenCalledTimes(1);
      expect(host.onRemove).toHaveBeenCalledWith(B);
      expect(menus()).toEqual([]);
      expect(host.onReorder).not.toHaveBeenCalled();
    });

    it('下移 on A emits reorder with B, A, C once, and focus is on A\'s ⋯ after the host applies the order', async () => {
      host.applyOrder = true;
      const aMore = more(1);
      await openMenu(1);
      item(1, '下移').click();
      await flush();
      expect(host.onReorder).toHaveBeenCalledTimes(1);
      expect(host.onReorder).toHaveBeenCalledWith([2, 1, 3]);
      expect(Array.from(root().querySelectorAll('.rule-row')).map(r => r.getAttribute('data-rule-id'))).toEqual(['2', '1', '3']);
      expect(menus()).toEqual([]);
      expect(more(1)).toBe(aMore);
      expect(document.activeElement).toBe(aMore);
      expect(text(root().querySelector('.visually-hidden[aria-live="polite"]'))).toBe('已移到第 2 項，共 3 項');
    });

    it('上移 on C likewise', async () => {
      host.applyOrder = true;
      await openMenu(3);
      item(3, '上移').click();
      await flush();
      expect(host.onReorder).toHaveBeenCalledTimes(1);
      expect(host.onReorder).toHaveBeenCalledWith([1, 3, 2]);
      expect(menus()).toEqual([]);
      expect(document.activeElement).toBe(more(3));
      expect(text(root().querySelector('.visually-hidden[aria-live="polite"]'))).toBe('已移到第 2 項，共 3 項');
    });

    it('Home / End skip disabled items', async () => {
      await openMenu(1);
      await key('End');
      expect(document.activeElement).toBe(item(1, '刪除'));
      await key('Home');
      expect(document.activeElement).toBe(item(1, '編輯'));
      await key('ArrowDown');
      expect(document.activeElement).toBe(item(1, '下移'));
      await openMenu(3);
      await key('End');
      expect(document.activeElement).toBe(item(3, '刪除'));
      await key('ArrowUp');
      expect(document.activeElement).toBe(item(3, '上移'));
    });

    it('a press inside the menu followed by a focusout with relatedTarget null does not close it before the click', async () => {
      await openMenu(2);
      const del = item(2, '刪除');
      del.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
      del.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
      await flush();
      expect(menus().length).toBe(1);
      del.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
      del.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
      await flush();
      expect(menus().length).toBe(1);
      del.click();
      await flush();
      expect(host.onRemove).toHaveBeenCalledWith(B);
      expect(menus()).toEqual([]);
    });

    it('ArrowDown / ArrowUp wrap over enabled items; Escape closes and focuses ⋯', async () => {
      await openMenu(2);
      expect(document.activeElement).toBe(item(2, '編輯'));
      await key('ArrowUp');
      expect(document.activeElement).toBe(item(2, '刪除'));
      await key('ArrowDown');
      expect(document.activeElement).toBe(item(2, '編輯'));
      await key('ArrowDown');
      expect(document.activeElement).toBe(item(2, '上移'));
      const esc = await key('Escape');
      expect(esc.defaultPrevented).toBe(true);
      expect(menus()).toEqual([]);
      expect(document.activeElement).toBe(more(2));
      expect(more(2).getAttribute('aria-expanded')).toBe('false');
    });

    it('opening a second menu closes the first; focus leaving the menu closes it', async () => {
      await openMenu(1);
      await openMenu(3);
      expect(menus().length).toBe(1);
      expect(rowOf(3).querySelector('.rule-menu')).not.toBeNull();
      expect(more(1).getAttribute('aria-expanded')).toBe('false');

      // Focus moving inside the wrapper (to ⋯) keeps it open.
      item(3, '編輯').dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: more(3) }));
      await flush();
      expect(menus().length).toBe(1);

      item(3, '編輯').dispatchEvent(
        new FocusEvent('focusout', { bubbles: true, relatedTarget: rowOf(1).querySelector('.rule-open') }),
      );
      await flush();
      expect(menus()).toEqual([]);

      await openMenu(2);
      document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
      await flush();
      expect(menus()).toEqual([]);
    });

    it('⋯ and the handle do not emit open', async () => {
      await openMenu(2);
      more(2).click();
      await flush();
      expect(menus()).toEqual([]);
      rowOf(2).querySelector<HTMLButtonElement>('.rule-handle')!.click();
      await flush();
      expect(host.onOpen).not.toHaveBeenCalled();
    });
  });
});
