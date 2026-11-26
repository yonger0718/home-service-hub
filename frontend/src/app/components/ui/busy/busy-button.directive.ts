import { Directive, ElementRef, Renderer2, afterRenderEffect, inject, input } from '@angular/core';

/**
 * Busy state for a PrimeNG `p-button` (#441, owner ruling 2026-11-26), the same as `app-btn`'s: use `[appBusy]="busy"`
 * with `[disabled]="busy || …"` instead of `[loading]`. While busy its icon and label stay but are hidden (the width
 * does not change; styles.scss `p-button.hh-busy`), a spinner sits in the middle, `aria-busy="true"` and the button is
 * named 「<label>，處理中」. Idle: `aria-busy="false"` and the label names it again.
 */
@Directive({
  selector: 'p-button[appBusy]',
  host: { '[class.hh-busy]': 'appBusy()' },
})
export class BusyButtonDirective {
  readonly appBusy = input(false);
  /** The p-button's own `label` input, read for the busy name. */
  readonly label = input<string | undefined>(undefined);

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly renderer = inject(Renderer2);

  constructor() {
    afterRenderEffect(() => {
      const busy = this.appBusy();
      const label = this.label();
      const button = this.host.nativeElement.querySelector('button');
      if (!button) {
        return;
      }
      button.setAttribute('aria-busy', String(busy));
      const spinner = button.querySelector(':scope > .hh-busy-spinner');
      if (busy) {
        button.setAttribute('aria-label', `${label ?? (button.textContent ?? '').trim()}，處理中`);
        if (!spinner) {
          const icon = this.renderer.createElement('i') as HTMLElement;
          icon.className = 'pi pi-spinner pi-spin hh-busy-spinner';
          icon.setAttribute('aria-hidden', 'true');
          this.renderer.appendChild(button, icon);
        }
      } else {
        button.removeAttribute('aria-label');
        spinner?.remove();
      }
    });
  }
}
