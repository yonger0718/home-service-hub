/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Emoji / pictograph ranges: misc technical (⏰), misc symbols + dingbats, arrows/shapes (⭐), SMP pictographs. */
const EMOJI = /[\u{2300}-\u{23FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{1F000}-\u{1FAFF}]/gu;
/** ✕ ✓ as text glyphs (polish-layout R5); ⏎ only ever names the Enter key in keyboard hints. */
const ALLOWED = new Set(['✕', '✓', '⏎']);

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    const ok = e.name.endsWith('.html') || (e.name.endsWith('.ts') && !e.name.endsWith('.spec.ts'));
    return ok ? [p] : [];
  });
}

/** Disallowed emoji in `text` outside HTML comments and `{{ … }}` interpolations (both may span lines). */
export function hasStaticEmoji(text: string): boolean {
  return offendingLines(text).length > 0;
}

/** A component file's inline `template:` literals, everything else blanked (newlines kept); an .html file is all template.
 *  Escapes (including a backslash line continuation) stay inside the literal; a nested backtick literal inside `${…}` is not handled (none in src/app). */
export function templateOnly(file: string, text: string): string {
  if (!file.endsWith('.ts')) return text;
  const blank = (s: string) => s.replace(/[^\n]/g, ' ');
  let out = '';
  let last = 0;
  for (const m of text.matchAll(/\btemplate:\s*`((?:[^`\\]|\\[\s\S])*)`/g)) {
    const start = m.index! + m[0].indexOf('`') + 1;
    out += blank(text.slice(last, start)) + m[1];
    last = start + m[1].length;
  }
  return out + blank(text.slice(last));
}

/** 1-based line numbers of `text` that place a disallowed emoji outside a comment or an interpolation. */
export function offendingLines(text: string): number[] {
  // Blank the comment / interpolation bodies but keep their newlines so line numbers survive.
  const bare = text.replace(/<!--[\s\S]*?-->|\{\{[\s\S]*?\}\}/g, (m) => m.replace(/[^\n]/g, ' '));
  return bare
    .split('\n')
    .flatMap((line, i) => ((line.match(EMOJI) ?? []).some((ch) => !ALLOWED.has(ch)) ? [i + 1] : []));
}

describe('line icons for interface chrome (polish-layout-v2 R5)', () => {
  it('flags static emoji and passes allowed glyphs, interpolations and comments', () => {
    for (const bad of ['<span>🗑</span>', '<button>✎</button>', '<button>☆</button>', '<b>📅 日期</b>', '<i>🤝</i>', '<b>⏰</b>', '<b>⭐</b>']) {
      expect(hasStaticEmoji(bad), bad).toBe(true);
    }
    for (const ok of ['<b>✕</b>', '<b>✓</b>', '<b>‹</b>', '<b>›</b>', '<b>⇄</b>', '<b>—</b>', '<b title="⇧⏎">✓</b>', "<i>{{ account.icon ?? '🏦' }}</i>", '<!-- 🗑 -->']) {
      expect(hasStaticEmoji(ok), ok).toBe(false);
    }
  });

  it('ignores comments and interpolations that span lines and still numbers the offending line', () => {
    expect(offendingLines('<!--\n 🗑 \n-->\n<b>{{\n  icon ?? "🏦"\n}}</b>')).toEqual([]);
    expect(offendingLines('<p>✕</p>\n<!-- 📅 -->\n<span>🗑</span>')).toEqual([3]);
  });

  it('scans only the inline template of a component file', () => {
    const ts = "const ICONS = { card: '💳' };\n@Component({\n  template: `\n    <span>{{ icon ?? '🏦' }}</span>\n    <i>🔒</i>\n  `,\n})\nexport class X { fallback = '🔁'; }\n";
    expect(offendingLines(templateOnly('x.ts', ts))).toEqual([5]);
    expect(templateOnly('x.html', '<i>🔒</i>')).toBe('<i>🔒</i>');
    expect(offendingLines(templateOnly('y.ts', 'template: `a \\\n b\n<i>🔒</i>`'))).toEqual([3]);
  });

  it('has no static emoji in any template (.html or inline)', { timeout: 30_000 }, () => {
    const offenders = walk(join(process.cwd(), 'src/app')).flatMap((file) =>
      offendingLines(templateOnly(file, readFileSync(file, 'utf8'))).map((n) => `${relative(process.cwd(), file)}:${n}`),
    );
    expect(offenders).toEqual([]);
  });
});
