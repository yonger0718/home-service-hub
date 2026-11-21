import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it, vi } from 'vitest';
import { guardedRetry } from './toast-retry';
import type { ToastScope } from './toast.service';

describe('guardedRetry', () => {
  const network = new HttpErrorResponse({ status: 0 });
  const scope = (): ToastScope & { error: ReturnType<typeof vi.fn> } =>
    ({ done: vi.fn(), progress: vi.fn(), error: vi.fn(() => 42), dismiss: vi.fn() });

  it('offers nothing for a failure the server answered', () => {
    expect(guardedRetry(new HttpErrorResponse({ status: 422 }), scope(), 't', () => false, () => undefined)).toBeUndefined();
  });

  it('runs when idle', () => {
    const run = vi.fn();
    guardedRetry(network, scope(), 't', () => false, run)!();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('while busy it sends nothing, raises the same 失敗 + 重試 again and reports the new toast id', () => {
    const toasts = scope();
    const run = vi.fn();
    const raised = vi.fn();
    guardedRetry(network, toasts, 't', () => true, run, raised)!();
    expect(run).not.toHaveBeenCalled();
    expect(toasts.error).toHaveBeenCalledWith('t', { retry: expect.any(Function) });
    expect(raised).toHaveBeenCalledWith(42);
  });
});
