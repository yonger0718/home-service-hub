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

  it('a button with a spinner state and no icon keeps one width: .btn-steady, busy or not (#441)', () => {
    const fixture = TestBed.createComponent(BtnSteadyHostComponent);
    fixture.detectChanges();
    const [steady, iconed, iconOnly, plain] = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    // Only a button whose [loading] is bound and which has no icon of its own reserves the spinner's slot.
    expect(steady.classList).toContain('btn-steady');
    expect(iconed.classList).not.toContain('btn-steady');
    expect(iconOnly.classList).not.toContain('btn-steady');
    expect(plain.classList).not.toContain('btn-steady');
    expect(steady.getAttribute('aria-busy')).toBe('false');
    expect(plain.hasAttribute('aria-busy')).toBe(false);

    fixture.componentInstance.busy = true;
    fixture.changeDetectorRef.markForCheck();
    fixture.detectChanges();
    expect(steady.classList).toContain('btn-steady');
    expect(steady.getAttribute('aria-busy')).toBe('true');
    expect(steady.querySelector('.pi-spinner')).toBeTruthy();
    expect(steady.disabled).toBe(true);
  });

  it('pins the reserved slot (source): idle padding 1.1rem + half of (spinner 0.9rem + 7px gap) a side; busy 1.1rem, spinner 0.9rem wide', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/ui/btn/btn.scss'), 'utf8');
    const block = /\.btn-steady\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';
    expect(block).toMatch(/^\s*padding-inline:\s*calc\(1\.1rem \+ \(0\.9rem \+ 7px\) \/ 2\);/m);
    expect(block).toMatch(/&\[aria-busy='true'\]\s*\{\s*padding-inline:\s*1\.1rem;\s*\}/);
    expect(block).toMatch(/\.pi-spinner\s*\{[^}]*width:\s*0\.9rem;/);
    // The numbers it reserves for are the kit's: 1.1rem side padding, 7px gap, 0.9rem icons.
    expect(css).toMatch(/gap:\s*7px;/);
    expect(css).toMatch(/padding:\s*0\.6rem 1\.1rem;/);
    expect(css).toMatch(/i\s*\{\s*font-size:\s*0\.9rem;/);
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
    <app-btn variant="soft" [loading]="busy">全部入帳</app-btn>
    <app-btn icon="pi-check" [loading]="busy">確認匯入</app-btn>
    <app-btn icon="pi-refresh" [iconOnly]="true" [loading]="busy" ariaLabel="刷新"></app-btn>
    <app-btn variant="ghost">取消</app-btn>
  `,
})
class BtnSteadyHostComponent {
  busy = false;
}
