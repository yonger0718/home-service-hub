import { TestBed } from '@angular/core/testing';
import { Router, convertToParamMap, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { groupParentUrl } from './group-nav';

describe('groupParentUrl', () => {
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    router = TestBed.inject(Router);
  });

  it('carries the calendar month and day from the query to the timeline parent', () => {
    const query = convertToParamMap({ month: '2026-10', day: '2026-10-21', select: 'parent' });
    expect(groupParentUrl(router, {}, null, query)).toBe('/accounting?month=2026-10&day=2026-10-21');
    expect(groupParentUrl(router, { closeTo: 'list' }, null, query)).toBe('/accounting?month=2026-10&day=2026-10-21');
  });

  it('is the bare timeline without a query or calendar keys', () => {
    expect(groupParentUrl(router, {}, null)).toBe('/accounting');
    expect(groupParentUrl(router, {}, null, convertToParamMap({ select: 'parent' }))).toBe('/accounting');
  });

  it('keeps the passbook and the reminder centre as they are', () => {
    const query = convertToParamMap({ month: '2026-10', day: '2026-10-21' });
    expect(groupParentUrl(router, {}, 3, query)).toBe('/accounting/accounts/3');
    expect(groupParentUrl(router, { closeTo: 'reminders' }, null, query)).toBe('/accounting/reminders');
  });
});
