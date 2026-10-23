/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * Contrast (8.3): the ink variants for small trend-coloured text and the dark-mode primary button fill must keep
 * WCAG 2.x AA (4.5:1) on the surfaces they sit on, in both themes. Hex values are parsed from the stylesheets, so a
 * drifting token fails here.
 */

const src = (path: string) => readFileSync(join(process.cwd(), path), 'utf8');
const styles = src('src/styles.scss');
const btnScss = src('src/app/components/ui/btn/btn.scss');
const layoutScss = src('src/app/components/accounting/accounting-layout/accounting-layout.scss');
const timelineScss = src('src/app/components/accounting/timeline/timeline.scss');

const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** Body of the first rule whose selector line starts (at column 0) with `selector {`, braces matched. */
function block(css: string, selector: string): string {
  const text = stripComments(css);
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`^${escaped}\\s*\\{`, 'm').exec(text);
  if (!m) throw new Error(`block not found: ${selector}`);
  let depth = 1;
  let i = m.index + m[0].length;
  const start = i;
  for (; i < text.length && depth > 0; i++) {
    if (text[i] === '{') depth++;
    else if (text[i] === '}') depth--;
  }
  return text.slice(start, i - 1);
}

/** Custom properties declared directly in a block body (nested rules are dropped). */
function tokens(body: string): Record<string, string> {
  let flat = '';
  let depth = 0;
  for (const ch of body) {
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (depth === 0) flat += ch;
  }
  const out: Record<string, string> = {};
  for (const m of flat.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

function hex(map: Record<string, string>, name: string): string {
  const value = map[name];
  expect(value, `${name} declared`).toBeDefined();
  expect(value, `${name} is a hex literal`).toMatch(/^#[0-9a-f]{6}$/i);
  return value;
}

function luminance(color: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(color.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.x contrast ratio. */
function ratio(fg: string, bg: string): number {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

const light = tokens(block(styles, ':root'));
const dark = tokens(block(styles, '.app-dark-mode'));

describe('contrast helpers', () => {
  it('ratio follows WCAG 2.x', () => {
    expect(ratio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(ratio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
    expect(ratio('#ffffff', '#8b7dff')).toBeCloseTo(3.24, 2);
  });

  it('slices the two theme blocks separately (a first-match regex would read the light value twice)', () => {
    expect(light['--app-surface']).toBe('#fafcff');
    expect(dark['--app-surface']).toBe('#171c24');
    expect(dark['--c-red']).toBe('#ff6369');
  });
});

describe.each([
  ['light', light],
  ['dark', dark],
])('%s theme ink tokens', (_name, theme) => {
  it.each(['--c-red-ink', '--c-green-ink'])('%s ≥ 4.5:1 on --app-surface and --app-surface-soft', (token) => {
    const ink = hex(theme, token);
    expect(ratio(ink, hex(theme, '--app-surface'))).toBeGreaterThanOrEqual(4.5);
    expect(ratio(ink, hex(theme, '--app-surface-soft'))).toBeGreaterThanOrEqual(4.5);
  });
});

describe('ink tokens keep the theme hues', () => {
  it('light ink is darker than the bright token; dark ink keeps the dark hue', () => {
    expect(luminance(hex(light, '--c-red-ink'))).toBeLessThan(luminance(hex(light, '--c-red')));
    expect(luminance(hex(light, '--c-green-ink'))).toBeLessThan(luminance(hex(light, '--c-green')));
    expect(dark['--c-red-ink']).toBe(dark['--c-red']);
    expect(dark['--c-green-ink']).toBe(dark['--c-green']);
  });
});

describe('trend ink pair follows the gain/loss convention', () => {
  it('default block (紅漲綠跌)', () => {
    expect(light['--app-trend-positive-ink']).toBe('var(--c-red-ink)');
    expect(light['--app-trend-negative-ink']).toBe('var(--c-green-ink)');
  });

  it('[data-gainloss="asian"]', () => {
    const asian = tokens(block(styles, '[data-gainloss="asian"]'));
    expect(asian['--app-trend-positive-ink']).toBe('var(--c-red-ink)');
    expect(asian['--app-trend-negative-ink']).toBe('var(--c-green-ink)');
  });

  it('[data-gainloss="western"]', () => {
    const western = tokens(block(styles, '[data-gainloss="western"]'));
    expect(western['--app-trend-positive-ink']).toBe('var(--c-green-ink)');
    expect(western['--app-trend-negative-ink']).toBe('var(--c-red-ink)');
  });
});

describe('dark primary button', () => {
  it('white on --app-primary-ink-bg ≥ 4.5:1 (dark only; light keeps --app-primary)', () => {
    expect(ratio('#ffffff', hex(dark, '--app-primary-ink-bg'))).toBeGreaterThanOrEqual(4.5);
    expect(light['--app-primary-ink-bg']).toBeUndefined();
  });

  it('btn.scss paints the primary fill with the ink bg at rest and on hover', () => {
    const primary = block(btnScss, '.btn-primary');
    const rest = primary.replace(/&[^{]*\{[^}]*\}/g, '');
    expect(rest).toMatch(/background:\s*var\(--app-primary-ink-bg,\s*var\(--app-primary\)\)/);
    const hover = /&:hover[^{]*\{([^}]*)\}/.exec(primary)![1];
    expect(hover).toMatch(/background:\s*var\(--app-primary-ink-bg,\s*var\(--app-primary-hover\)\)/);
    expect(primary).not.toMatch(/&:active/);
  });
});

describe('large figures keep the bright tokens', () => {
  it('the accounting .neg / .pos rule resolves --tone-neg / --tone-pos to the bright --c-red / --c-green', () => {
    expect(block(timelineScss, '.neg')).toMatch(/color:\s*var\(--tone-neg,\s*var\(--c-red\)\)/);
    expect(block(timelineScss, '.pos')).toMatch(/color:\s*var\(--tone-pos,\s*var\(--c-green\)\)/);
    const host = tokens(block(layoutScss, ':host'));
    expect(host['--tone-neg']).toBe('var(--c-red)');
    expect(host['--tone-pos']).toBe('var(--c-green)');
    const swapped = tokens(block(layoutScss, ':host(.green-red)'));
    expect(swapped['--tone-neg']).toBe('var(--c-green)');
    expect(swapped['--tone-pos']).toBe('var(--c-red)');
  });

  it('small-text tone variants are siblings of --tone-*, swapped by the same preference', () => {
    const host = tokens(block(layoutScss, ':host'));
    expect(host['--tone-neg-ink']).toBe('var(--c-red-ink)');
    expect(host['--tone-pos-ink']).toBe('var(--c-green-ink)');
    const swapped = tokens(block(layoutScss, ':host(.green-red)'));
    expect(swapped['--tone-neg-ink']).toBe('var(--c-green-ink)');
    expect(swapped['--tone-pos-ink']).toBe('var(--c-red-ink)');
  });
});
