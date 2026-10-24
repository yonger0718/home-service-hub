import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LayoutModeService, MATCH_MEDIA } from '../../../services/layout-mode.service';
import { FilterBarComponent, FilterChip } from './filter-bar';

@Component({
  standalone: true,
  imports: [FilterBarComponent],
  template: `
    <app-filter-bar
      [chips]="chips()"
      (remove)="removed.push($event)"
      (clearAll)="cleared = cleared + 1"
      (reset)="resets = resets + 1"
      (openChange)="opens.push($event)"
    >
      <input filterBarSearch aria-label="搜尋" />
      <select filterBarPrimary aria-label="狀態"><option>全部</option></select>
      <label class="hub-filter-field" filterBarSecondary>
        <span class="field-label">備註</span>
        <input class="note" [value]="note" (input)="note = $any($event.target).value" />
      </label>
    </app-filter-bar>
  `,
})
class Host {
  chips = signal<FilterChip[]>([]);
  removed: string[] = [];
  cleared = 0;
  resets = 0;
  opens: boolean[] = [];
  note = '';
}

const TWO: FilterChip[] = [
  { key: 'date', label: '日期：2026-01-01 – 2026-03-31' },
  { key: 'broker', label: '券商：IB' },
];

const fakeMatchMedia = () =>
  ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList;

