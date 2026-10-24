import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Sidebar wordmark lockup, inlined so every fill follows the theme tokens (the old <img> hard-coded a dark wordmark). */
@Component({
  selector: 'app-logo-lockup',
  template: `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 80" role="img" aria-label="Home Hub">
      <g transform="translate(8,8) scale(0.64)">
        <rect width="100" height="100" rx="22" fill="var(--app-text)"></rect>
        <rect x="28" y="35" width="8" height="35" rx="1" fill="var(--app-text-muted)"></rect>
        <rect x="64" y="30" width="8" height="40" rx="1" fill="var(--app-surface)"></rect>
        <path d="M28 52 H72" stroke="var(--app-surface)" stroke-width="8" stroke-linecap="square"></path>
        <circle cx="50" cy="30" r="5" fill="var(--app-surface)"></circle>
      </g>
      <text
        x="84"
        y="42"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
        font-size="26"
        font-weight="800"
        letter-spacing="-0.02em"
        fill="currentColor"
      >Home Hub</text>
      <text
        x="85"
        y="62"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
        font-size="11.5"
        font-weight="700"
        letter-spacing="0.18em"
        fill="var(--app-text-muted)"
      >家庭服務中樞</text>
    </svg>
  `,
  styles: `
    :host {
      color: var(--app-text);
      display: block;
      height: 36px;
    }

    svg {
      display: block;
      height: 100%;
      width: auto;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LogoLockupComponent {}
