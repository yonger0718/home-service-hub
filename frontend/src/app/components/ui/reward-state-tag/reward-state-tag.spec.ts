import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { RewardAccrualState } from '../../../models/accounting.model';
import { REWARD_STATE_LABELS, RewardStateTagComponent } from './reward-state-tag';

@Component({
  imports: [RewardStateTagComponent],
  template: `<app-reward-state-tag [state]="state()" />`,
})
class HostComponent {
  readonly state = signal<RewardAccrualState>('estimated');
}

const PATHS: Record<RewardAccrualState, string> = {
  estimated: 'M12 7v5l3 2 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  posted: 'M5 12l4 4 10-10',
  bank_confirmed: 'M18 6 7 17l-5-5 M22 10l-7.5 7.5L13 16',
  forfeited: 'M18 6L6 18 M6 6l12 12',
};
const STATES = Object.keys(PATHS) as RewardAccrualState[];

function render(state: RewardAccrualState) {
  const fixture = TestBed.createComponent(HostComponent);
  fixture.componentInstance.state.set(state);
  fixture.detectChanges();
  return (fixture.nativeElement as HTMLElement).querySelector('.rst') as HTMLElement;
}

describe('RewardStateTagComponent', () => {
  it('renders the label and data-state for each state', () => {
    const labels = { estimated: '預估', posted: '已入帳', bank_confirmed: '銀行確認', forfeited: '未獲得' };
    for (const state of STATES) {
      const el = render(state);
      expect(el.textContent?.trim()).toBe(labels[state]);
      expect(REWARD_STATE_LABELS[state]).toBe(labels[state]);
      expect(el.getAttribute('data-state')).toBe(state);
    }
  });

  it('gives every state its own icon', () => {
    const seen = new Set<string>();
    for (const state of STATES) {
      const svg = render(state).querySelector('svg') as SVGElement;
      const d = svg.querySelector('path')?.getAttribute('d');
      expect(d).toBe(PATHS[state]);
      seen.add(d as string);
      expect(svg.getAttribute('aria-hidden')).toBe('true');
      expect(svg.getAttribute('fill')).toBe('none');
      expect(svg.getAttribute('stroke')).toBe('currentColor');
    }
    expect(seen.size).toBe(4);
  });

  it('is announced by its label only', () => {
    for (const state of STATES) {
      expect(render(state).textContent?.trim()).toBe(REWARD_STATE_LABELS[state]);
    }
  });
});