describe('FilterBarComponent', () => {
  let fixture: ComponentFixture<Host>;
  let host: Host;
  let el: HTMLElement;

  const q = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel);
  const flush = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  };
  const toggle = async () => {
    q('.filter-toggle')!.click();
    await flush();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [Host],
      providers: [{ provide: MATCH_MEDIA, useValue: fakeMatchMedia }],
    });
    fixture = TestBed.createComponent(Host);
    host = fixture.componentInstance;
    el = fixture.nativeElement;
    TestBed.inject(LayoutModeService).set('panes');
    fixture.detectChanges();
  });

  it('reads 篩選 with no count and no chip row when nothing is active', () => {
    expect(q('.filter-toggle')!.textContent!.trim()).toBe('篩選');
    expect(q('.filter-chips')).toBeNull();
    expect(q('.filter-toggle')!.getAttribute('aria-expanded')).toBe('false');
  });

  it('reads 篩選 2 with two chips and 清除全部; a single chip has no 清除全部', async () => {
    host.chips.set(TWO);
    await flush();
    expect(q('.filter-toggle')!.textContent!.trim()).toBe('篩選 2');
    expect(el.querySelectorAll('.filter-chip').length).toBe(2);
    expect(q('.clear-all')!.textContent!.trim()).toBe('清除全部');
    host.chips.set([TWO[1]]);
    await flush();
    expect(q('.clear-all')).toBeNull();
    expect(q('.filter-toggle')!.textContent!.trim()).toBe('篩選 1');
  });

  it('emits remove with the chip key and clearAll from 清除全部', async () => {
    host.chips.set(TWO);
    await flush();
    q('.chip-remove[aria-label="移除 券商：IB"]')!.click();
    expect(host.removed).toEqual(['broker']);
    q('.clear-all')!.click();
    expect(host.cleared).toBe(1);
  });

  it('opens a popover with role=dialog at >= 760 px, closes on Escape and 套用, refocuses the button', async () => {
    await toggle();
    expect(q('.filter-panel.popover[role="dialog"][aria-label="篩選"]')).not.toBeNull();
    expect(q('.filter-overlay')).toBeNull();
    expect(q('.filter-toggle')!.getAttribute('aria-expanded')).toBe('true');
    expect(host.opens).toEqual([true]);
    expect(document.activeElement).toBe(q('.filter-panel'));
    expect(q('.filter-panel')!.getAttribute('tabindex')).toBe('-1');
    q('.filter-panel')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await flush();
    expect(q('.filter-panel')).toBeNull();
    expect(document.activeElement).toBe(q('.filter-toggle'));
    expect(host.opens).toEqual([true, false]);
    await toggle();
    q('.panel-apply')!.click();
    await flush();
    expect(q('.filter-panel')).toBeNull();
  });

  it('opens a bottom sheet on a phone with 套用 and 重設; backdrop click closes; 重設 emits reset', async () => {
    TestBed.inject(LayoutModeService).set('phone');
    await toggle();
    expect(q('.filter-overlay > .filter-panel.sheet[aria-modal="true"]')).not.toBeNull();
    expect(q('.grab')).not.toBeNull();
    const texts = Array.from(el.querySelectorAll('.panel-actions button')).map(b => b.textContent!.trim());
    expect(texts).toEqual(['重設', '套用']);
    q('.panel-reset')!.click();
    await flush();
    expect(host.resets).toBe(1);
    expect(q('.filter-panel')).not.toBeNull();
    q('.filter-overlay')!.click();
    await flush();
    expect(q('.filter-panel')).toBeNull();
  });

  it('links the toggle to the open panel by a stable id (aria-controls only while open)', async () => {
    expect(q('.filter-toggle')!.hasAttribute('aria-controls')).toBe(false);
    await toggle();
    const id = q('.filter-panel')!.id;
    expect(id).toMatch(/^filter-panel-\d+$/);
    expect(q('.filter-toggle')!.getAttribute('aria-controls')).toBe(id);
    q('.panel-apply')!.click();
    await flush();
    expect(q('.filter-toggle')!.hasAttribute('aria-controls')).toBe(false);
    await toggle();
    expect(q('.filter-panel')!.id).toBe(id);
  });

  it('leaves the panel open on an Escape a child already handled (defaultPrevented)', async () => {
    await toggle();
    const note = q('.note')!;
    const stop = (e: Event) => e.preventDefault();
    note.addEventListener('keydown', stop);
    try {
      note.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      await flush();
      expect(q('.filter-panel')).not.toBeNull();
    } finally {
      note.removeEventListener('keydown', stop);
    }
  });

  it('leaves the panel open on an Escape during IME composition', async () => {
    await toggle();
    q('.note')!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, isComposing: true }),
    );
    await flush();
    expect(q('.filter-panel')).not.toBeNull();
  });

  it('closes on a plain Escape from a child, marks it handled and refocuses 篩選', async () => {
    await toggle();
    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    q('.note')!.dispatchEvent(esc);
    await flush();
    expect(esc.defaultPrevented).toBe(true);
    expect(q('.filter-panel')).toBeNull();
    expect(document.activeElement).toBe(q('.filter-toggle'));
  });

  it('an outside pointerdown closes the popover without moving focus', async () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    try {
      await toggle();
      outside.focus();
      expect(document.activeElement).toBe(outside);
      outside.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      await flush();
      expect(q('.filter-panel')).toBeNull();
      expect(document.activeElement).toBe(outside);
    } finally {
      outside.remove();
    }
  });

  it('listens for document pointerdown only while open', async () => {
    const add = vi.spyOn(document, 'addEventListener');
    const removeSpy = vi.spyOn(document, 'removeEventListener');
    try {
      const pd = (spy: typeof add) => spy.mock.calls.filter(c => c[0] === 'pointerdown').length;
      expect(pd(add)).toBe(0);
      await toggle();
      expect(pd(add)).toBe(1);
      q('.panel-apply')!.click();
      await flush();
      expect(pd(removeSpy)).toBe(1);
      await toggle();
      expect(pd(add)).toBe(2);
      fixture.destroy();
      expect(pd(removeSpy)).toBe(2);
    } finally {
      add.mockRestore();
      removeSpy.mockRestore();
    }
  });

  it('closes the popover on a pointerdown outside the host and keeps it on one inside', async () => {
    await toggle();
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    await flush();
    expect(q('.filter-panel')).toBeNull();
    await toggle();
    q('.note')!.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    await flush();
    expect(q('.filter-panel')).not.toBeNull();
  });

  it('re-renders the projected secondary control with its current value after close and reopen', async () => {
    await toggle();
    const input = q<HTMLInputElement>('.note')!;
    input.value = 'abc';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await flush();
    q('.panel-apply')!.click();
    await flush();
    await toggle();
    expect(q<HTMLInputElement>('.note')).toBe(input);
    expect(el.querySelectorAll('.note').length).toBe(1);
    expect(input.value).toBe('abc');
  });

  it('keeps the popover open on a pointerdown inside a portaled PrimeNG overlay', async () => {
    await toggle();
    const stand = document.createElement('div');
    stand.className = 'p-datepicker-panel';
    stand.innerHTML = '<span class="p-datepicker-day">1</span>';
    document.body.appendChild(stand);
    try {
      stand.querySelector('span')!.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      await flush();
      expect(q('.filter-panel')).not.toBeNull();
      document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      await flush();
      expect(q('.filter-panel')).toBeNull();
    } finally {
      stand.remove();
    }
  });
});
