import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';

import { BannerDismissals } from './banner-dismissals';

export type BannerTone = 'error' | 'warn' | 'info';

const ICONS: Record<BannerTone, string> = {
  error: 'pi-exclamation-circle',
  warn: 'pi-exclamation-triangle',
  info: 'pi-info-circle',
};

@Component({
  selector: 'app-banner',
  template: `
    @if (!hidden()) {
      <div class="banner banner--{{ tone() }}" [class.banner--closable]="closable()" [attr.role]="tone() === 'error' ? 'alert' : 'status'">
        <i class="banner-icon pi {{ icon() }}" aria-hidden="true"></i>
        <div class="banner-body">
          <b class="banner-title">{{ heading() }}</b>
          @if (detail()) {
            <span class="banner-detail">{{ detail() }}</span>
          }
        </div>
        @if (actionLabel()) {
          <button type="button" class="banner-action" [disabled]="actionDisabled()" (click)="action.emit()">
            {{ actionLabel() }}
          </button>
        }
        @if (closable()) {
          <button type="button" class="banner-close" aria-label="關閉" (click)="close()">
            <i class="pi pi-times" aria-hidden="true"></i>
          </button>
        }
      </div>
    }
  `,
  styleUrl: './banner.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BannerComponent {
  private readonly dismissals = inject(BannerDismissals);

  readonly tone = input.required<BannerTone>();
  readonly heading = input.required<string>();
  readonly detail = input<string | null>(null);
  readonly actionLabel = input<string | null>(null);
  readonly actionDisabled = input(false);
  /** Only for tone 'info': makes it dismissible and is the remembered key. Ignored for error / warn. */
  readonly dismissId = input<string | null>(null);
  readonly action = output<void>();

  protected readonly icon = computed(() => ICONS[this.tone()]);
  protected readonly closable = computed(() => this.tone() === 'info' && !!this.dismissId());
  protected readonly hidden = computed(() => {
    const id = this.dismissId();
    return this.tone() === 'info' && !!id && this.dismissals.isDismissed(id);
  });

  protected close(): void {
    const id = this.dismissId();
    if (id) this.dismissals.dismiss(id);
  }
}
