import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { AccountingOverviewService } from './overview.service';

describe('AccountingOverviewService', () => {
  it('leaves visible untouched on clear()', () => {
    const service = TestBed.inject(AccountingOverviewService);
    service.visible.set(true);
    service.clear();
    expect(service.visible()).toBe(true);
  });
});
