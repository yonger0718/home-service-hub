import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { displayedSign, formatPct } from '../format';

@Component({
  selector: 'app-pct-badge',
  template: `<span class="pct-badge" [class.down]="isDown()" [class.neutral]="isNeutral()">{{ formatted() }}</span>`,
  styleUrl: './pct-badge.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PctBadgeComponent {
  readonly value = input<number | null | undefined>(0);

  protected readonly hasValue = computed(() => {
    const raw = this.value();
    return raw != null && Number.isFinite(Number(raw));
  });
  protected readonly safeValue = computed(() => {
    const raw = this.value();
    const num = Number(raw);
    return Number.isFinite(num) ? num : 0;
  });
  private readonly sign = computed(() => (this.hasValue() ? displayedSign(this.safeValue(), this.formatted()) : 0));
  protected readonly isDown = computed(() => this.sign() < 0);
  protected readonly isNeutral = computed(() => this.sign() === 0);
  protected readonly formatted = computed(() => {
    if (!this.hasValue()) return '—';
    return formatPct(this.safeValue(), { sign: 'change' });
  });
}
