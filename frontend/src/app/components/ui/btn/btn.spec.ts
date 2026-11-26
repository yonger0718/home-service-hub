import { Component } from '@angular/core';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { TestBed } from '@angular/core/testing';

import { BtnComponent } from './btn';

@Component({
  imports: [BtnComponent],
  template: `<app-btn variant="primary" icon="pi-plus" [disabled]="disabled" ariaLabel="Add" (click)="count = count + 1">新增</app-btn>`,
})
class BtnHostComponent {
  disabled = false;
  count = 0;
}

describe('BtnComponent', () => {
  it('renders variant, icon, content, and ARIA label', () => {
    const fixture = TestBed.createComponent(BtnHostComponent);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.classList.contains('btn-primary')).toBe(true);
    expect(button.querySelector('.pi-plus')).toBeTruthy();
    expect(button.getAttribute('aria-label')).toBe('Add');
    expect(button.textContent).toContain('新增');
  });

  it('emits click only when enabled', () => {
    const fixture = TestBed.createComponent(BtnHostComponent);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    button.click();
    expect(fixture.componentInstance.count).toBe(1);

    fixture.componentInstance.disabled = true;
    fixture.detectChanges();
    button.click();
    expect(fixture.componentInstance.count).toBe(1);
  });

  it('sets aria-expanded on the button only when given', () => {
    const fixture = TestBed.createComponent(BtnExpandedHostComponent);
    fixture.detectChanges();
    const [plain, toggle] = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    expect(plain.hasAttribute('aria-expanded')).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fixture.componentInstance.expanded = true;
    fixture.changeDetectorRef.markForCheck();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  describe('busy (#441, owner ruling 2026-11-26)', () => {
    function render() {
      const fixture = TestBed.createComponent(BtnBusyHostComponent);
      fixture.detectChanges();
      const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
      const setBusy = (busy: boolean) => {
        fixture.componentInstance.busy = busy;
        fixture.changeDetectorRef.markForCheck();
        fixture.detectChanges();
      };
      return { fixture, buttons, setBusy };
    }

    it('idle: a button with a spinner state says aria-busy="false", keeps its name and has no spinner; one without has no aria-busy', () => {
      const { buttons: [text, named, iconed, iconOnly, plain] } = render();
      for (const b of [text, named, iconed, iconOnly]) {
        expect(b.getAttribute('aria-busy')).toBe('false');
        expect(b.classList).toContain('btn-steady');
        expect(b.querySelector('.pi-spinner')).toBeNull();
        expect(b.disabled).toBe(false);
      }
      expect(text.hasAttribute('aria-label')).toBe(false);
      expect(named.getAttribute('aria-label')).toBe('已處理：國泰對帳單 10/22');
      expect(plain.hasAttribute('aria-busy')).toBe(false);
      expect(plain.classList).not.toContain('btn-steady');
    });

    it('busy: aria-busy="true", named 「<文字>，處理中」 (or 「<ariaLabel>，處理中」), disabled, a spinner; the icon and text stay in the button', () => {
      const { fixture, buttons: [text, named, iconed, iconOnly, plain], setBusy } = render();
      setBusy(true);
      for (const b of [text, named, iconed, iconOnly]) {
        expect(b.getAttribute('aria-busy')).toBe('true');
        expect(b.disabled).toBe(true);
        expect(b.querySelectorAll('.pi-spinner.pi-spin.btn-spinner').length).toBe(1);
      }
      expect(text.getAttribute('aria-label')).toBe('全部入帳，處理中');
      expect(named.getAttribute('aria-label')).toBe('已處理：國泰對帳單 10/22，處理中');
      expect(iconed.getAttribute('aria-label')).toBe('確認匯入，處理中');
      expect(iconOnly.getAttribute('aria-label')).toBe('刷新，處理中');
      // Hidden, not removed: the text and the icon keep their place (and the width).
      expect(text.querySelector('.btn-content')!.textContent!.trim()).toBe('全部入帳');
      expect(iconed.querySelector('.pi-check')).toBeTruthy();
      expect(iconed.querySelector('.btn-content')!.textContent!.trim()).toBe('確認匯入');
      expect(plain.hasAttribute('aria-busy')).toBe(false);

      text.click();
      expect(fixture.componentInstance.clicks).toBe(0);

      setBusy(false);
      expect(text.getAttribute('aria-busy')).toBe('false');
      expect(text.hasAttribute('aria-label')).toBe(false);
      expect(named.getAttribute('aria-label')).toBe('已處理：國泰對帳單 10/22');
      expect(text.querySelector('.pi-spinner')).toBeNull();
      text.click();
      expect(fixture.componentInstance.clicks).toBe(1);
    });

    it('pins the overlay (source): busy hides every child but the spinner (visibility, so the width stays); the spinner is centred', () => {
      const css = readFileSync(join(process.cwd(), 'src/app/components/ui/btn/btn.scss'), 'utf8');
      const block = /\.btn-steady\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';
      expect(block).toMatch(/position:\s*relative;/);
      expect(block).toMatch(/&\[aria-busy='true'\] > :not\(\.btn-spinner\)\s*\{\s*visibility:\s*hidden;\s*\}/);
      expect(block).toMatch(/\.btn-spinner\s*\{[^}]*left:\s*50%;[^}]*margin:\s*-0\.45rem 0 0 -0\.45rem;[^}]*position:\s*absolute;[^}]*top:\s*50%;[^}]*width:\s*0\.9rem;/);
      expect(block).not.toMatch(/padding|display:\s*none|color:\s*transparent/);
    });
  });

  it('renders the soft variant as .btn-soft', () => {
    const fixture = TestBed.createComponent(BtnSoftHostComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('button.btn-soft')).toBeTruthy();
  });
});

@Component({ imports: [BtnComponent], template: `<app-btn variant="soft">新增</app-btn>` })
class BtnSoftHostComponent {}

@Component({ imports: [BtnComponent], template: `<app-btn>新增</app-btn><app-btn [ariaExpanded]="expanded">展開</app-btn>` })
class BtnExpandedHostComponent {
  expanded = false;
}

@Component({
  imports: [BtnComponent],
  template: `
    <app-btn variant="soft" [loading]="busy" (click)="clicks = clicks + 1">全部入帳</app-btn>
    <app-btn variant="soft" [loading]="busy" ariaLabel="已處理：國泰對帳單 10/22">已處理</app-btn>
    <app-btn icon="pi-check" [loading]="busy">確認匯入</app-btn>
    <app-btn icon="pi-refresh" [iconOnly]="true" [loading]="busy" ariaLabel="刷新"></app-btn>
    <app-btn variant="ghost">取消</app-btn>
  `,
})
class BtnBusyHostComponent {
  busy = false;
  clicks = 0;
}
