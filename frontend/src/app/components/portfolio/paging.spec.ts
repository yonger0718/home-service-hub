import { Observable, of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Paged } from '../../models/portfolio.model';
import { allPages } from './paging';

function pages(total: number) {
  return vi.fn((offset: number): Observable<Paged<number>> => {
    const count = Math.max(0, Math.min(100, total - offset));
    return of({ total, items: Array.from({ length: count }, (_, i) => offset + i + 1) });
  });
}

describe('allPages', () => {
  it('fetches offsets 0, 100, 200 until the total is covered and concatenates them, uncapped', () => {
    const fetch = pages(250);
    let result: { items: number[]; capped: boolean } | null = null;
    allPages(fetch).subscribe(r => (result = r));

    expect(fetch.mock.calls.map(([offset]) => offset)).toEqual([0, 100, 200]);
    expect(result!.items).toEqual(Array.from({ length: 250 }, (_, i) => i + 1));
    expect(result!.capped).toBe(false);
  });

  it('fetches once and returns nothing when there are no rows', () => {
    const fetch = pages(0);
    let result: { items: number[]; capped: boolean } | null = null;
    allPages(fetch).subscribe(r => (result = r));

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ items: [], capped: false });
  });

  it('stops when a page comes back empty before the total is reached, uncapped', () => {
    const fetch = vi.fn((offset: number) => of({ total: 500, items: offset === 0 ? [1] : [] }));
    let result: { items: number[]; capped: boolean } | null = null;
    allPages(fetch).subscribe(r => (result = r));

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(result).toEqual({ items: [1], capped: false });
  });

  it('stops at the page cap and reports capped while rows remain', () => {
    const fetch = pages(500);
    let result: { items: number[]; capped: boolean } | null = null;
    allPages(fetch, 100, 2).subscribe(r => (result = r));

    expect(fetch.mock.calls.map(([offset]) => offset)).toEqual([0, 100]);
    expect(result!.items).toHaveLength(200);
    expect(result!.capped).toBe(true);
  });

  it('is not capped when the last allowed page covers the total exactly', () => {
    const fetch = pages(200);
    let result: { items: number[]; capped: boolean } | null = null;
    allPages(fetch, 100, 2).subscribe(r => (result = r));

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(result!.capped).toBe(false);
  });

  it('defaults to a 50-page cap', () => {
    const fetch = pages(100_000);
    let result: { items: number[]; capped: boolean } | null = null;
    allPages(fetch).subscribe(r => (result = r));

    expect(fetch).toHaveBeenCalledTimes(50);
    expect(result!.items).toHaveLength(5000);
    expect(result!.capped).toBe(true);
  });

  it('honours a custom page size for the offsets', () => {
    const fetch = vi.fn((offset: number) => of({ total: 60, items: Array.from({ length: Math.min(25, 60 - offset) }, (_, i) => offset + i) }));
    allPages(fetch, 25).subscribe();

    expect(fetch.mock.calls.map(([offset]) => offset)).toEqual([0, 25, 50]);
  });

  it('propagates a page error and emits nothing', () => {
    const fetch = vi.fn((offset: number): Observable<Paged<number>> =>
      offset === 0 ? of({ total: 300, items: Array.from({ length: 100 }, (_, i) => i) }) : throwError(() => new Error('boom')),
    );
    const next = vi.fn();
    let error: unknown = null;
    allPages(fetch).subscribe({ next, error: e => (error = e) });

    expect(next).not.toHaveBeenCalled();
    expect((error as Error).message).toBe('boom');
  });

  it('starts from offset 0 again on each subscription', () => {
    const fetch = pages(150);
    const all = allPages(fetch);
    all.subscribe();
    all.subscribe();

    expect(fetch.mock.calls.map(([offset]) => offset)).toEqual([0, 100, 0, 100]);
  });
});
