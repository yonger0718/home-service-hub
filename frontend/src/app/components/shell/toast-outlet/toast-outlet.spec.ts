import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { Component, DestroyRef, EnvironmentInjector, createEnvironmentInjector } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastScope, ToastService } from '../../../services/toast.service';
import { ToastOutletComponent } from './toast-outlet';

@Component({
  imports: [ToastOutletComponent],
  template: `<button id="outside">外面</button><button id="other">別處</button><app-toast-outlet />`,
})
class Host {}

describe('ToastOutletComponent', () => {
  let fixture: ComponentFixture<Host>;
  let el: HTMLElement;
  let svc: ToastService;
  let scope: ToastScope;

  const flush = () => fixture.detectChanges();
  const q = (selector: string) => el.querySelector<HTMLElement>(selector);
  const status = () => q('.toast-region[role="status"]')!;
  const alert = () => q('.toast-region[role="alert"]')!;

  beforeEach(() => {
    vi.useFakeTimers();
    fixture = TestBed.createComponent(Host);
    el = fixture.nativeElement;
    document.body.appendChild(el);
    svc = TestBed.inject(ToastService);
    scope = svc.scope(createEnvironmentInjector([], TestBed.inject(EnvironmentInjector)).get(DestroyRef));
    flush();
  });
  afterEach(() => {
    vi.useRealTimers();
    el.remove();
  });

  it('keeps both live regions in the DOM with nothing to show', () => {
    expect(status().getAttribute('aria-live')).toBe('polite');
    expect(status().getAttribute('aria-atomic')).toBe('true');
    expect(alert().getAttribute('aria-atomic')).toBe('true');
    expect(q('.toast')).toBeNull();
    expect(q('app-toast-outlet')!.hasAttribute('data-toast-outlet')).toBe(true);
  });

  it('完成 renders in the status region with 復原 and no close', () => {
    scope.done('已確認', { undo: vi.fn() });
    flush();
    const toast = status().querySelector('.toast.toast--done')!;
    expect(toast.querySelector('.toast-text')!.textContent).toBe('已確認');
    expect(toast.querySelector('i.toast-icon.pi-check')!.getAttribute('aria-hidden')).toBe('true');
    expect(toast.querySelector('.toast-action')!.textContent).toBe('復原');
    expect(toast.querySelector('.toast-close')).toBeNull();
    expect(alert().querySelector('.toast')).toBeNull();
  });

  it('進行中 with neither action nor close', () => {
    scope.progress('資料重算執行中…');
    flush();
    const toast = status().querySelector('.toast.toast--progress')!;
    expect(toast.querySelector('i.pi-spinner.pi-spin')).not.toBeNull();
    expect(toast.querySelector('button')).toBeNull();
  });

  it('失敗 in the alert region with 重試 and 關閉', () => {
    scope.error('儲存失敗，網路中斷', { retry: vi.fn() });
    flush();
    const toast = alert().querySelector('.toast.toast--error')!;
    expect(toast.querySelector('i.pi-exclamation-circle')).not.toBeNull();
    expect(toast.querySelector('.toast-action')!.textContent).toBe('重試');
    expect(toast.querySelector('.toast-close')!.getAttribute('aria-label')).toBe('關閉');
    expect(toast.getAttribute('data-toast-id')).toBe(String(svc.current()!.id));
    expect(status().querySelector('.toast')).toBeNull();
  });

  it('復原 / 重試 run their callback once and remove the toast', () => {
    const retry = vi.fn();
    scope.error('帳戶讀取失敗', { retry });
    flush();
    q('.toast-action')!.click();
    q('.toast-action')?.click();
    flush();
    expect(retry).toHaveBeenCalledTimes(1);
    expect(q('.toast')).toBeNull();

    const undo = vi.fn();
    scope.done('已確認', { undo });
    flush();
    q('.toast-action')!.click();
    flush();
    expect(undo).toHaveBeenCalledTimes(1);
    expect(q('.toast')).toBeNull();
  });

  it('關閉 removes a 失敗 toast', () => {
    scope.error('儲存失敗');
    flush();
    q('.toast-close')!.click();
    flush();
    expect(q('.toast')).toBeNull();
  });

  it('pointer and focus hold the timer together', () => {
    scope.done('已確認', { undo: vi.fn() });
    flush();
    const host = q('app-toast-outlet')!;
    const action = q('.toast-action')!;
    host.dispatchEvent(new Event('pointerenter'));
    action.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    host.dispatchEvent(new Event('pointerleave'));
    vi.advanceTimersByTime(10_000);
    expect(svc.current()).not.toBeNull();
    action.dispatchEvent(
      new FocusEvent('focusout', { bubbles: true, relatedTarget: q('#outside') }),
    );
    vi.advanceTimersByTime(5000);
    expect(svc.current()).toBeNull();
  });

  it('focus moving between the toast controls keeps the pause', () => {
    scope.error('x', { retry: vi.fn() });
    flush();
    const resume = vi.spyOn(svc, 'resume');
    q('.toast-action')!.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: q('.toast-close') }));
    expect(resume).not.toHaveBeenCalled();
    q('.toast-close')!.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: q('#outside') }));
    expect(resume).toHaveBeenCalledWith('focus');
  });

  it('a focused 重試 replaced by page code does not leave the next 完成 stuck', async () => {
    scope.error('儲存失敗', { retry: vi.fn() });
    flush();
    const retry = q('.toast-action')!;
    retry.focus();
    retry.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    scope.done('已儲存');
    flush();
    await fixture.whenStable();
    vi.advanceTimersByTime(5000);
    expect(svc.current()).toBeNull();
  });

  it('focus that is really still inside the toast now shown keeps the timer paused', async () => {
    scope.done('A', { undo: vi.fn() });
    flush();
    const button = q('.toast-action')!;
    button.focus(); // the same button is reused for the next toast, so focus is genuinely inside
    scope.done('B', { undo: vi.fn() });
    flush();
    await fixture.whenStable();
    expect(document.activeElement).toBe(q('.toast-action'));
    vi.advanceTimersByTime(20_000);
    expect(svc.current()?.text).toBe('B');
    q('.toast-action')!.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: q('#outside') }));
    vi.advanceTimersByTime(5000);
    expect(svc.current()).toBeNull();
  });

  it('a toast raised while the pointer is still over the outlet starts paused', async () => {
    scope.error('儲存失敗');
    flush();
    q('app-toast-outlet')!.dispatchEvent(new Event('pointerenter'));
    q('.toast-close')!.click();
    flush();
    scope.done('已儲存');
    flush();
    await fixture.whenStable();
    vi.advanceTimersByTime(20_000);
    expect(svc.current()?.text).toBe('已儲存');
    q('app-toast-outlet')!.dispatchEvent(new Event('pointerleave'));
    vi.advanceTimersByTime(5000);
    expect(svc.current()).toBeNull();
  });

  it('a pointer that moved away while no toast was shown does not pause the next one', async () => {
    scope.error('儲存失敗');
    flush();
    q('app-toast-outlet')!.dispatchEvent(new Event('pointerenter'));
    q('.toast-close')!.click();
    flush();
    q('#other')!.dispatchEvent(new Event('pointermove', { bubbles: true }));
    scope.done('已儲存');
    flush();
    await fixture.whenStable();
    vi.advanceTimersByTime(5000);
    expect(svc.current()).toBeNull();
  });

  it('a pointer that moved away from a replacing toast without a pointerleave lets its timer run', async () => {
    scope.error('儲存失敗');
    flush();
    q('app-toast-outlet')!.dispatchEvent(new Event('pointerenter'));
    scope.done('已儲存');
    flush();
    await fixture.whenStable();
    vi.advanceTimersByTime(20_000);
    expect(svc.current()?.text).toBe('已儲存');
    q('#other')!.dispatchEvent(new Event('pointermove', { bubbles: true }));
    vi.advanceTimersByTime(5000);
    expect(svc.current()).toBeNull();
  });

  it('a new toast never moves focus', () => {
    const outside = q('#outside')!;
    outside.focus();
    scope.error('儲存失敗', { retry: vi.fn() });
    flush();
    expect(document.activeElement).toBe(outside);
  });

  it('closing from inside returns focus to the last element focused outside', () => {
    q('#other')!.focus();
    scope.error('儲存失敗');
    flush();
    q('.toast-close')!.focus();
    q('.toast-close')!.click();
    flush();
    expect(q('.toast')).toBeNull();
    expect(document.activeElement).toBe(q('#other'));
  });

  it('Esc inside the toast closes it and stops the event', () => {
    scope.error('儲存失敗', { retry: vi.fn() });
    flush();
    const seen = vi.fn();
    document.addEventListener('keydown', seen);
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    q('.toast-action')!.dispatchEvent(event);
    document.removeEventListener('keydown', seen);
    flush();
    expect(q('.toast')).toBeNull();
    expect(event.defaultPrevented).toBe(true);
    expect(seen).not.toHaveBeenCalled();
  });

  it('scss: fixed, z-index above every PrimeNG overlay, width limited by --toast-fab-clear, no colour literal', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/shell/toast-outlet/toast-outlet.scss'), 'utf8');
    expect(css).toContain('position: fixed');
    expect(css).toContain('var(--toast-fab-clear');
    expect(css).toContain('max(240px, min(440px');
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(css).not.toMatch(/rgba?\(/);
  });

  it('scss: z-index clears the PrimeNG counter ceiling and the app\'s own layers', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/shell/toast-outlet/toast-outlet.scss'), 'utf8');
    const z = Number(/:host\s*{[^}]*?z-index:\s*(\d+)/.exec(css)![1]);
    // PrimeNG bases: modal / tooltip 1100, overlay / menu 1000; every key change adds its base + 2, same key +2.
    // Eight alternating layers (far beyond a real session) top out at 1100 + 8 * (1100 + 2) = 9916.
    expect(z).toBeGreaterThan(1100 + 8 * (1100 + 2));
    expect(z).toBeGreaterThan(1200); // account-picker / picker / sheet layers
  });

  it('scss: below 760 px the toast clears the tab bar and its raised + button by 12 px, from the same tokens', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/shell/toast-outlet/toast-outlet.scss'), 'utf8');
    const nav = readFileSync(join(process.cwd(), 'src/app/components/mobile-nav/mobile-nav.scss'), 'utf8');
    const styles = readFileSync(join(process.cwd(), 'src/styles.scss'), 'utf8');
    expect(styles).toMatch(/--m-nav-h:\s*64px/);
    expect(styles).toMatch(/--m-plus-lift:\s*14px/);
    expect(nav).toContain('height: var(--m-nav-h)');
    expect(nav).toContain('margin: calc(-1 * var(--m-plus-lift))');
    expect(css).toContain('bottom: calc(var(--m-nav-h) + var(--m-plus-lift) + env(safe-area-inset-bottom, 0px) + 12px)');
  });

  it('a click on 重試 over a dismissable-mask dialog retries and leaves the dialog open', () => {
    const wrapper = document.createElement('div'); // PrimeNG listens for mousedown on the wrapper and closes only when target === wrapper
    const closed = vi.fn();
    wrapper.addEventListener('mousedown', event => {
      if (wrapper.isSameNode(event.target as Node)) closed();
    });
    const documentClick = vi.fn(); // PrimeNG overlays (select panel) treat a document click outside as dismiss
    document.addEventListener('click', documentClick);
    document.body.appendChild(wrapper);
    const retry = vi.fn();
    scope.error('失敗', { retry });
    flush();
    const button = q('.toast-action')!;
    button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    button.click();
    document.removeEventListener('click', documentClick);
    wrapper.remove();
    expect(retry).toHaveBeenCalledTimes(1);
    expect(closed).not.toHaveBeenCalled();
    expect(documentClick).not.toHaveBeenCalled();
  });

  it('no PrimeNG toast remains', () => {
    const root = join(process.cwd(), 'src/app');
    const sources = (readdirSync(root, { recursive: true }) as string[])
      .filter(file => /\.(ts|html|scss)$/.test(file) && !file.endsWith('.spec.ts'));
    expect(sources.length).toBeGreaterThan(100);
    const hits = sources.filter(file => /\bp-toast\b|primeng\/toast|MessageService/.test(readFileSync(join(root, file), 'utf8')));
    expect(hits).toEqual([]);
  });
});
