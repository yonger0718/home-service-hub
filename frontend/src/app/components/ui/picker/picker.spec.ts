import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { LayoutModeService } from '../../../services/layout-mode.service';
import { PickerComponent, PickerOption } from './picker';

@Component({
  imports: [PickerComponent],
  template: `<button class="before">before</button>
    <app-picker [options]="options()" [value]="value()" label="計算方式" (change)="emitted.push($event)"></app-picker>`,
})
class PickerHostComponent {
  options = signal<PickerOption[]>([
    { value: 'a', label: '甲方案' },
    { value: 'b', label: '乙方案', hint: '備註' },
    { value: 'c', label: '丙方案', disabled: true },
  ]);
  value = signal('a');
  emitted: string[] = [];
}

function setup(mode: 'phone' | 'panes' = 'panes') {
  TestBed.inject(LayoutModeService).set(mode);
  const fixture = TestBed.createComponent(PickerHostComponent);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  const q = <T extends HTMLElement>(sel: string) => el.querySelector(sel) as T;
  const open = () => {
    q<HTMLButtonElement>('.pk-trigger').click();
    fixture.detectChanges();
  };
  const key = (target: HTMLElement, k: string, init: KeyboardEventInit = {}) => {
    const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    fixture.detectChanges();
    return event;
  };
  return { fixture, host: fixture.componentInstance, el, q, open, key };
}

describe('PickerComponent', () => {
  it('shows the selected option label and data-value', () => {
    const { q, fixture, host } = setup();
    const trigger = q<HTMLButtonElement>('.pk-trigger');
    expect(trigger.textContent).toContain('甲方案');
    expect(trigger.dataset['value']).toBe('a');
    expect(trigger.getAttribute('aria-haspopup')).toBe('listbox');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    host.value.set('zzz');
    fixture.detectChanges();
    expect(trigger.textContent).toContain('計算方式');
  });

  it('choosing an option emits change, closes and refocuses the trigger', () => {
    const { q, fixture, host, open } = setup();
    open();
    expect(q('.pk-trigger').getAttribute('aria-expanded')).toBe('true');
    expect(fixture.nativeElement.querySelectorAll('[role=listbox] [role=option]').length).toBe(3);
    expect(q('[role=option][data-value="a"]').getAttribute('aria-selected')).toBe('true');
    q('[role=option][data-value="b"]').click();
    fixture.detectChanges();
    expect(host.emitted).toEqual(['b']);
    expect(q('[role=listbox]')).toBeNull();
    expect(document.activeElement).toBe(q('.pk-trigger'));
  });

  it('trigger names the label and the chosen option; the listbox keeps the label', () => {
    const { q, fixture, host, open } = setup();
    expect(q('.pk-trigger').getAttribute('aria-label')).toBe('計算方式：甲方案');
    open();
    expect(q('[role=listbox]').getAttribute('aria-label')).toBe('計算方式');
    q('[role=option][data-value="b"]').click();
    host.value.set(host.emitted[0]);
    fixture.detectChanges();
    expect(q('.pk-trigger').getAttribute('aria-label')).toBe('計算方式：乙方案');
  });

  it('Escape closes without emitting', () => {
    const { q, host, open, key } = setup();
    open();
    const event = key(q('[role=option][data-value="a"]'), 'Escape');
    expect(event.defaultPrevented).toBe(true);
    expect(q('[role=listbox]')).toBeNull();
    expect(host.emitted).toEqual([]);
    expect(document.activeElement).toBe(q('.pk-trigger'));
  });

  it('phone: overlay tap closes, keeps value, returns focus to trigger', () => {
    const { q, fixture, host, open } = setup('phone');
    open();
    expect(q('.overlay > .sheet[role=dialog][aria-modal=true] [role=listbox]')).not.toBeNull();
    q('.sheet').click();
    fixture.detectChanges();
    expect(q('.sheet')).not.toBeNull();
    q('.overlay').click();
    fixture.detectChanges();
    expect(q('.overlay')).toBeNull();
    expect(host.emitted).toEqual([]);
    expect(q('.pk-trigger').dataset['value']).toBe('a');
    expect(document.activeElement).toBe(q('.pk-trigger'));
  });

  it('desktop: outside pointerdown closes without emitting', () => {
    const { q, fixture, host, open } = setup();
    open();
    q('.before').dispatchEvent(new Event('pointerdown', { bubbles: true }));
    fixture.detectChanges();
    expect(q('[role=listbox]')).toBeNull();
    expect(host.emitted).toEqual([]);
  });

  it('Tab inside the open panel stays inside', () => {
    const { q, open, key, fixture } = setup();
    open();
    const options = Array.from(fixture.nativeElement.querySelectorAll('[role=option]')) as HTMLElement[];
    const focusable = options.filter(o => o.tabIndex >= 0);
    const last = focusable[focusable.length - 1];
    last.focus();
    const event = key(last, 'Tab');
    expect(event.defaultPrevented).toBe(true);
    expect(q('[role=listbox]').contains(document.activeElement)).toBe(true);
  });

  it('arrows move the active option and Enter chooses it', () => {
    const { q, host, open, key } = setup();
    open();
    key(q('[role=option][data-value="a"]'), 'ArrowDown');
    expect(document.activeElement).toBe(q('[role=option][data-value="b"]'));
    key(q('[role=option][data-value="b"]'), 'Enter');
    expect(host.emitted).toEqual(['b']);
  });

  it('disabled option is not selectable', () => {
    const { q, fixture, host, open, key } = setup();
    open();
    const disabled = q('[role=option][data-value="c"]');
    expect(disabled.getAttribute('aria-disabled')).toBe('true');
    disabled.click();
    fixture.detectChanges();
    expect(host.emitted).toEqual([]);
    expect(q('[role=listbox]')).not.toBeNull();
    key(q('[role=option][data-value="a"]'), 'ArrowDown');
    key(q('[role=option][data-value="b"]'), 'ArrowDown');
    expect(document.activeElement).toBe(q('[role=option][data-value="a"]'));
  });
});
