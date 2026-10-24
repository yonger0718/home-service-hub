import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

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
    expect(document.activeElement).toBe(q('.note'));
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
    expect(q<HTMLInputElement>('.note')!.value).toBe('abc');
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
