import { Component } from '@angular/core';
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
