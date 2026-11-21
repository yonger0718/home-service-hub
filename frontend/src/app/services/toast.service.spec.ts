import { DestroyRef, EnvironmentInjector, createEnvironmentInjector, runInInjectionContext } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TOAST_DONE_MS, ToastScope, ToastService, injectToasts, toastText } from './toast.service';

describe('ToastService', () => {
  let s: ToastService;
  let t: ToastScope;
  let ownerInjector: EnvironmentInjector;
  const child = () => createEnvironmentInjector([], TestBed.inject(EnvironmentInjector));
  const scopeOf = (injector: EnvironmentInjector) => injector.get(ToastService).scope(injector.get(DestroyRef));

  beforeEach(() => {
    vi.useFakeTimers();
    s = TestBed.inject(ToastService);
    ownerInjector = child();
    t = scopeOf(ownerInjector);
  });
  afterEach(() => vi.useRealTimers());

  it('shows one toast at a time: a newer call replaces the current one', () => {
    t.done('已確認');
    t.error('儲存失敗，網路中斷');
    expect(s.current()).toEqual(expect.objectContaining({ kind: 'error', text: '儲存失敗，網路中斷' }));
  });

  it('完成 disappears after 5 s', () => {
    expect(TOAST_DONE_MS).toBe(5000);
    t.done('已確認');
    vi.advanceTimersByTime(4999);
    expect(s.current()).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(s.current()).toBeNull();
  });

  it('失敗 stays until it is dismissed', () => {
    const id = t.error('儲存失敗');
    vi.advanceTimersByTime(60_000);
    expect(s.current()?.id).toBe(id);
    s.dismiss(id);
    expect(s.current()).toBeNull();
  });

  it('進行中 stays until its result replaces it', () => {
    t.progress('資料重算執行中…');
    vi.advanceTimersByTime(60_000);
    expect(s.current()).toEqual(expect.objectContaining({ kind: 'progress', action: null }));
    t.done('重算完成');
    expect(s.current()?.kind).toBe('done');
  });

  it('a replaced 完成 timer never clears the newer toast', () => {
    t.done('A');
    vi.advanceTimersByTime(3000);
    t.error('B');
    vi.advanceTimersByTime(5000);
    expect(s.current()?.text).toBe('B');
  });

  it('a replaced timer leaves no timer behind', () => {
    t.done('A');
    expect(vi.getTimerCount()).toBe(1);
    t.error('B');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('pause reasons are not carried from one toast to the next', () => {
    t.error('A', { retry: vi.fn() });
    s.pause('focus');
    s.pause('hover');
    t.done('B');
    vi.advanceTimersByTime(5000);
    expect(s.current()).toBeNull();
  });

  it('dismiss(id) of an older toast leaves the current one', () => {
    const old = t.error('A');
    t.error('B');
    s.dismiss(old);
    t.dismiss(old);
    expect(s.current()?.text).toBe('B');
  });

  it('labels: undo → 復原 on 完成, retry → 重試 on 失敗, none otherwise', () => {
    t.done('已確認', { undo: vi.fn() });
    expect(s.current()?.action?.label).toBe('復原');
    t.done('已確認');
    expect(s.current()?.action).toBeNull();
    t.error('失敗', { retry: vi.fn() });
    expect(s.current()?.action?.label).toBe('重試');
    t.error('失敗');
    expect(s.current()?.action).toBeNull();
    t.progress('執行中');
    expect(s.current()?.action).toBeNull();
  });

  it('runAction runs the callback once and dismisses by its own id', () => {
    const retry = vi.fn();
    const id = t.error('帳戶讀取失敗', { retry });
    s.runAction(id);
    s.runAction(id);
    expect(retry).toHaveBeenCalledTimes(1);
    expect(s.current()).toBeNull();
  });

  it('runAction keeps a toast the callback raises synchronously', () => {
    const id = t.error('匯入失敗', { retry: () => t.progress('資料重算執行中…') });
    s.runAction(id);
    expect(s.current()).toEqual(expect.objectContaining({ kind: 'progress', text: '資料重算執行中…' }));
  });

  it('runAction on a toast that is not current does nothing', () => {
    const retry = vi.fn();
    const id = t.error('A', { retry });
    t.done('B');
    s.runAction(id);
    expect(retry).not.toHaveBeenCalled();
    expect(s.current()?.text).toBe('B');
  });

  it('pause holds while hover OR focus is held; resume only when both are gone', () => {
    t.done('A');
    vi.advanceTimersByTime(3000);
    s.pause('hover');
    s.pause('focus');
    s.resume('hover');
    vi.advanceTimersByTime(10_000);
    expect(s.current()).not.toBeNull();
    s.resume('focus');
    vi.advanceTimersByTime(1999);
    expect(s.current()).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(s.current()).toBeNull();
  });

  it('the reverse order (focus then hover released) also resumes once', () => {
    t.done('A');
    vi.advanceTimersByTime(1000);
    s.pause('hover');
    s.pause('focus');
    s.resume('focus');
    vi.advanceTimersByTime(10_000);
    expect(s.current()).not.toBeNull();
    s.resume('hover');
    vi.advanceTimersByTime(3999);
    expect(s.current()).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(s.current()).toBeNull();
  });

  it('a resume without a matching pause changes nothing', () => {
    t.done('A');
    vi.advanceTimersByTime(2000);
    s.resume('hover');
    vi.advanceTimersByTime(2999);
    expect(s.current()).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(s.current()).toBeNull();
  });

  it('an owned 失敗 keeps its text but loses 重試 when the owner is destroyed', () => {
    t.error('無法取得重算狀態', { retry: vi.fn() });
    ownerInjector.destroy();
    expect(s.current()).toEqual(expect.objectContaining({ text: '無法取得重算狀態', action: null }));
  });

  it('an owned 進行中 is removed when the owner is destroyed', () => {
    t.progress('資料重算執行中…');
    ownerInjector.destroy();
    expect(s.current()).toBeNull();
  });

  it('a destroyed owner can no longer raise a 進行中', () => {
    ownerInjector.destroy();
    expect(t.progress('x')).toBe(-1);
    expect(s.current()).toBeNull();
  });

  it('a 完成 from a destroyed owner still shows, without 復原', () => {
    ownerInjector.destroy();
    const id = t.done('已完成', { undo: vi.fn() });
    expect(id).toBeGreaterThan(0);
    expect(s.current()).toEqual(expect.objectContaining({ id, kind: 'done', text: '已完成', action: null }));
  });

  it('a 失敗 from a destroyed owner still shows, without 重試', () => {
    ownerInjector.destroy();
    const id = t.error('匯入進行中，請稍後再試', { retry: vi.fn() });
    expect(s.current()).toEqual(expect.objectContaining({ id, kind: 'error', text: '匯入進行中，請稍後再試', action: null }));
  });

  it('a toast replaced before its owner dies is untouched by that destroy', () => {
    const otherInjector = child();
    const other = scopeOf(otherInjector);
    t.error('A', { retry: vi.fn() });
    other.error('B', { retry: vi.fn() });
    ownerInjector.destroy();
    expect(s.current()?.text).toBe('B');
    expect(s.current()?.action?.label).toBe('重試');
    otherInjector.destroy();
  });

  it('injectToasts binds the scope to the calling injection context', () => {
    const scope = runInInjectionContext(ownerInjector, () => injectToasts());
    const id = scope.progress('執行中');
    expect(s.current()?.id).toBe(id);
    ownerInjector.destroy();
    expect(s.current()).toBeNull();
    expect(scope.progress('x')).toBe(-1);
  });

  it('toastText joins with a full-width colon and drops an empty detail', () => {
    expect(toastText('匯入失敗', '檔案格式錯誤')).toBe('匯入失敗：檔案格式錯誤');
    expect(toastText('匯入失敗', '')).toBe('匯入失敗');
    expect(toastText('匯入失敗', null)).toBe('匯入失敗');
    expect(toastText('匯入失敗')).toBe('匯入失敗');
  });
});
