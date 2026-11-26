/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { NOTIF_CLOCK } from '../../../services/notif-clock';
import { NotifCommand, NotifTask } from '../notif-task';
import { SnoozeOption } from '../snooze';
import { NOTIF_PRIMARY_CONTROL, NotifRowComponent } from './notif-row';

const commandTask: NotifTask = {
  id: 'sched:7',
  source: 'ledger',
  from: 'schedules',
  tier: 'overdue',
  icon: '🏠',
  title: '測試月費 · 3 期未入帳',
  when: '最早 08/20',
  meta: '測試銀行卡',
  amount: 'NT$ 2,400',
  progress: null,
  primary: {
    label: '全部入帳',
    command: { kind: 'catchUp', definitionId: 7, name: '測試月費' },
  },
  secondary: [
    { label: '逐期處理', route: '/accounting/reminders', queryParams: { tab: 'pending' } },
  ],
  sortKey: '2026-08-20',
  wake: {},
};

const linkTask: NotifTask = {
  ...commandTask,
  id: 'bill:11:2026-10-24',
  from: 'bills',
  tier: 'today',
  icon: 'pi pi-credit-card',
  title: '測試銀行卡帳單',
  when: '今天扣款',
  meta: null,
  amount: null,
  primary: { label: '記繳款', route: '/accounting/accounts/11' },
  secondary: [],
};

