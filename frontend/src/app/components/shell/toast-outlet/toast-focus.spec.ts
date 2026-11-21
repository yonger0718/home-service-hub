import { Component, DestroyRef, EnvironmentInjector, createEnvironmentInjector } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastScope, ToastService } from '../../../services/toast.service';
import { trapFocus } from '../../accounting/accounting-ui';
import { ToastOutletComponent } from './toast-outlet';
import { cycleFocusables, outermostModal, toastCycleTarget } from './toast-focus';

@Component({
  imports: [ToastOutletComponent],
  template: `
    <button id="before">前面</button>
    @if (modal) {
      <section id="dlg" role="dialog" aria-modal="true" tabindex="-1" (keydown)="onKey($event)">
        <span tabindex="0" data-p-hidden-focusable="true" id="s1"></span>
        <button id="d1">一</button>
        <button id="d2">二</button>
        <span tabindex="0" data-p-hidden-focusable="true" id="s2"></span>
      </section>
    }
    <app-toast-outlet />
  `,
})
class Host {
  modal = true;
  trap = true;
  dialogEsc = vi.fn();
  onKey(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      this.dialogEsc();
    } else if (this.trap) {
      trapFocus(event.currentTarget as HTMLElement, event);
    }
  }
}

describe('toast focus cycle with an open modal', () => {
  let fixture: ComponentFixture<Host>;
  let el: HTMLElement;
  let svc: ToastService;
  let scope: ToastScope;

  const q = <T extends HTMLElement = HTMLElement>(selector: string) => el.querySelector<T>(selector)!;
  const tab = (target: Element, shiftKey = false) => {
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    return event;
  };
  const press = (target: Element, key: string) => {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    return event;
  };
  const raise = (retry: () => void = vi.fn()) => {
    scope.error('儲存失敗', { retry });
    fixture.detectChanges();
  };

  beforeEach(() => {
    fixture = TestBed.createComponent(Host);
    el = fixture.nativeElement;
    document.body.appendChild(el);
    svc = TestBed.inject(ToastService);
    scope = svc.scope(createEnvironmentInjector([], TestBed.inject(EnvironmentInjector)).get(DestroyRef));
    fixture.detectChanges();
  });
  afterEach(() => el.remove());

  it('cycleFocusables never returns a PrimeNG sentinel', () => {
    expect(cycleFocusables(q('#dlg')).map(e => e.id)).toEqual(['d1', 'd2']);
  });

  it("Tab on the dialog's last control moves to 重試", () => {
    raise();
    q('#d2').focus();
    const event = tab(q('#d2'));
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('.toast-action'));
  });

  it('Tab from 重試 moves to 關閉, left to the browser', () => {
    raise();
    q('#d2').focus();
    q('.toast-action').focus();
    expect(toastCycleTarget({ active: q('.toast-action'), modal: q('#dlg'), toast: q('.toast'), backwards: false })).toBeNull();
    expect(tab(q('.toast-action')).defaultPrevented).toBe(false);
  });

  it('Tab from 關閉 wraps to the dialog\'s first control', () => {
    raise();
    q('#d2').focus();
    q('.toast-close').focus();
    expect(tab(q('.toast-close')).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('#d1'));
  });

  it("Shift+Tab on the dialog's first control moves to 關閉", () => {
    raise();
    q('#d1').focus();
    expect(tab(q('#d1'), true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('.toast-close'));
  });

  it("Shift+Tab from 重試 moves to the dialog's last control", () => {
    raise();
    q('#d1').focus();
    q('.toast-action').focus();
    expect(tab(q('.toast-action'), true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('#d2'));
  });

  it("Tab between two dialog controls is left to the dialog's trap", () => {
    raise();
    q('#d1').focus();
    expect(tab(q('#d1')).defaultPrevented).toBe(false);
  });

  it('no sentinel is ever focused', () => {
    raise();
    q('#d1').focus();
    const visited: string[] = [];
    for (const [from, back] of [['#d2', false], ['.toast-close', false], ['#d1', true]] as const) {
      q(from).focus();
      tab(q(from), back);
      visited.push(document.activeElement!.id);
    }
    expect(visited.some(id => id === 's1' || id === 's2')).toBe(false);
  });

  it("with no toast the dialog's cycle is unchanged", () => {
    // trapFocus knows no sentinels (PrimeNG's own trap does), so drop them to see trapFocus's wrap on its own.
    el.querySelectorAll('[data-p-hidden-focusable]').forEach(node => node.remove());
    q('#d2').focus();
    const event = tab(q('#d2'));
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('#d1'));
  });

  it("with no toast and the sentinels present the outlet leaves the event alone", () => {
    q('#d2').focus();
    const event = tab(q('#d2'));
    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(q('#d2'));
  });

  it('a nested role="dialog" inside the dialog does not become the modal: the cycle follows the outer dialog', () => {
    const inner = document.createElement('div');
    inner.setAttribute('role', 'dialog');
    inner.innerHTML = '<button id="n1">內一</button><button id="n2">內二</button>';
    q('#d2').after(inner);
    raise();
    expect(outermostModal(q('#n2'))).toBe(q('#dlg'));
    q('#n2').focus();
    // n2 is the outer dialog's last control: on to the toast, and 關閉 wraps to the outer dialog's first control.
    expect(tab(q('#n2')).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('.toast-action'));
    q('.toast-close').focus();
    tab(q('.toast-close'));
    expect(document.activeElement).toBe(q('#d1'));
    // n1 to n2 stays inside the dialog: not the outlet's.
    q('#n1').focus();
    expect(tab(q('#n1')).defaultPrevented).toBe(false);
  });

  it('an overlay panel appended to body while the dialog is open is not hijacked', () => {
    const panel = document.createElement('div');
    panel.className = 'p-datepicker p-component';
    panel.setAttribute('role', 'dialog');
    panel.innerHTML = '<button id="o1">日</button><button id="o2">月</button>';
    document.body.appendChild(panel);
    raise();
    try {
      expect(outermostModal(panel.querySelector<HTMLElement>('#o2')!)).toBeNull();
      const last = panel.querySelector<HTMLElement>('#o2')!;
      last.focus();
      expect(tab(last).defaultPrevented).toBe(false);
      expect(document.activeElement).toBe(last);
      const first = panel.querySelector<HTMLElement>('#o1')!;
      first.focus();
      expect(tab(first, true).defaultPrevented).toBe(false);
    } finally {
      panel.remove();
    }
  });

  it('Shift+Tab with the dialog container itself focused moves to 關閉', () => {
    raise();
    q('#dlg').focus();
    expect(tab(q('#dlg'), true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('.toast-close'));
  });

  it('without a modal the outlet leaves Tab alone', () => {
    q('#dlg').remove();
    raise();
    q('#before').focus();
    expect(tab(q('#before')).defaultPrevented).toBe(false);
    q('.toast-close').focus();
    expect(tab(q('.toast-close')).defaultPrevented).toBe(false);
  });

  it('Enter on 重試 runs it once and focus returns to #d2', () => {
    const retry = vi.fn();
    raise(retry);
    q('#d2').focus();
    tab(q('#d2'));
    const button = q<HTMLButtonElement>('.toast-action');
    expect(document.activeElement).toBe(button);
    button.click(); // what Enter does on a focused button
    fixture.detectChanges();
    expect(retry).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(q('#d2'));
  });

  it('關閉 returns focus to #d2', () => {
    raise();
    q('#d2').focus();
    tab(q('#d2'));
    q('.toast-close').focus();
    q<HTMLButtonElement>('.toast-close').click();
    fixture.detectChanges();
    expect(q('.toast')).toBeNull();
    expect(document.activeElement).toBe(q('#d2'));
  });

  it('Esc inside the toast closes the toast only', () => {
    raise();
    q('#d2').focus();
    q('.toast-action').focus();
    const spy = vi.fn();
    document.addEventListener('keydown', spy);
    const event = press(q('.toast-action'), 'Escape');
    document.removeEventListener('keydown', spy);
    fixture.detectChanges();
    expect(event.defaultPrevented).toBe(true);
    expect(spy).not.toHaveBeenCalled();
    expect(fixture.componentInstance.dialogEsc).not.toHaveBeenCalled();
    expect(q('.toast')).toBeNull();
    expect(q('#dlg')).not.toBeNull();
    expect(document.activeElement).toBe(q('#d2'));
  });

  it('Esc in the dialog keeps its meaning', () => {
    raise();
    q('#d2').focus();
    press(q('#d2'), 'Escape');
    expect(fixture.componentInstance.dialogEsc).toHaveBeenCalledTimes(1);
    expect(q('.toast')).not.toBeNull();
  });
});
