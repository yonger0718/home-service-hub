/// <reference types="node" />
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Legacy PrimeNG forms that use the Bootstrap grid on purpose (design D1); shrink this list as they are rebuilt. */
const ALLOWLIST = [
  'src/app/components/item-list/item-list.html',
  'src/app/components/portfolio/dividend-list/dividend-list.html',
  'src/app/components/portfolio/transaction-list/transaction-list.html',
  'src/app/components/accounting/account-settings/account-settings.html',
];
const GRID_TOKEN = /^(row|col|col-(\d{1,2}|auto|(sm|md|lg|xl|xxl)(-\d{1,2}|-auto)?)|container(-fluid)?)$/;

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    const ok = e.name.endsWith('.html') || (e.name.endsWith('.ts') && !e.name.endsWith('.spec.ts'));
    return ok ? [p] : [];
  });
}

/** `file:line` for every static `class="…"` attribute holding a Bootstrap grid token. */
function gridOffenders(root: string): string[] {
  const allowed = new Set(ALLOWLIST.map((p) => join(process.cwd(), p)));
  const out: string[] = [];
  for (const file of walk(root)) {
    if (allowed.has(file)) continue;
    readFileSync(file, 'utf8')
      .split('\n')
      .forEach((line, i) => {
        for (const m of line.matchAll(/\bclass="([^"]*)"/g)) {
          if (m[1].split(/\s+/).some((t) => GRID_TOKEN.test(t))) {
            out.push(`${relative(process.cwd(), file)}:${i + 1}`);
            break;
          }
        }
      });
  }
  return out;
}

describe('Bootstrap grid class names (polish-layout-v2 D1)', () => {
  it('flags only whole grid tokens', () => {
    expect('row'.match(GRID_TOKEN)).not.toBeNull();
    expect('col-6'.match(GRID_TOKEN)).not.toBeNull();
    for (const ok of ['row-name', 'entry-row', 'field-row', 'rows', 'column', 'col-span']) expect(ok.match(GRID_TOKEN), ok).toBeNull();
  });
  it('no component template outside the allowlist uses a Bootstrap grid class', () => {
    expect(gridOffenders(join(process.cwd(), 'src/app'))).toEqual([]);
  });
  it('every allowlisted file still exists', () => {
    for (const p of ALLOWLIST) expect(existsSync(join(process.cwd(), p)), p).toBe(true);
  });
});
