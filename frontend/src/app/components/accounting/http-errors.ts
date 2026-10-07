import { HttpErrorResponse } from '@angular/common/http';

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
};

/**
 * One human-readable line for a failed write. 404 / 409 bodies are shared_lib's `{"code", "message", "trace_id"}`
 * whose `message` starts with the business code (`locked_until_cutover`, `retry: …`, `member_locked: …`); 422
 * bodies carry a `detail` list.
 */
export function writeErrorMessage(err: unknown): string {
  if (isImportRunning(err)) {
    return IMPORT_RUNNING_TOAST;
  }
  if (err instanceof HttpErrorResponse) {
    const body = err.error as { detail?: unknown; message?: unknown } | null;
    const detail = body?.detail;
    // The business code is the message's prefix (`retry: member 7 changed`), never the generic HTTP `code`.
    const message = typeof body?.message === 'string' ? body.message : typeof detail === 'string' ? detail : null;
    const messageCode = message?.split(':', 1)[0].trim();
    if (err.status === 409 && messageCode === 'locked_until_cutover') {
      return 'MOZE 匯入資料，切換後可編輯';
    }
    if (err.status === 409 && messageCode && Object.hasOwn(CONFLICT_MESSAGES, messageCode)) {
      return CONFLICT_MESSAGES[messageCode];
    }
    if (err.status === 404 && messageCode === 'member_not_found') {
      return '找不到群組中的子項，請重新載入';
    }
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
