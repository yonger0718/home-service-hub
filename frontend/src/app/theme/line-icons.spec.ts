/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Emoji / pictograph ranges; ✕ ✓ stay allowed as text glyphs (polish-layout R5). */
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/gu;
const ALLOWED = new Set(['✕', '✓']);

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return e.name.endsWith('.html') ? [p] : [];
  });
}

/** True when `line` places a disallowed emoji outside an HTML comment or a `{{ … }}` interpolation. */
export function hasStaticEmoji(line: string): boolean {
  const bare = line.replace(/<!--.*?-->/g, '').replace(/\{\{.*?\}\}/g, '');
  return (bare.match(EMOJI) ?? []).some((ch) => !ALLOWED.has(ch));
}

describe('line icons for interface chrome (polish-layout-v2 R5)', () => {
  it('flags static emoji and passes allowed glyphs, interpolations and comments', () => {
    for (const bad of ['<span>🗑</span>', '<button>✎</button>', '<button>☆</button>', '<b>📅 日期</b>', '<i>🤝</i>']) {
      expect(hasStaticEmoji(bad), bad).toBe(true);
    }
    for (const ok of ['<b>✕</b>', '<b>✓</b>', '<b>‹</b>', '<b>›</b>', '<b>⇄</b>', '<b>—</b>', "<i>{{ account.icon ?? '🏦' }}</i>", '<!-- 🗑 -->']) {
      expect(hasStaticEmoji(ok), ok).toBe(false);
    }
  });

  it('has no static emoji in any template', { timeout: 30_000 }, () => {
    const offenders = walk(join(process.cwd(), 'src/app')).flatMap((file) =>
      readFileSync(file, 'utf8')
        .split('\n')
        .flatMap((line, i) => (hasStaticEmoji(line) ? [`${relative(process.cwd(), file)}:${i + 1}`] : [])),
    );
    expect(offenders).toEqual([]);
  });
});
