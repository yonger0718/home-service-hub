import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  effect,
  inject,
} from '@angular/core';

import { Toast, ToastService } from '../../../services/toast.service';
import { isHandledKey } from '../../accounting/accounting-ui';
import { outermostModal, toastCycleTarget } from './toast-focus';

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
    '(pointerenter)': 'onPointerEnter()',
    '(pointerleave)': 'onPointerLeave()',
    '(document:pointermove)': 'onPointerMove($event)',
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
  private readonly injector = inject(Injector);
  private lastOutside: HTMLElement | null = null;
  /** Tracked from pointerenter / pointerleave (`:hover` is not queryable everywhere); survives a toast that is replaced. */
  private pointerInside = false;
  private shownId: number | null = null;

  constructor() {
    // A replaced toast takes its focused / hovered button with it without a reliable focusout / pointerleave, and the
    // service drops its pause reasons on every raise: after render, say what is true for the toast now shown.
    effect(() => {
      const toast = this.toasts.current();
      if (!toast || toast.id === this.shownId) {
        this.shownId = toast?.id ?? null;
        return;
      }
      this.shownId = toast.id;
      afterNextRender(() => this.reassertPause(toast.id), { injector: this.injector });
    });

    // Capture phase: runs before the modal's own trap (trapFocus / PrimeNG pFocusTrap) and marks the event handled.
    const handler = (event: KeyboardEvent) => this.onTab(event);
    this.document.addEventListener('keydown', handler, true);
    inject(DestroyRef).onDestroy(() => this.document.removeEventListener('keydown', handler, true));
  }

  protected readonly icons: Record<Toast['kind'], string> = {
    done: 'pi pi-check',
    progress: 'pi pi-spinner pi-spin',
    error: 'pi pi-exclamation-circle',
  };

  protected onPointerEnter(): void {
    this.pointerInside = true;
    this.toasts.pause('hover');
  }

  protected onPointerLeave(): void {
    this.pointerInside = false;
    this.toasts.resume('hover');
  }

  /** With no toast under it the pointer sends no pointerleave: a move elsewhere is the only sign it left. */
  protected onPointerMove(event: PointerEvent): void {
    if (this.pointerInside && event.target instanceof Node && !this.host.contains(event.target)) {
      this.pointerInside = false;
    }
  }

  private reassertPause(id: number): void {
    if (this.toasts.current()?.id !== id) {
      return;
    }
    if (this.pointerInside) {
      this.toasts.pause('hover');
    }
    const active = this.document.activeElement;
    if (active && this.host.contains(active)) {
      this.toasts.pause('focus');
    }
  }

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

  /** Tab / Shift+Tab at a group edge moves between the open modal's controls and the toast's buttons as one cycle. */
  private onTab(event: KeyboardEvent): void {
    if (event.key !== 'Tab' || isHandledKey(event)) {
      return;
    }
    const toast = this.host.querySelector<HTMLElement>('.toast');
    const active = this.document.activeElement;
    if (!toast?.querySelector('button') || !(active instanceof HTMLElement)) {
      return;
    }
    const inToast = toast.contains(active);
    const modal = inToast
      ? (this.lastOutside?.isConnected ? outermostModal(this.lastOutside) : null)
      : outermostModal(active);
    const target = toastCycleTarget({ active, modal, toast, backwards: event.shiftKey });
    if (target) {
      event.preventDefault();
      if (!inToast) {
        this.lastOutside = active;
      }
      target.focus();
    }
  }

  /** The toast closes with focus inside it: focus goes back to where it was before, if that still exists. */
  private returnFocus(): void {
    const active = this.document.activeElement;
    if (active && this.host.contains(active) && this.lastOutside?.isConnected) {
      this.lastOutside.focus();
    }
  }
}
