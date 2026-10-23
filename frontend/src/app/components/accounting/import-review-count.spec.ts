import { describe, expect, it } from 'vitest';

import { ImportRun } from '../../models/accounting.model';
import { importReviewCount } from './import-review-count';

function run(summary: unknown): ImportRun {
  return {
    id: 1,
    status: 'succeeded',
    started_at: '2026-10-22T01:00:00Z',
    finished_at: '2026-10-22T01:01:00Z',
    file_name: 'backup.zip',
    file_sha256: 'abc',
    row_count: 10,
    summary: summary as ImportRun['summary'],
  };
}

describe('importReviewCount', () => {
  it('is 0 without a run', () => {
    expect(importReviewCount(null)).toBe(0);
    expect(importReviewCount(undefined)).toBe(0);
  });

  it('reads needs_review.count', () => {
    expect(importReviewCount(run({ needs_review: { count: 3 } }))).toBe(3);
  });

  it('falls back to the unpaired transfers', () => {
    expect(importReviewCount(run({ unpaired_transfers: [1, 2] }))).toBe(2);
  });

  it('needs_review wins over unpaired transfers', () => {
    expect(importReviewCount(run({ needs_review: { count: 1 }, unpaired_transfers: [1, 2] }))).toBe(1);
  });
});
