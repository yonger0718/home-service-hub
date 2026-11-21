import { isNetworkFailure } from '../components/accounting/http-errors';
import type { ToastScope } from './toast.service';

/**
 * 重試 for a failed save: only for a network-level failure. Pressed while the same kind of save is in flight
 * (`busy()`), it sends nothing and shows the same 失敗 toast again, so the owner can press it once that save is done.
 */
export function guardedRetry(
  err: unknown,
  toasts: ToastScope,
  text: string,
  busy: () => boolean,
  run: () => void,
): (() => void) | undefined {
  if (!isNetworkFailure(err)) {
    return undefined;
  }
  const retry = (): void => {
    if (busy()) {
      toasts.error(text, { retry });
      return;
    }
    run();
  };
  return retry;
}
