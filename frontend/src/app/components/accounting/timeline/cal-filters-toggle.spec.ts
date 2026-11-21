import { readFileSync } from 'node:fs';
import { join } from 'node:path';

describe('cal-filters-toggle styles', () => {
  const src = readFileSync(join(process.cwd(), 'src/app/components/accounting/timeline/cal-filters-toggle.ts'), 'utf8');

  it('has an explicit focus-visible ring from the app token', () => {
    const block = src.match(/\.cal-filters-toggle:focus-visible \{([^}]*)\}/)?.[1] ?? '';
    expect(block).toContain('outline: none');
    expect(block).toContain('box-shadow: 0 0 0 3px var(--app-focus-ring)');
  });
});
