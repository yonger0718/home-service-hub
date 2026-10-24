/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const C = 'src/app/components';
const read = (rel: string): string => readFileSync(join(process.cwd(), rel), 'utf8');

const PORTFOLIO = [
  `${C}/portfolio/transaction-list/transaction-list.html`,
  `${C}/portfolio/dividend-list/dividend-list.html`,
  `${C}/portfolio/realized-pnl/realized-pnl.html`,
  `${C}/portfolio/accounts/account-detail.html`,
];
const ALL = [...PORTFOLIO, `${C}/accounting/timeline/timeline.html`];

describe('filter bar adoption (polish-layout-v2 D6)', () => {
  it('every list page uses <app-filter-bar>', () => {
    for (const f of ALL) expect(read(f), f).toContain('<app-filter-bar');
  });

  it('the portfolio templates no longer carry their own 清除篩選', () => {
    for (const f of PORTFOLIO) expect(read(f), f).not.toContain('清除篩選');
  });

  it('the legacy hub-filter-bar class is gone from account-detail and styles.scss', () => {
    expect(read(`${C}/portfolio/accounts/account-detail.html`)).not.toContain('hub-filter-bar');
    expect(read('src/styles.scss')).not.toContain('hub-filter-bar');
  });

  it('the accounting timeline dropped its search-toggle', () => {
    expect(read(`${C}/accounting/timeline/timeline.html`)).not.toContain('search-toggle');
  });

  it('styles.scss defines the shared .hub-filter-field', () => {
    expect(read('src/styles.scss')).toContain('.hub-filter-field');
  });
});
