import { ChangeDetectionStrategy, Component, ElementRef, computed, input, output, viewChild } from '@angular/core';

export type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'soft';

@Component({
  selector: 'app-btn',
  template: `
    <button
      class="btn"
      type="button"
      [class.btn-primary]="variant() === 'primary'"
      [class.btn-secondary]="variant() === 'secondary'"
      [class.btn-ghost]="variant() === 'ghost'"
      [class.btn-soft]="variant() === 'soft'"
      [class.btn-icon]="iconOnly()"
      [class.btn-steady]="spins()"
      [disabled]="disabled() || loading()"
      [attr.aria-busy]="spins() ? !!loading() : null"
      [attr.aria-label]="accessibleName()"
      [attr.aria-expanded]="ariaExpanded()"
      (click)="onClick($event)"
    >
      @if (icon()) {
        <i class="pi {{ normalizedIcon() }}" aria-hidden="true"></i>
      }
      <span class="btn-content" #content><ng-content></ng-content></span>
      @if (loading()) {
        <i class="pi pi-spinner pi-spin btn-spinner" aria-hidden="true"></i>
      }
    </button>
  `,
  styleUrl: './btn.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BtnComponent {
  readonly variant = input<BtnVariant>('primary');
  readonly disabled = input(false);
  /**
   * Busy (#441, owner ruling 2026-11-26): the icon and text stay but are hidden (the width does not change), a spinner
   * sits in the middle, the button is disabled, `aria-busy="true"` and its name is 「<name>，處理中」. Left unbound
   * (undefined) on a button that never spins (no aria-busy then).
   */
  readonly loading = input<boolean | undefined>(undefined);
  readonly icon = input('');
  readonly iconOnly = input(false);
  readonly ariaLabel = input('');
  /** For a button that opens or closes a region; null (the default) leaves the attribute off. */
  readonly ariaExpanded = input<boolean | null>(null);
  readonly click = output<MouseEvent>();

  protected readonly normalizedIcon = computed(() => this.icon().replace(/^pi\s+/, ''));

  /** The button has a spinner state ([loading] bound). */
  protected readonly spins = computed(() => this.loading() !== undefined);

  private readonly content = viewChild.required<ElementRef<HTMLElement>>('content');

  /** `ariaLabel`, else none (the text names it); busy: 「<ariaLabel or text>，處理中」. */
  protected accessibleName(): string | null {
    if (!this.loading()) {
      return this.ariaLabel() || null;
    }
    const name = this.ariaLabel() || (this.content().nativeElement.textContent ?? '').trim();
    return `${name}，處理中`;
  }

  protected onClick(event: MouseEvent): void {
    // stop native bubble so ancestor (click) handlers don't fire twice: once from
    // this.click.emit and again from the DOM-bubble of the inner <button>
    event.stopPropagation();

    if (this.disabled() || this.loading()) {
      event.preventDefault();
      return;
    }

    this.click.emit(event);
  }
}
