import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

export type BtnVariant = 'primary' | 'secondary' | 'ghost';

@Component({
  selector: 'app-btn',
  template: `
    <button
      class="btn"
      type="button"
      [class.btn-primary]="variant() === 'primary'"
      [class.btn-secondary]="variant() === 'secondary'"
      [class.btn-ghost]="variant() === 'ghost'"
      [class.btn-icon]="iconOnly()"
      [disabled]="disabled() || loading()"
      [attr.aria-busy]="loading()"
      [attr.aria-label]="ariaLabel() || null"
      (click)="onClick($event)"
    >
      @if (loading()) {
        <i class="pi pi-spinner pi-spin" aria-hidden="true"></i>
      } @else if (icon()) {
        <i class="pi {{ normalizedIcon() }}" aria-hidden="true"></i>
      }
      <span class="btn-content"><ng-content></ng-content></span>
    </button>
  `,
  styleUrl: './btn.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BtnComponent {
  readonly variant = input<BtnVariant>('primary');
  readonly disabled = input(false);
  readonly loading = input(false);
  readonly icon = input('');
  readonly iconOnly = input(false);
  readonly ariaLabel = input('');
  readonly click = output<MouseEvent>();

  protected readonly normalizedIcon = computed(() => this.icon().replace(/^pi\s+/, ''));

  protected onClick(event: MouseEvent): void {
    event.stopPropagation();

    if (this.disabled() || this.loading()) {
      event.preventDefault();
      return;
    }

    this.click.emit(event);
  }
}
