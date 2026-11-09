import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { formatNumber } from '../../accounting/format';
import { RewardUnit, rewardFigure } from '../../accounting/reward-rule-text';

export interface CapBarWithCap {
  near: boolean;
  /** Width of the posted fill, 0–100 (one decimal). */
  usedPct: number;
  /** Width of the estimated fill; `usedPct + estPct` never exceeds 100. */
  estPct: number;
  text: string;
  aria: string;
}

export interface CapBarNoCap {
  near: false;
  text: string;
}

export type CapBarView = CapBarWithCap | CapBarNoCap;

const NEAR_RATIO = 0.9;

function pct(part: number, cap: number, limit: number): number {
  return Math.round(Math.min(limit, (part / cap) * 100) * 10) / 10;
}

/**
 * polish-reward-and-account-settings D2: the posted and estimated figures of one usage row against its cap, in the
 * reward's unit (D1a). `near` from 90 % of (used + estimated) / cap; without a cap only the one-line text remains.
 */
export function capBarView(used: string | number, estimated: string | number, cap: string | number | null,
                           unit: RewardUnit): CapBarView {
  const usedN = Number(used);
  const estN = Number(estimated);
  const fig = (value: number): string => rewardFigure(value, unit);
  if (cap === null || Number(cap) <= 0) {
    return { near: false, text: `無上限 · 本期 ${fig(usedN)} 已入帳 + ${fig(estN)} 預估` };
  }
  const capN = Number(cap);
  const total = usedN + estN;
  const near = total / capN >= NEAR_RATIO;
  const usedPct = pct(usedN, capN, 100);
  const estPct = pct(estN, capN, 100 - usedPct);
  const number = (value: number): string => formatNumber(value, 'TWD');
  const head = unit.money ? `${fig(total)} / ${fig(capN)}` : `${number(total)} / ${number(capN)} ${unit.name}`;
  const tail = near ? '快到上限' : `剩 ${unit.money ? fig(Math.max(0, capN - total)) : number(Math.max(0, capN - total))}`;
  return {
    near, usedPct, estPct,
    text: `${head} · ${tail}`,
    aria: `本期已入帳 ${fig(usedN)}，預估 ${fig(estN)}，上限 ${fig(capN)}`,
  };
}

@Component({
  selector: 'app-cap-bar',
  template: `
    <div class="cap-bar" [class.near]="view().near">
      @if (withCap(); as c) {
        <span class="cap-track" role="img" [attr.aria-label]="c.aria">
          <span class="cap-used" [style.width.%]="c.usedPct"></span>
          <span class="cap-est" [style.width.%]="c.estPct"></span>
        </span>
      }
      <span class="cap-text">{{ view().text }}</span>
    </div>
  `,
  styleUrl: './cap-bar.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CapBarComponent {
  readonly used = input.required<string | number>();
  readonly estimated = input.required<string | number>();
  readonly cap = input.required<string | number | null>();
  readonly unit = input.required<RewardUnit>();

  readonly view = computed(() => capBarView(this.used(), this.estimated(), this.cap(), this.unit()));
  /** The capped view, or null when the rule has no cap (then only the text line renders). */
  readonly withCap = computed<CapBarWithCap | null>(() => {
    const view = this.view();
    return 'aria' in view ? view : null;
  });
}
