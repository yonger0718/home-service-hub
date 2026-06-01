import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

@Component({
  selector: 'app-pct-badge',
  template: `<span class="pct-badge" [class.down]="hasValue() && isDown()" [class.neutral]="!hasValue() || safeValue() === 0">{{ formatted() }}</span>`,
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
  protected readonly isDown = computed(() => this.safeValue() < 0);
  protected readonly formatted = computed(() => {
    if (!this.hasValue()) return '—';
    const value = this.safeValue();
    const prefix = value > 0 ? '+' : '';
    return `${prefix}${value.toFixed(2)}%`;
  });
}
