import { Injector, afterNextRender } from '@angular/core';

/**
 * After a 重試 succeeded the read-failure banner is gone, and the 重試 button that held focus went with it (focus
 * falls to `<body>`, outside a sheet's or drawer's focus trap). When that happened, focus goes to `selector` inside
 * the page (its heading or main section, `tabindex="-1"`); focus the owner moved elsewhere meanwhile is left alone.
 */
export function focusPageIfLost(host: HTMLElement, injector: Injector, selector: string): void {
  afterNextRender(
    () => {
      const active = document.activeElement;
      if (active && active !== document.body) {
        return;
      }
      host.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
    },
    { injector },
  );
}

/**
 * After a 重試 failed again the banner is the same node, and the only change is its button enabling. The button was
 * disabled while the read ran, and a browser may have dropped focus to `<body>` meanwhile: focus goes back to
 * `selector` (the 重試 button) then; focus the owner moved elsewhere is left alone.
 */
export function focusRetryIfLost(host: HTMLElement, injector: Injector, selector: string): void {
  afterNextRender(
    () => {
      const active = document.activeElement;
      if (active && active !== document.body) {
        return;
      }
      host.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
    },
    { injector },
  );
}
