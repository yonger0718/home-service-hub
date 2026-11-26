import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ButtonModule } from 'primeng/button';

import { BusyButtonDirective } from './busy-button.directive';

@Component({
  imports: [ButtonModule, BusyButtonDirective],
  template: `
    <p-button label="儲存" icon="pi pi-check" type="submit" [appBusy]="busy" [disabled]="busy" (onClick)="clicks = clicks + 1"></p-button>
    <p-button label="建立" type="submit" [appBusy]="busy" [disabled]="busy"></p-button>
  `,
})
class HostComponent {
  busy = false;
  clicks = 0;
}

describe('BusyButtonDirective (p-button busy, #441)', () => {
  function render() {
    const fixture = TestBed.createComponent(HostComponent);
    fixture.detectChanges();
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('p-button button')) as HTMLButtonElement[];
    const setBusy = async (busy: boolean) => {
      fixture.componentInstance.busy = busy;
      fixture.changeDetectorRef.markForCheck();
      fixture.detectChanges();
      await fixture.whenStable();
    };
    return { fixture, buttons, setBusy };
  }

  it('idle: aria-busy="false", no spinner, no name override', async () => {
    const { buttons, setBusy } = render();
    await setBusy(false);
    for (const b of buttons) {
      expect(b.getAttribute('aria-busy')).toBe('false');
      expect(b.hasAttribute('aria-label')).toBe(false);
      expect(b.querySelector('.hh-busy-spinner')).toBeNull();
      expect(b.closest('p-button')!.classList).not.toContain('hh-busy');
    }
  });

  it('busy: aria-busy="true", 「<label>，處理中」, disabled, one centred spinner; icon and label stay (hidden); idle again restores', async () => {
    const { fixture, buttons: [save, create], setBusy } = render();
    await setBusy(true);
    for (const b of [save, create]) {
      expect(b.getAttribute('aria-busy')).toBe('true');
      expect(b.disabled).toBe(true);
      expect(b.querySelectorAll('.hh-busy-spinner.pi.pi-spinner.pi-spin').length).toBe(1);
      expect(b.querySelector('.hh-busy-spinner')!.getAttribute('aria-hidden')).toBe('true');
      expect(b.closest('p-button')!.classList).toContain('hh-busy');
    }
    expect(save.getAttribute('aria-label')).toBe('儲存，處理中');
    expect(create.getAttribute('aria-label')).toBe('建立，處理中');
    expect(save.querySelector('.pi-check')).toBeTruthy();
    expect(save.querySelector('.p-button-label')!.textContent!.trim()).toBe('儲存');
    save.click();
    expect(fixture.componentInstance.clicks).toBe(0);

    await setBusy(false);
    expect(save.getAttribute('aria-busy')).toBe('false');
    expect(save.hasAttribute('aria-label')).toBe(false);
    expect(save.querySelector('.hh-busy-spinner')).toBeNull();
    save.click();
    expect(fixture.componentInstance.clicks).toBe(1);
  });

  it('pins the global overlay (source): busy hides every child but the spinner (visibility, so the width stays); spinner centred', () => {
    const css = readFileSync(join(process.cwd(), 'src/styles.scss'), 'utf8');
    const block = /p-button\.hh-busy \.p-button\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';
    expect(block).toMatch(/position:\s*relative;/);
    expect(block).toMatch(/> :not\(\.hh-busy-spinner\)\s*\{\s*visibility:\s*hidden;\s*\}/);
    expect(block).toMatch(/\.hh-busy-spinner\s*\{[^}]*left:\s*50%;[^}]*margin:\s*-0\.5em 0 0 -0\.5em;[^}]*position:\s*absolute;[^}]*top:\s*50%;[^}]*width:\s*1em;/);
  });
});
