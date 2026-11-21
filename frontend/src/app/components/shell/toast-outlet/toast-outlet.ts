import { DOCUMENT } from '@angular/common';
import { ChangeDetectionStrategy, Component, ElementRef, inject } from '@angular/core';

import { Toast, ToastService } from '../../../services/toast.service';

/**
 * The one toast of the app (shell-level). Two live regions stay in the DOM so a screen reader hears a toast that is
 * inserted later: 完成 / 進行中 are polite (`status`), 失敗 is assertive (`alert`).
 */
@Component({
  selector: 'app-toast-outlet',
  template: `
    <div class="toast-region" role="status" aria-live="polite" aria-atomic="true">
      @let toast = toasts.current();
      @if (toast && toast.kind !== 'error') {
        <div class="toast" [class.toast--done]="toast.kind === 'done'" [class.toast--progress]="toast.kind === 'progress'" [attr.data-toast-id]="toast.id">
          <i class="toast-icon" [class]="icons[toast.kind]" aria-hidden="true"></i>
          <span class="toast-text">{{ toast.text }}</span>
          @if (toast.action; as action) {
            <button type="button" class="toast-action" (click)="act(toast.id)">{{ action.label }}</button>
          }
        </div>
      }
    </div>
    <div class="toast-region" role="alert" aria-atomic="true">
      @if (toast && toast.kind === 'error') {
        <div class="toast toast--error" [attr.data-toast-id]="toast.id">
          <i class="toast-icon" [class]="icons.error" aria-hidden="true"></i>
          <span class="toast-text">{{ toast.text }}</span>
          @if (toast.action; as action) {
            <button type="button" class="toast-action" (click)="act(toast.id)">{{ action.label }}</button>
          }
          <button type="button" class="toast-close" aria-label="關閉" (click)="close(toast.id)">
            <i class="pi pi-times" aria-hidden="true"></i>
          </button>
        </div>
      }
    </div>
  `,
  styleUrl: './toast-outlet.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    'data-toast-outlet': '',
    '(pointerenter)': 'toasts.pause("hover")',
    '(pointerleave)': 'toasts.resume("hover")',
    '(focusin)': 'toasts.pause("focus")',
    '(focusout)': 'onFocusOut($event)',
    '(document:focusin)': 'rememberOutside($event)',
    '(keydown.escape)': 'onEscape($event)',
  },
})
export class ToastOutletComponent {
  protected readonly toasts = inject(ToastService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly document = inject(DOCUMENT);
  private lastOutside: HTMLElement | null = null;

  protected readonly icons: Record<Toast['kind'], string> = {
    done: 'pi pi-check',
    progress: 'pi pi-spinner pi-spin',
    error: 'pi pi-exclamation-circle',
  };

  protected onFocusOut(event: FocusEvent): void {
    const next = event.relatedTarget;
    if (!(next instanceof Node && this.host.contains(next))) {
      this.toasts.resume('focus');
    }
  }

  protected rememberOutside(event: FocusEvent): void {
    const target = event.target;
    if (target instanceof HTMLElement && !this.host.contains(target)) {
      this.lastOutside = target;
    }
  }

  protected close(id: number): void {
    this.returnFocus();
    this.toasts.dismiss(id);
  }

  protected act(id: number): void {
    this.returnFocus();
    this.toasts.runAction(id);
  }

  protected onEscape(event: Event): void {
    const toast = this.toasts.current();
    if (!toast) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    this.close(toast.id);
  }

  /** The toast closes with focus inside it: focus goes back to where it was before, if that still exists. */
  private returnFocus(): void {
    const active = this.document.activeElement;
    if (active && this.host.contains(active) && this.lastOutside?.isConnected) {
      this.lastOutside.focus();
    }
  }
}
