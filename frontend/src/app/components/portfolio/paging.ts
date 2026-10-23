import { EMPTY, Observable, defer, expand, map, reduce } from 'rxjs';

import { Paged } from '../../models/portfolio.model';

/**
 * Every row of a paged endpoint: pages at offset 0, pageSize, … until `Paged.total` is covered,
 * an empty page comes back, or `maxPages` pages were read. `capped` is true only when the page cap
 * stopped the loop while rows remained.
 */
export function allPages<T>(
  fetch: (offset: number) => Observable<Paged<T>>,
  pageSize = 100,
  maxPages = 50,
): Observable<{ items: T[]; capped: boolean }> {
  return defer(() => {
    let pages = 0;
    let offset = 0;
    let capped = false;
    return fetch(0).pipe(
      expand(page => {
        pages++;
        offset += pageSize;
        const remaining = page.items.length > 0 && offset < page.total;
        if (remaining && pages >= maxPages) {
          capped = true;
          return EMPTY;
        }
        return remaining ? fetch(offset) : EMPTY;
      }, 1),
      reduce((all, page) => all.concat(page.items), [] as T[]),
      map(items => ({ items, capped })),
    );
  });
}
