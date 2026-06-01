import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FileChipComponent } from './file-chip';

@Component({
  imports: [FileChipComponent],
  template: `<app-file-chip filename="trades.csv" [parsedCount]="12" (remove)="removed = true"></app-file-chip>`,
})
class FileChipHostComponent {
  removed = false;
}

describe('FileChipComponent', () => {
  it('renders filename, parsed count, and emits remove', () => {
    const fixture = TestBed.createComponent(FileChipHostComponent);
    fixture.detectChanges();

    const chip = fixture.nativeElement.querySelector('.file-chip') as HTMLElement;
    expect(chip.textContent).toContain('trades.csv');
    expect(chip.textContent).toContain('12');

    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.getAttribute('aria-label')).toBe('移除檔案');
    button.click();
    expect(fixture.componentInstance.removed).toBe(true);
  });
});
