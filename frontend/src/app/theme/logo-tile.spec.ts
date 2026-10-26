/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (rel: string): string => readFileSync(join(process.cwd(), rel), 'utf8');

/** Text of the first top-level block opened by `selector {`, up to its closing brace at column 0. */
function block(css: string, selector: string): string {
  const start = css.indexOf(`\n${selector} {`);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  const end = css.indexOf('\n}', start + 1);
  return css.slice(start, end);
}

describe('account logo tile (#175)', () => {
  const css = read('src/styles.scss');
  const count = (s: string): number => s.split('--app-logo-tile: #fff;').length - 1;

  it('defines the tile token once in the light block and once in the dark block', () => {
    expect(count(block(css, ':root'))).toBe(1);
    expect(count(block(css, '.app-dark-mode'))).toBe(1);
  });

  it('component styles paint the tile on .tile, never on :host', () => {
    const src = read('src/app/components/accounting/account-icon/account-icon.ts');
    expect(src).toMatch(/\.tile \{[^}]*background: var\(--app-logo-tile\)/);
    expect(src).not.toMatch(/:host \{[^}]*background/);
  });
});
