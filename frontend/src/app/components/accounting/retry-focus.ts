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
      if (active && active !== document.body && host.contains(active)) {
        return;
      }
      if (active && active !== document.body) {
        return;
      }
      host.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
    },
    { injector },
  );
}
