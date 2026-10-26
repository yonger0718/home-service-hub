/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ACCOUNT_LOGOS, LOGO_SLUG, accountLogo } from './account-logos';

const DIR = join(__dirname, '../../assets/logos');
/** Single source for the SVG rules, shared with tools/account-logos/normalize.py. */
const RULES: {
  forbidden_elements: string[];
  forbidden_text: string[];
  forbidden_regex: string[];
  max_bytes: number;
} = JSON.parse(
  readFileSync(join(__dirname, '../../../../tools/account-logos/svg-rules.json'), 'utf8'),
);

const svgs = () => ACCOUNT_LOGOS.filter((l) => l.file.endsWith('.svg'));
const parse = (text: string) => new DOMParser().parseFromString(text, 'image/svg+xml');
const numbers = (viewBox: string | null) =>
  (viewBox ?? '')
    .trim()
    .split(/[\s,]+/)
    .map(Number);

/** [width, height]: SVG → the OUTERMOST element's viewBox (never a regex over the text); PNG → IHDR. */
function dimensions(path: string): [number, number] {
  if (path.endsWith('.png')) {
    const b = readFileSync(path);
    return [b.readUInt32BE(16), b.readUInt32BE(20)];
  }
  const [, , w, h] = numbers(
    parse(readFileSync(path, 'utf8')).documentElement.getAttribute('viewBox'),
  );
  return [w, h];
}

describe('bundled institution logos (#175)', () => {
  it('every manifest entry has a square file and every file is in the manifest', () => {
    const files = readdirSync(DIR).filter((f) => f !== 'SOURCES.md');
    expect(new Set(ACCOUNT_LOGOS.map((l) => l.file))).toEqual(new Set(files));
    for (const l of ACCOUNT_LOGOS) {
      expect(l.slug).toMatch(LOGO_SLUG);
      expect(l.file).toBe(`${l.slug}${extname(l.file)}`);
      const [w, h] = dimensions(join(DIR, l.file));
      expect(w, l.file).toBeGreaterThan(0);
      expect(w, l.file).toBe(h);
      expect(statSync(join(DIR, l.file)).size, l.file).toBeLessThanOrEqual(RULES.max_bytes);
      if (l.file.endsWith('.png')) expect(w, l.file).toBeGreaterThanOrEqual(180);
    }
  });

  it("every svg is the normaliser's wrap: one child <svg> centred by the 1.16 formula", () => {
    for (const l of svgs()) {
      const root = parse(readFileSync(join(DIR, l.file), 'utf8')).documentElement;
      expect(root.localName, l.file).toBe('svg');
      const [, , s] = numbers(root.getAttribute('viewBox'));
      expect(root.children.length, l.file).toBe(1);
      const inner = root.children[0];
      expect(inner.localName, l.file).toBe('svg');
      expect(inner.getAttribute('viewBox'), l.file).toBeTruthy();
      const [x, y, w, h] = ['x', 'y', 'width', 'height'].map((a) => Number(inner.getAttribute(a)));
      expect(s, l.file).toBeCloseTo(Math.max(w, h) * 1.16, 2);
      expect(x, l.file).toBeCloseTo((s - w) / 2, 2);
      expect(y, l.file).toBeCloseTo((s - h) / 2, 2);
    }
  });

  it('svg files obey tools/account-logos/svg-rules.json', () => {
    const forbidden = new Set(RULES.forbidden_elements);
    const regexes = RULES.forbidden_regex.map((r) => new RegExp(r, 'i'));
    for (const l of svgs()) {
      const text = readFileSync(join(DIR, l.file), 'utf8');
      for (const t of RULES.forbidden_text)
        expect(text.toLowerCase().includes(t.toLowerCase()), `${l.file}: ${t}`).toBe(false);
      for (const r of regexes) expect(r.test(text), `${l.file}: ${r}`).toBe(false);
      const doc = parse(text);
      expect(doc.getElementsByTagName('parsererror').length, l.file).toBe(0);
      for (const el of Array.from(doc.getElementsByTagName('*'))) {
        expect(forbidden.has(el.localName), `${l.file}: <${el.localName}>`).toBe(false);
        for (const a of Array.from(el.attributes)) {
          expect(a.localName.toLowerCase().startsWith('on'), `${l.file}: ${a.name}`).toBe(false);
          if (a.localName === 'href')
            expect(a.value.startsWith('#'), `${l.file}: ${a.name}=${a.value}`).toBe(true);
        }
      }
    }
  });

  it('slugs are unique and labels non-empty', () => {
    expect(ACCOUNT_LOGOS.length).toBeGreaterThan(0);
    expect(new Set(ACCOUNT_LOGOS.map((l) => l.slug)).size).toBe(ACCOUNT_LOGOS.length);
    for (const l of ACCOUNT_LOGOS) expect(l.label.trim(), l.slug).not.toBe('');
  });

  it('accountLogo resolves a slug and returns undefined for unknown / null', () => {
    expect(accountLogo('cathay-united')?.label).toBe('國泰世華');
    expect(accountLogo('nope')).toBeUndefined();
    expect(accountLogo(null)).toBeUndefined();
    expect(accountLogo(undefined)).toBeUndefined();
  });
});
