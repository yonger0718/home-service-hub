import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { RewardUnit } from '../../accounting/reward-rule-text';
import { CapBarComponent, CapBarWithCap, capBarView } from './cap-bar';

const capped = (...args: Parameters<typeof capBarView>): CapBarWithCap => capBarView(...args) as CapBarWithCap;

const MONEY: RewardUnit = { money: true };
const MILES: RewardUnit = { money: false, name: '測試里程' };

@Component({
  imports: [CapBarComponent],
  template: `<app-cap-bar [used]="used()" [estimated]="estimated()" [cap]="cap()" [unit]="unit" />`,
})
class HostComponent {
  readonly used = signal('236');
  readonly estimated = signal('64');
  readonly cap = signal<string | null>('500');
  readonly unit = MONEY;
}

// polish-reward-and-account-settings D2: posted + estimated against the cap, in the reward's unit.
describe('capBarView', () => {
  it('stays normal at 89 % and switches to near at 90 %', () => {
    expect(capBarView('178', '0', '200', MONEY)).toEqual({
      near: false, usedPct: 89, estPct: 0, text: '$178 / $200 · 剩 $22', aria: '本期已入帳 $178，預估 $0，上限 $200',
    });
    const at90 = capped('180', '0', '200', MONEY);
    expect([at90.near, at90.text]).toEqual([true, '$180 / $200 · 快到上限']);
  });

  it('spec scenario: $180 posted + $13 estimated on a $200 cap reads 快到上限', () => {
    const view = capped('180', '13', '200', MONEY);
    expect([view.near, view.text]).toEqual([true, '$193 / $200 · 快到上限']);
    expect(view.aria).toBe('本期已入帳 $180，預估 $13，上限 $200');
  });

  it('over the cap the two fills still sum to 100 %', () => {
    const view = capped('190', '30', '200', MONEY);
    expect(view.near).toBe(true);
    expect(view.usedPct + view.estPct).toBe(100);
    expect(view.text).toBe('$220 / $200 · 快到上限');
  });

  it('no cap: the one-line text and no bar', () => {
    const view = capBarView('236', '64', null, MONEY);
    expect(view).toEqual({ near: false, text: '無上限 · 本期 $236 已入帳 + $64 預估' });
  });

  it('points: the unit name once, after the cap', () => {
    const view = capped('300', '0', '500', MILES);
    expect(view.text).toBe('300 / 500 測試里程 · 剩 200');
    expect(view.aria).toBe('本期已入帳 300 測試里程，預估 0 測試里程，上限 500 測試里程');
    expect(capBarView('236', '64', null, MILES).text).toBe('無上限 · 本期 236 測試里程 已入帳 + 64 測試里程 預估');
  });

  it('the default aria name is D2\'s sentence', () => {
    expect(capped('236', '64', '500', MONEY).aria).toBe('本期已入帳 $236，預估 $64，上限 $500');
  });
});

describe('CapBarComponent', () => {
  function render() {
    const fixture = TestBed.createComponent(HostComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('renders the track with an accessible name and the two fills', () => {
    const el = render().nativeElement as HTMLElement;
    const bar = el.querySelector('.cap-bar') as HTMLElement;
    expect(bar.classList.contains('near')).toBe(false);
    const track = el.querySelector('[role="img"]') as HTMLElement;
    expect(track.getAttribute('aria-label')).toBe('本期已入帳 $236，預估 $64，上限 $500');
    expect((el.querySelector('.cap-used') as HTMLElement).style.width).toBe('47.2%');
    expect((el.querySelector('.cap-est') as HTMLElement).style.width).toBe('12.8%');
    expect(el.querySelector('.cap-text')?.textContent?.trim()).toBe('$300 / $500 · 剩 $200');
  });

  it('marks the host near at 90 %', () => {
    const fixture = render();
    fixture.componentInstance.used.set('450');
    fixture.componentInstance.estimated.set('0');
    fixture.detectChanges();
    expect((fixture.nativeElement.querySelector('.cap-bar') as HTMLElement).classList.contains('near')).toBe(true);
    expect(fixture.nativeElement.querySelector('.cap-text')?.textContent?.trim()).toBe('$450 / $500 · 快到上限');
  });

  it('without a cap there is no [role=img], only the line', () => {
    const fixture = render();
    fixture.componentInstance.cap.set(null);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="img"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('.cap-text')?.textContent?.trim()).toBe('無上限 · 本期 $236 已入帳 + $64 預估');
  });
});
