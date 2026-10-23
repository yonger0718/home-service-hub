import { ImportRun } from '../../models/accounting.model';

/** Rows of the latest import awaiting review (帳戶 page and nav badge): `needs_review.count`, else the unpaired transfers. */
export function importReviewCount(run: ImportRun | null | undefined): number {
  const summary = run?.summary as unknown as
    | { needs_review?: { count?: number }; unpaired_transfers?: unknown[] }
    | null
    | undefined;
  return summary?.needs_review?.count ?? summary?.unpaired_transfers?.length ?? 0;
}
