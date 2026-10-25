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
    return e.name.endsWith('.html') ? [p] : [];
  });
}

/** Disallowed emoji in `text` outside HTML comments and `{{ … }}` interpolations (both may span lines). */
export function hasStaticEmoji(text: string): boolean {
  return offendingLines(text).length > 0;
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

  it('has no static emoji in any template', { timeout: 30_000 }, () => {
    const offenders = walk(join(process.cwd(), 'src/app')).flatMap((file) =>
      offendingLines(readFileSync(file, 'utf8')).map((n) => `${relative(process.cwd(), file)}:${n}`),
    );
    expect(offenders).toEqual([]);
  });
});
