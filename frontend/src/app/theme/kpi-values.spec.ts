/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const P = 'src/app/components/portfolio';
const read = (rel: string): string => readFileSync(join(process.cwd(), rel), 'utf8');

const PAGES = ['dashboard/dashboard', 'transaction-list/transaction-list', 'dividend-list/dividend-list', 'realized-pnl/realized-pnl'];

describe('KPI and summary values fit their card (polish-layout-v2 D3)', () => {
  const shared = read(`${P}/kpi-cards.scss`);

  it('the shared stylesheet clamps values inside an inline-size container', () => {
    for (const s of [
      'clamp(18px, 12cqi, var(--fs-value))',
      'white-space: nowrap',
      'container-type: inline-size',
      'repeat(2, minmax(0, 1fr))',
      '--bento-pad: var(--space-4)',
    ]) {
      expect(shared, s).toContain(s);
    }
  });

  it('the bento padding is overridable through --bento-pad', () => {
    expect(read('src/app/components/ui/bento/bento.scss')).toContain('var(--bento-pad, var(--space-6))');
  });

  it.each(PAGES)('%s.scss no longer sets its own KPI value size', (page) => {
    const css = read(`${P}/${page}.scss`);
    expect(css).not.toMatch(/\.value\s*\{[^}]*font-size/);
    expect(css).not.toContain('font-size: 1.35rem');
  });

  it.each(PAGES)('%s.ts loads kpi-cards.scss', (page) => {
    expect(read(`${P}/${page}.ts`)).toMatch(/styleUrls:\s*\[[^\]]*kpi-cards\.scss/);
  });

  it('現金帳戶 figures clamp too', () => {
    const list = read(`${P}/accounts/accounts-list.scss`);
    expect(list).toContain('container-type: inline-size');
    expect(list).toContain('clamp(18px, 12cqi, var(--fs-value))');
    const detail = read(`${P}/accounts/account-detail.scss`);
    expect(detail).toContain('container-type: inline-size');
    expect(detail).toContain('clamp(18px, 12cqi, var(--fs-value))');
  });
});
