import { SplitResult } from '../../../models/accounting.model';
import { ChildDraft } from './split-draft';

const MISSING_KEYS = '儲存回應缺少子項對應，請重新載入';

/** Saved member ids by submitted child key; throws when the answer cannot be mapped one-to-one. */
export function resultIds(result: SplitResult, submitted: readonly ChildDraft[]): Map<string, number> {
  const ids = new Map<string, number>();
  for (const member of result.members ?? []) {
    if (member.client_key === null || ids.has(member.client_key)) {
      throw new Error(MISSING_KEYS);
    }
    ids.set(member.client_key, member.id);
  }
  if (ids.size !== submitted.length || submitted.some(child => !ids.has(child.key))) {
    throw new Error(MISSING_KEYS);
  }
  return ids;
}

/**
 * The child a 422 names (`members.{i}.{field}`, as the service's dotted loc or FastAPI's split loc, union-tag
 * segments skipped), resolved through the keys in submitted order; null for any other error.
 */
export function memberError(error: unknown, keys: readonly string[]): { key: string; field: string; message: string } | null {
  if (typeof error !== 'object' || error === null || !('error' in error)) {
    return null;
  }
  const body = (error as { error: unknown }).error;
  if (typeof body !== 'object' || body === null || !('detail' in body)) {
    return null;
  }
  const detail = (body as { detail: unknown }).detail;
  const entries: Array<[string, string]> = [];
  if (Array.isArray(detail)) {
    for (const item of detail as Array<{ loc?: unknown; msg?: unknown } | null>) {
      if (item && Array.isArray(item.loc)) {
        entries.push([item.loc.join('.'), String(item.msg)]);
      }
    }
  } else if (detail && typeof detail === 'object') {
    for (const [field, message] of Object.entries(detail)) {
      entries.push([field, String(message)]);
    }
  }
  for (const [path, message] of entries) {
    const match = /(?:^|\.)members\.(\d+)\.(?:(?:SplitMemberIn|SplitKeepIn|full|keep|function-after\[[^\]]+\])\.)*(.+)$/.exec(path);
    const key = match ? keys[Number(match[1])] : undefined;
    if (match && key !== undefined) {
      return { key, field: match[2], message };
    }
  }
  return null;
}
