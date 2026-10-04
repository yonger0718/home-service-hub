import { HttpErrorResponse } from '@angular/common/http';

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

/**
 * One human-readable line for a failed write. 404 / 409 bodies are shared_lib's `{"code", "message", "trace_id"}`
 * (the lock refusal has `message == "locked_until_cutover"`; an ended schedule's PUT `definition_ended`); 422 bodies
 * carry a `detail` list.
 */
export function writeErrorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const body = err.error as { detail?: unknown; message?: unknown } | null;
    const detail = body?.detail;
    if (err.status === 409 && (detail === 'locked_until_cutover' || body?.message === 'locked_until_cutover')) {
      return 'MOZE 匯入資料，切換後可編輯';
    }
    if (err.status === 409 && (detail === 'definition_ended' || body?.message === 'definition_ended')) {
      return '排程已結束，無法編輯';
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
