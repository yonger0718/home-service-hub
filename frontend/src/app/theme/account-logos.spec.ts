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
  forbidden_attr_chars: string[];
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

const SVG_NS = 'http://www.w3.org/2000/svg';

/** Every rule offence in one SVG's text: the raw-text pass, then a DOM walk that re-checks each attribute value and
 *  each element's own text after the parser has decoded character references (`&#117;rl(…)` is `url(…)` in the DOM).
 *  Also rejected: any element outside the SVG namespace (an XHTML `<iframe>` / `<img>` would load in a standalone SVG
 *  document), any processing instruction or doctype at document level, and a backslash anywhere (a CSS escape such
 *  as `\75 rl(` spells `url(` past the regex). */
export function svgOffences(file: string, text: string): string[] {
  const forbidden = new Set(RULES.forbidden_elements);
  const regexes = RULES.forbidden_regex.map((r) => new RegExp(r, 'i'));
  const banned = (where: string, value: string) => [
    ...RULES.forbidden_text
      .filter((t) => value.toLowerCase().includes(t.toLowerCase()))
      .map((t) => `${file}: ${where}: forbidden text "${t}"`),
    ...regexes.filter((r) => r.test(value)).map((r) => `${file}: ${where}: forbidden pattern ${r}`),
    ...RULES.forbidden_attr_chars
      .filter((c) => value.includes(c))
      .map((c) => `${file}: ${where}: forbidden character "${c}"`),
  ];
  const out = banned('raw text', text);
  const doc = parse(text);
  if (doc.getElementsByTagName('parsererror').length > 0) return [...out, `${file}: parse error`];
  for (const n of Array.from(doc.childNodes)) {
    if (n.nodeType === 7) out.push(`${file}: <?${n.nodeName}?>: processing instruction`);
    if (n.nodeType === 10) out.push(`${file}: <!DOCTYPE ${n.nodeName}>: doctype`);
  }
  for (const el of Array.from(doc.getElementsByTagName('*'))) {
    if (el.namespaceURI !== SVG_NS)
      out.push(`${file}: <${el.localName}>: foreign-namespace element`);
    if (forbidden.has(el.localName)) out.push(`${file}: <${el.localName}>: forbidden element`);
    for (const a of Array.from(el.attributes)) {
      const where = `<${el.localName} ${a.name}="${a.value}">`;
      if (a.localName.toLowerCase().startsWith('on')) out.push(`${file}: ${where}: event handler`);
      if (a.localName === 'href' && !a.value.startsWith('#'))
        out.push(`${file}: ${where}: non-fragment href`);
      out.push(...banned(where, a.value));
    }
    const ownText = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3 || n.nodeType === 4)
      .map((n) => n.nodeValue ?? '')
      .join('');
    out.push(...banned(`<${el.localName}> text`, ownText));
  }
  return out;
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
    for (const l of svgs()) {
      expect(svgOffences(l.file, readFileSync(join(DIR, l.file), 'utf8'))).toEqual([]);
    }
  });

  it('reports forbidden content hidden behind character references in attributes and text', () => {
    const svg = (body: string) =>
      `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 4 4">${body}</svg>`;
    expect(svgOffences('ok.svg', svg(`<rect fill="url(#g)" stroke="url('#g')"/>`))).toEqual([]);
    expect(svgOffences('a.svg', svg(`<rect fill="&#117;rl(https://evil/x.svg#g)"/>`))).toEqual([
      expect.stringMatching(
        /^a\.svg: <rect fill="url\(https:\/\/evil\/x\.svg#g\)">: forbidden pattern/,
      ),
    ]);
    expect(
      svgOffences('b.svg', svg(`<rect style="fill:url(&#100;ata:image/png;base64,AA)"/>`)),
    ).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/^b\.svg: <rect style=.*: forbidden text "data:"/),
      ]),
    );
    expect(svgOffences('c.svg', svg(`<text>&#117;rl(//evil/x)</text>`))).toEqual([
      expect.stringMatching(/^c\.svg: <text> text: forbidden pattern/),
    ]);
    expect(svgOffences('d.svg', svg(`<use xlink:href="http://evil/x.svg#a"/>`))).toEqual([
      expect.stringMatching(/^d\.svg: <use xlink:href=.*non-fragment href/),
    ]);
    expect(svgOffences('e.svg', svg(`<rect onclick="x()"/><script/>`))).toHaveLength(2);
    expect(svgOffences('f.svg', '<svg')).toEqual([expect.stringMatching(/^f\.svg: parse error/)]);
  });

  it('reports PIs, CSS escapes, string-URL CSS functions and foreign-namespace elements', () => {
    const NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"';
    const XHTML = 'xmlns="http://www.w3.org/1999/xhtml"';
    const payloads: [string, string, RegExp][] = [
      [
        'pi.svg',
        `<?xml-stylesheet type="text/css" href="https://evil.example/x.css"?><svg ${NS}/>`,
        /: <\?xml-stylesheet\?>: processing instruction$/,
      ],
      [
        'esc.svg',
        `<svg ${NS}><rect style="fill:\\75 rl(https://evil.example/x.svg#g)"/></svg>`,
        /forbidden character "\\"/,
      ],
      [
        'set.svg',
        `<svg ${NS} style="background-image:image-set('https://evil.example/p.png' 1x)"/>`,
        /forbidden pattern \/image-set/,
      ],
      [
        'srcdoc.svg',
        `<svg ${NS}><iframe ${XHTML} srcdoc="&lt;script&gt;1&lt;/script&gt;"/></svg>`,
        /<iframe>: foreign-namespace element/,
      ],
      [
        'js.svg',
        `<svg ${NS}><iframe ${XHTML} src="javascript:1"/></svg>`,
        /<iframe>: foreign-namespace element/,
      ],
      [
        'img.svg',
        `<svg ${NS}><img ${XHTML} src="https://evil.example/t.gif"/></svg>`,
        /<img>: foreign-namespace element/,
      ],
    ];
    for (const [file, text, offence] of payloads) {
      const found = svgOffences(file, text);
      expect(found.length, file).toBeGreaterThan(0);
      expect(found, file).toEqual(expect.arrayContaining([expect.stringMatching(offence)]));
    }
    expect(svgOffences('ok.svg', `<svg ${NS}><rect fill="url(#g)"/></svg>`)).toEqual([]);
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
