import { HttpErrorResponse } from '@angular/common/http';

import type { ToastScope } from '../../services/toast.service';
import { IMPORT_RUNNING_TOAST, isImportRunning } from './schedule-math';

interface ValidationItem {
  loc?: unknown[];
  msg?: unknown;
}

/**
 * 422 body `{"detail": [{"loc": [...], "msg": ..., "type": "value_error"}]}` (the write services' `ValidationError`,
 * Task 12's `service_errors()`, and FastAPI's own request validation) → `{ field: message }`, keyed by the last string
 * in `loc` that is not `body` (split members arrive as one dotted string, `members.<i>.<field>`).
 * A 422 rewritten by shared_lib (`{"code", "message", "trace_id"}`) is kept under the `_` key.
 */
export function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof HttpErrorResponse) || err.status !== 422) {
    return {};
  }
  const body = err.error as { detail?: unknown; message?: unknown } | null;
  const detail = body?.detail;
  if (!Array.isArray(detail)) {
    return typeof body?.message === 'string' && body.message ? { _: body.message } : {};
  }
  const result: Record<string, string> = {};
  for (const item of detail as ValidationItem[]) {
    const loc = Array.isArray(item?.loc) ? item.loc : [];
    const field = [...loc].reverse().find((part): part is string => typeof part === 'string' && part !== 'body') ?? '_';
    result[field] = String(item?.msg ?? '格式錯誤');
  }
  return result;
}

/** 409 codes (schedule `{"detail": "<code>"}`, shared_lib `message` = `<code>` or `<code>: <detail>`) in the owner's words. */
const CONFLICT_MESSAGES: Record<string, string> = {
  definition_ended: '排程已結束，無法編輯',
  already_posted: '此期已入帳',
  skipped: '此期已略過',
  definition_changed: '排程剛變更，請重試',
  busy: '排程工作正在執行，請稍後再試',
  retry: '記錄剛被更新，請重新載入後再試',
  member_locked: '子項已受保護，請重新載入後再試',
  already_grouped: '此記錄已屬於群組，請重新載入',
  entry_locked: '此記錄目前不能拆帳',
  kind_not_splittable: '此記錄類型不能拆帳',
  import_running: IMPORT_RUNNING_TOAST,
  group_scheduled: '排程產生的群組不可在此修改',
  // #396: the form did not load this child (added since), so its save did not confirm dropping it
  children_unconfirmed: '這筆記錄的手續費或折扣是在你開啟後才加上的；請重新載入後再編輯',
  // #390: the delete dialog did not list this child (added since the load, or under the other transfer leg)
  cascade_unconfirmed: '附帶的手續費或折扣記在別的帳戶；請重新載入後再刪，或先刪那筆附帶記錄',
};

/** The owner's wording of a 409 conflict code, or `null` when the code is not one the app maps. */
export function conflictReason(code: string): string | null {
  return Object.hasOwn(CONFLICT_MESSAGES, code) ? CONFLICT_MESSAGES[code] : null;
}

/** shared_lib `message` (or a schedule's string `detail`) of an error body, else `null`. */
function bodyText(err: HttpErrorResponse): string | null {
  const body = err.error as { detail?: unknown; message?: unknown } | null;
  return typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : null;
}

/**
 * The owner-worded line for the failures the app maps itself (import running, the cutover lock, the 409 codes in
 * `CONFLICT_MESSAGES`, a missing group member), else `null`: raw server text, validation lists and network failures
 * are not the app's words.
 */
export function knownWriteReason(err: unknown): string | null {
  if (isImportRunning(err)) {
    return IMPORT_RUNNING_TOAST;
  }
  if (!(err instanceof HttpErrorResponse)) {
    return null;
  }
  // The business code is the message's prefix (`retry: member 7 changed`), never the generic HTTP `code`.
  const code = bodyText(err)?.split(':', 1)[0].trim();
  if (err.status === 409 && code === 'locked_until_cutover') {
    return 'MOZE 匯入資料，切換後可編輯';
  }
  if (err.status === 409 && code) {
    return conflictReason(code);
  }
  if (err.status === 404 && code === 'member_not_found') {
    return '找不到群組中的子項，請重新載入';
  }
  return null;
}

/**
 * One human-readable line for a failed write. 404 / 409 bodies are shared_lib's `{"code", "message", "trace_id"}`
 * whose `message` starts with the business code (`locked_until_cutover`, `retry: …`, `member_locked: …`); 422
 * bodies carry a `detail` list.
 */
export function writeErrorMessage(err: unknown): string {
  const known = knownWriteReason(err);
  if (known !== null) {
    return known;
  }
  if (err instanceof HttpErrorResponse) {
    const body = err.error as { detail?: unknown; message?: unknown } | null;
    const detail = body?.detail;
    if (typeof body?.message === 'string' && body.message) {
      return body.message;
    }
    if (typeof detail === 'string' && detail) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      return String((detail[0] as ValidationItem)?.msg ?? '資料格式錯誤');
    }
    if (err.status === 0) {
      return '無法連線到伺服器';
    }
  }
  return '儲存失敗，請稍後再試';
}

/** A save that may not have reached the server (offline, 5xx): safe to offer 重試. */
export function isNetworkFailure(err: unknown): boolean {
  return err instanceof HttpErrorResponse && (err.status === 0 || err.status >= 500);
}

/** `run` as the toast's retry for a network failure, else `undefined` (a 4xx says what to fix). */
export function retryOnNetwork(err: unknown, run: () => void): (() => void) | undefined {
  return isNetworkFailure(err) ? run : undefined;
}

/** A failed schedule action: `import_running` raises the 失敗 toast (page state unchanged) → null; else the error line. */
export function scheduleActionError(err: unknown, toasts: ToastScope): string | null {
  if (isImportRunning(err)) {
    toasts.error(IMPORT_RUNNING_TOAST);
    return null;
  }
  return writeErrorMessage(err);
}
