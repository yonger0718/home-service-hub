import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';
import { describe, expect, it, beforeEach, vi } from 'vitest';

import { SettingsComponent } from './settings';
import { AppearanceService, GainLossConvention } from '../../services/appearance.service';

describe('SettingsComponent', () => {
  let appearance: {
    dark$: BehaviorSubject<boolean>;
    gainLoss$: BehaviorSubject<GainLossConvention>;
    setDark: ReturnType<typeof vi.fn>;
    setGainLoss: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    appearance = {
      dark$: new BehaviorSubject(false),
      gainLoss$: new BehaviorSubject<GainLossConvention>('asian'),
      setDark: vi.fn(),
      setGainLoss: vi.fn(),
    };

    await TestBed.configureTestingModule({
      imports: [SettingsComponent],
      providers: [{ provide: AppearanceService, useValue: appearance }],
    }).compileComponents();
  });

  it('calls AppearanceService when toggles change', () => {
    const fixture = TestBed.createComponent(SettingsComponent);
    fixture.detectChanges();

    const buttons = Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
    buttons.find(button => button.textContent?.trim() === '深色')!.click();
    buttons.find(button => button.textContent?.trim() === '綠漲紅跌')!.click();

    expect(appearance.setDark).toHaveBeenCalledWith(true);
    expect(appearance.setGainLoss).toHaveBeenCalledWith('western');
  });
});