@Component({
  imports: [NotifRowComponent],
  template: `<app-notif-row
    [task]="task()"
    [busy]="busy()"
    [solid]="solid()"
    [selected]="selected()"
    [snoozable]="snoozable()"
    (run)="ran.push($event)"
    (snooze)="snoozed.push($event)"
  />`,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class HostComponent {
  readonly task = signal<NotifTask>(commandTask);
  readonly busy = signal(false);
  readonly solid = signal(false);
  readonly selected = signal(false);
  readonly snoozable = signal(true);
  readonly ran: NotifCommand[] = [];
  readonly snoozed: SnoozeOption[] = [];
}

function setup(task: NotifTask = commandTask) {
  // Saturday 2026-10-24 10:00, device time.
  TestBed.configureTestingModule({ providers: [provideRouter([]), { provide: NOTIF_CLOCK, useValue: () => new Date(2026, 9, 24, 10, 0) }] });
  const fixture = TestBed.createComponent(HostComponent);
  fixture.componentInstance.task.set(task);
  fixture.autoDetectChanges();
  const root: HTMLElement = fixture.nativeElement;
  const q = <T extends Element = HTMLElement>(s: string) => root.querySelector<T>(s);
  return { fixture, host: fixture.componentInstance, root, q };
}

describe('NotifRowComponent', () => {
  describe('稍後提醒 (PR E2)', () => {
    it('a 稍後提醒 picker after the actions, named for the task; options with the time they end', () => {
      const { fixture, q, root } = setup();
      const trigger = q<HTMLButtonElement>('.notif-snooze .pk-trigger')!;
      expect(trigger.textContent!.trim()).toBe('稍後提醒');
      expect(trigger.getAttribute('aria-label')).toBe('稍後提醒：測試月費 · 3 期未入帳');
      // From 760 px the trigger is the clock icon alone (CSS); its hover title says 稍後提醒 (owner design review 2026-11-26).
      expect(trigger.querySelector('.pk-icon.pi-clock')).not.toBeNull();
      expect(trigger.getAttribute('title')).toBe('稍後提醒');
      expect(q('.notif-actions')!.lastElementChild!.matches('app-picker.notif-snooze')).toBe(true);
      trigger.click();
      fixture.detectChanges();
      const options = [...root.querySelectorAll('.pk-option')].map((o) => [
        o.querySelector('.pk-label')!.textContent!.trim(),
        o.querySelector('.pk-hint')!.textContent!.trim(),
      ]);
      expect(options).toEqual([
        ['3 小時後', '10/24（六）13:00'],
        ['明天 09:00', '10/25（日）'],
        ['下週一 09:00', '10/26（一）'],
      ]);
    });

    it('picking an option emits it', () => {
      const { fixture, host, q, root } = setup();
      q<HTMLButtonElement>('.notif-snooze .pk-trigger')!.click();
      fixture.detectChanges();
      root.querySelector<HTMLElement>('.pk-option[data-value="tomorrow"]')!.click();
      fixture.detectChanges();
      expect(host.snoozed).toEqual(['tomorrow']);
      expect(host.ran).toEqual([]);
    });

    it('snoozable false: no picker', () => {
      const { fixture, host, q } = setup();
      host.snoozable.set(false);
      fixture.detectChanges();
      expect(q('.notif-snooze')).toBeNull();
    });
  });

  it('renders title, source tag, when, meta and amount', async () => {
    const { fixture, q } = setup();
    await fixture.whenStable();
    expect(q('.notif-title')?.textContent?.trim()).toBe('測試月費 · 3 期未入帳');
    expect(q('.notif-tag')?.textContent?.trim()).toBe('財務');
    expect(q('.notif-when')?.textContent?.trim()).toBe('最早 08/20');
    expect(q('.notif-meta')?.textContent).toBe(' · 測試銀行卡');
    expect(q('.notif-amount')?.textContent?.trim()).toBe('NT$ 2,400');
    expect(q('.notif-line')?.textContent).toBe('最早 08/20 · 測試銀行卡');
  });

  it('renders no separator or empty element for an empty when or meta', async () => {
    const a = setup({ ...commandTask, when: '', meta: null });
    await a.fixture.whenStable();
    expect(a.q('.notif-when')).toBeNull();
    expect(a.q('.notif-meta')).toBeNull();
    expect(a.q('.notif-line')).toBeNull();
    TestBed.resetTestingModule();
    const b = setup({ ...commandTask, when: '', meta: '測試銀行卡' });
    await b.fixture.whenStable();
    expect(b.q('.notif-when')).toBeNull();
    expect(b.q('.notif-meta')?.textContent).toBe('測試銀行卡');
    TestBed.resetTestingModule();
    const c = setup({ ...commandTask, when: '今天', meta: '' });
    await c.fixture.whenStable();
    expect(c.q('.notif-meta')).toBeNull();
    expect(c.q('.notif-line')?.textContent?.trim()).toBe('今天');
  });

  it('an overdue row marks itself and shows when in the alert tone', () => {
    const { q } = setup();
    const row = q('app-notif-row')!;
    expect(row.classList.contains('notif-row')).toBe(true);
    expect(row.classList.contains('notif-row--alert')).toBe(true);
    expect(row.getAttribute('data-task-id')).toBe('sched:7');
    expect(q('.notif-when')).not.toBeNull();
  });

  it('a non-alert row has no alert class; a system row has', () => {
    const a = setup(linkTask);
    expect(a.root.querySelector('.notif-row--alert')).toBeNull();
    TestBed.resetTestingModule();
    const b = setup({ ...linkTask, tier: 'system' });
    expect(b.root.querySelector('.notif-row--alert')).not.toBeNull();
  });

  it('a command primary is a native button named 「全部入帳：測試月費 · 3 期未入帳」 and emits its command once', () => {
    const { q, host, fixture } = setup();
    const btn = q(NOTIF_PRIMARY_CONTROL)!;
    expect(btn.tagName).toBe('BUTTON');
    expect(btn.getAttribute('aria-label')).toBe('全部入帳：測試月費 · 3 期未入帳');
    expect(q('app-btn.notif-primary')?.hasAttribute('aria-label')).toBe(false);
    btn.click();
    fixture.detectChanges();
    expect(host.ran).toEqual([{ kind: 'catchUp', definitionId: 7, name: '測試月費' }]);
  });

  it('NOTIF_PRIMARY_CONTROL matches exactly one focusable element per row, for a command and for a link', () => {
    for (const task of [commandTask, linkTask]) {
      const { root } = setup(task);
      const all = root.querySelectorAll<HTMLElement>(NOTIF_PRIMARY_CONTROL);
      expect(all.length).toBe(1);
      document.body.appendChild(root);
      all[0].focus();
      expect(document.activeElement).toBe(all[0]);
      root.remove();
      TestBed.resetTestingModule();
    }
  });

  it('busy: the button is disabled, exposes aria-busy and emits nothing', () => {
    const { q, host, fixture } = setup();
    host.busy.set(true);
    fixture.detectChanges();
    const btn = q<HTMLButtonElement>(NOTIF_PRIMARY_CONTROL)!;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('aria-busy')).toBe('true');
    btn.click();
    expect(host.ran).toEqual([]);
  });

  it('a link primary is an anchor to /accounting/accounts/11 named 「記繳款：測試銀行卡帳單」', () => {
    const { q } = setup(linkTask);
    const a = q<HTMLAnchorElement>(NOTIF_PRIMARY_CONTROL)!;
    expect(a.tagName).toBe('A');
    expect(a.getAttribute('href')).toBe('/accounting/accounts/11');
    expect(a.getAttribute('aria-label')).toBe('記繳款：測試銀行卡帳單');
    expect(a.classList.contains('notif-primary--link')).toBe(true);
    expect(q('app-btn')).toBeNull();
  });

  it('a link with a fragment carries it on the primary and on a secondary (/portfolio/import#cathay)', () => {
    const toCard = { label: '查看', route: '/portfolio/import', fragment: 'cathay' };
    const { q } = setup({ ...linkTask, primary: toCard, secondary: [toCard] });
    expect(q<HTMLAnchorElement>(NOTIF_PRIMARY_CONTROL)!.getAttribute('href')).toBe('/portfolio/import#cathay');
    expect(q<HTMLAnchorElement>('a.notif-secondary')!.getAttribute('href')).toBe('/portfolio/import#cathay');
  });

  it('secondary links follow the primary in the DOM with their query params', () => {
    const { q, root } = setup();
    const s = q<HTMLAnchorElement>('a.notif-secondary')!;
    expect(s.getAttribute('href')).toBe('/accounting/reminders?tab=pending');
    expect(s.getAttribute('aria-label')).toBe('逐期處理：測試月費 · 3 期未入帳');
    const focusables = Array.from(root.querySelectorAll('button, a'));
    expect(focusables.indexOf(q(NOTIF_PRIMARY_CONTROL)!)).toBeLessThan(focusables.indexOf(s));
    // Then 稍後提醒 (PR E2), last.
    expect(focusables.length).toBe(3);
    expect(focusables[2]).toBe(q('.notif-snooze .pk-trigger'));
  });

  it('progress 17 / 28 renders a bar 60.7% wide, hidden from assistive tech; no bar without progress', () => {
    const { q, host, fixture } = setup();
    expect(q('.notif-bar')).toBeNull();
    host.task.set({ ...commandTask, progress: { k: 17, n: 28 } });
    fixture.detectChanges();
    const bar = q('.notif-bar')!;
    expect(bar.getAttribute('aria-hidden')).toBe('true');
    const fill = bar.querySelector<HTMLElement>('span')!;
    expect(parseFloat(fill.style.width)).toBeCloseTo(60.7, 1);
    host.task.set({ ...commandTask, progress: { k: 0, n: 0 } });
    fixture.detectChanges();
    expect(q('.notif-bar')).toBeNull();
  });

  it('no amount element when amount is null', () => {
    const { q } = setup(linkTask);
    expect(q('.notif-amount')).toBeNull();
  });

  it('solid: the command button uses the primary variant; the link gets .solid', () => {
    const a = setup();
    expect(a.q(NOTIF_PRIMARY_CONTROL)!.classList.contains('btn-soft')).toBe(true);
    a.host.solid.set(true);
    a.fixture.detectChanges();
    expect(a.q(NOTIF_PRIMARY_CONTROL)!.classList.contains('btn-primary')).toBe(true);
    TestBed.resetTestingModule();
    const b = setup(linkTask);
    expect(b.q(NOTIF_PRIMARY_CONTROL)!.classList.contains('solid')).toBe(false);
    b.host.solid.set(true);
    b.fixture.detectChanges();
    expect(b.q(NOTIF_PRIMARY_CONTROL)!.classList.contains('solid')).toBe(true);
  });

  it('an emoji icon renders as text, a pi icon as <i>; the tile is hidden from assistive tech', () => {
    const a = setup();
    const tile = a.q('.notif-icon')!;
    expect(tile.getAttribute('aria-hidden')).toBe('true');
    expect(tile.textContent?.trim()).toBe('🏠');
    expect(tile.querySelector('i')).toBeNull();
    TestBed.resetTestingModule();
    const b = setup(linkTask);
    const i = b.q('.notif-icon i')!;
    expect(i.classList.contains('pi-credit-card')).toBe(true);
    expect(b.q('.notif-icon')!.textContent?.trim()).toBe('');
  });

  it('the bar carries no text and is aria-hidden; the visible when / meta text carries the progress wording', () => {
    const { q, host, fixture } = setup();
    host.task.set({
      ...commandTask,
      progress: { k: 17, n: 28 },
      when: '17 / 28 已核對',
      meta: '10 筆要你決定',
    });
    fixture.detectChanges();
    const bar = q('.notif-bar')!;
    expect(bar.textContent?.trim()).toBe('');
    expect(bar.getAttribute('aria-hidden')).toBe('true');
    expect(q('.notif-line')?.textContent).toBe('17 / 28 已核對 · 10 筆要你決定');
    expect(q('.notif-line')?.closest('[aria-hidden="true"]')).toBeNull();
  });

  it('the title element carries the full title as its title attribute', () => {
    const { q } = setup();
    expect(q('.notif-title')?.getAttribute('title')).toBe('測試月費 · 3 期未入帳');
  });

  it('the source tag reads 投資 for a portfolio task', () => {
    const { q } = setup({ ...commandTask, source: 'portfolio' });
    expect(q('.notif-tag')?.textContent?.trim()).toBe('投資');
  });

  it('a link primary with a secondary link: primary first, both named label：title', () => {
    const { q, root } = setup({
      ...linkTask,
      secondary: [
        { label: '看帳單', route: '/accounting/reminders', queryParams: { tab: 'bills' } },
      ],
    });
    const links = Array.from(root.querySelectorAll<HTMLAnchorElement>('.notif-actions a'));
    expect(links.map((a) => a.getAttribute('aria-label'))).toEqual([
      '記繳款：測試銀行卡帳單',
      '看帳單：測試銀行卡帳單',
    ]);
    expect(links[0]).toBe(q(NOTIF_PRIMARY_CONTROL));
    expect(links[1].getAttribute('href')).toBe('/accounting/reminders?tab=bills');
  });

  describe('selected (the statement open in the review flow)', () => {
    const reconTask: NotifTask = {
      ...linkTask,
      id: 'recon:12',
      from: 'recon',
      tier: 'confirm',
      title: '對帳 · 測試銀行卡（至 10/05）',
      primary: { label: '對帳', route: '/notifications/recon/12' },
    };

    it('not selected by default: no class, the label 對帳, no aria-current', () => {
      const { q } = setup(reconTask);
      expect(q('app-notif-row')!.classList.contains('notif-row--selected')).toBe(false);
      const a = q<HTMLAnchorElement>(NOTIF_PRIMARY_CONTROL)!;
      expect(a.textContent?.trim()).toBe('對帳');
      expect(a.getAttribute('aria-label')).toBe('對帳：對帳 · 測試銀行卡（至 10/05）');
      expect(a.hasAttribute('aria-current')).toBe(false);
    });

    it('selected: the row is marked, the primary reads 對帳中 with aria-current="true" and stays a link', () => {
      const { q, host, fixture } = setup(reconTask);
      host.selected.set(true);
      fixture.detectChanges();
      expect(q('app-notif-row')!.classList.contains('notif-row--selected')).toBe(true);
      const a = q<HTMLAnchorElement>(NOTIF_PRIMARY_CONTROL)!;
      expect(a.tagName).toBe('A');
      expect(a.textContent?.trim()).toBe('對帳中');
      expect(a.getAttribute('aria-label')).toBe('對帳中：對帳 · 測試銀行卡（至 10/05）');
      expect(a.getAttribute('aria-current')).toBe('true');
      expect(a.getAttribute('href')).toBe('/notifications/recon/12');
      host.selected.set(false);
      fixture.detectChanges();
      expect(q('app-notif-row')!.classList.contains('notif-row--selected')).toBe(false);
      expect(q(NOTIF_PRIMARY_CONTROL)!.textContent?.trim()).toBe('對帳');
    });

    it('scss: on a phone the primary control scrolls clear of the tab bar and its + button (focus lands on it after the flow closes)', () => {
      const css = readFileSync(
        join(process.cwd(), 'src/app/components/notifications/notif-row/notif-row.scss'),
        'utf8',
      );
      const phone = css.slice(css.indexOf('@media (max-width: 759.98px)'));
      const block = phone.slice(0, phone.indexOf('\n}\n'));
      const clear = 'scroll-margin-bottom: calc(var(--m-nav-h) + var(--m-plus-lift) + env(safe-area-inset-bottom, 0px) + 12px)';
      const rule = /\.notif-primary--link,\s*\.notif-primary ::ng-deep \.btn\s*\{([^}]*)\}/.exec(block);
      expect(rule?.[1]).toContain(clear);
    });

    it('scss: the selected row is on --app-primary-soft with a 3 px inset --app-primary bar at the start side', () => {
      const css = readFileSync(
        join(process.cwd(), 'src/app/components/notifications/notif-row/notif-row.scss'),
        'utf8',
      );
      const block = css.slice(css.indexOf(':host(.notif-row--selected) {'));
      const body = block.slice(0, block.indexOf('\n}\n'));
      expect(body).toMatch(/background:\s*var\(--app-primary-soft\)/);
      expect(body).toMatch(/box-shadow:\s*inset 3px 0 0 var\(--app-primary\)/);
    });
  });

  describe('scss rules (source pins)', () => {
    const css = readFileSync(
      join(process.cwd(), 'src/app/components/notifications/notif-row/notif-row.scss'),
      'utf8',
    );
    const phone = css.slice(css.indexOf('@media (max-width: 759.98px)'));
    const rule = (src: string, selector: string) => {
      const m = new RegExp(
        `${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
      ).exec(src);
      return m ? m[1] : '';
    };

    it('below 760 px the title wraps, clamped to 2 lines, and the head lets the amount drop under it', () => {
      const t = rule(phone, '.notif-title');
      expect(t).toMatch(/white-space:\s*normal/);
      expect(t).toMatch(/overflow-wrap:\s*anywhere/);
      expect(t).toMatch(/-webkit-line-clamp:\s*2/);
      expect(rule(phone, '.notif-head')).toMatch(/flex-wrap:\s*wrap/);
    });

    it("below 760 px the title's basis is its own text (auto, not a px value): a short title keeps its amount beside it", () => {
      const t = rule(phone, '.notif-title');
      expect(t).toMatch(/flex:\s*1 1 auto/);
      expect(t).not.toMatch(/flex:[^;]*\d+px/);
    });

    it('at 760 px and up the actions have one fixed width, content right-aligned, so every amount ends at the same x', () => {
      const top = css.slice(0, css.indexOf('@media'));
      expect(rule(top, '.notif-actions')).toMatch(/flex:\s*0 0 240px/);
      expect(rule(top, '.notif-actions')).toMatch(/justify-content:\s*flex-end/);
      expect(rule(phone, '.notif-actions')).toMatch(/flex:\s*0 0 100%/);
      expect(rule(phone, '.notif-actions')).toMatch(/justify-content:\s*flex-start/);
    });

    it('at 760 px and up the title is a single-line ellipsis', () => {
      const t = rule(css.slice(0, css.indexOf('@media')), '.notif-title');
      expect(t).toMatch(/white-space:\s*nowrap/);
      expect(t).toMatch(/text-overflow:\s*ellipsis/);
    });

    it('the actions wrap under the body before the body shrinks below its minimum width', () => {
      const top = css.slice(0, css.indexOf('@media'));
      expect(rule(top, ':host')).toMatch(/flex-wrap:\s*wrap/);
      expect(rule(top, '.notif-body')).toMatch(/min-width:\s*280px/);
    });
  });

  it('scss: no colour literal', () => {
    const css = readFileSync(
      join(process.cwd(), 'src/app/components/notifications/notif-row/notif-row.scss'),
      'utf8',
    );
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(css).not.toMatch(/rgba?\(/);
  });
});
