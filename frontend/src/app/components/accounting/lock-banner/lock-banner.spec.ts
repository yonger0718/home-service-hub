import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { LockBannerComponent } from './lock-banner';

function render(locked: boolean): HTMLElement {
  TestBed.configureTestingModule({ imports: [LockBannerComponent] });
  const fixture = TestBed.createComponent(LockBannerComponent);
  fixture.componentRef.setInput('locked', locked);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('LockBannerComponent', () => {
  it('explains the cutover lock on imported rows', () => {
    const banner = render(true).querySelector('.lock-banner');

    expect(banner?.getAttribute('role')).toBe('status');
    expect(banner?.textContent).toContain('MOZE 匯入資料，切換後可編輯');
  });

  it('renders nothing for editable rows', () => {
    expect(render(false).querySelector('.lock-banner')).toBeNull();
  });
});
