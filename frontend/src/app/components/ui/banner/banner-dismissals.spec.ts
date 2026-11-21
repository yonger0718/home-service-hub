import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { BANNER_DISMISSALS_KEY, BannerDismissals } from './banner-dismissals';

describe('BannerDismissals', () => {
  beforeEach(() => localStorage.removeItem(BANNER_DISMISSALS_KEY));
  afterEach(() => localStorage.removeItem(BANNER_DISMISSALS_KEY));

  it('reads a stored list once', () => {
    localStorage.setItem(BANNER_DISMISSALS_KEY, JSON.stringify(['a-info']));
    const svc = TestBed.inject(BannerDismissals);
    expect(svc.isDismissed('a-info')).toBe(true);
    expect(svc.isDismissed('b-info')).toBe(false);
    localStorage.setItem(BANNER_DISMISSALS_KEY, JSON.stringify(['b-info']));
    expect(svc.isDismissed('a-info')).toBe(true);
    expect(svc.isDismissed('b-info')).toBe(false);
  });

  it('ignores a malformed value', () => {
    localStorage.setItem(BANNER_DISMISSALS_KEY, '{not json');
    expect(TestBed.inject(BannerDismissals).isDismissed('x')).toBe(false);
    TestBed.resetTestingModule();
    localStorage.setItem(BANNER_DISMISSALS_KEY, JSON.stringify({ a: 1 }));
    expect(TestBed.inject(BannerDismissals).isDismissed('a')).toBe(false);
  });

  it('dismiss is idempotent', () => {
    const svc = TestBed.inject(BannerDismissals);
    svc.dismiss('x-info');
    svc.dismiss('x-info');
    expect(svc.isDismissed('x-info')).toBe(true);
    expect(JSON.parse(localStorage.getItem(BANNER_DISMISSALS_KEY) ?? '[]')).toEqual(['x-info']);
  });
});
