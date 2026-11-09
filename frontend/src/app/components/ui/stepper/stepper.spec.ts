import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { StepperComponent } from './stepper';

@Component({
  imports: [StepperComponent],
  template: `<app-stepper
    [value]="value()"
    [min]="min()"
    [max]="max()"
    [nullable]="nullable()"
    nullLabel="月底"
    suffix="號"
    ariaLabel="結帳日"
    (valueChange)="emitted.push($event)"
  ></app-stepper>`,
})
class StepperHostComponent {
  value = signal<number | null>(25);
  min = signal(1);
  max = signal(31);
  nullable = signal(false);
  emitted: (number | null)[] = [];
}

function setup(opts: { value?: number | null; nullable?: boolean } = {}) {
  const fixture = TestBed.createComponent(StepperHostComponent);
  const host = fixture.componentInstance;
  if (opts.value !== undefined) host.value.set(opts.value);
  if (opts.nullable) host.nullable.set(true);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  const q = <T extends HTMLElement>(sel: string) => el.querySelector(sel) as T;
  const openInput = () => {
    q<HTMLButtonElement>('.val').click();
    fixture.detectChanges();
    return q<HTMLInputElement>('.val-input');
  };
  const type = (input: HTMLInputElement, text: string) => {
    input.value = text;
    input.dispatchEvent(new Event('input'));
  };
  const key = (target: HTMLElement, k: string) =>
    target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
  return { fixture, host, q, openInput, type, key };
}

describe('StepperComponent', () => {
  it('renders value with suffix', () => {
    const { q } = setup();
    expect(q('.val').textContent?.trim()).toBe('25 號');
    expect(q('[role=group]').getAttribute('aria-label')).toBe('結帳日');
  });

  it('加一 emits 26 and 減一 emits 24', () => {
    const { host, q } = setup();
    q<HTMLButtonElement>('.inc').click();
    q<HTMLButtonElement>('.dec').click();
    expect(host.emitted).toEqual([26, 24]);
  });

  it('加一 is disabled at max 31', () => {
    const { q } = setup({ value: 31 });
    expect(q<HTMLButtonElement>('.inc').disabled).toBe(true);
  });

  it('nullable: 減一 at 1 emits null and renders the null label without the suffix', () => {
    const { fixture, host, q } = setup({ value: 1, nullable: true });
    q<HTMLButtonElement>('.dec').click();
    expect(host.emitted).toEqual([null]);
    host.value.set(null);
    fixture.detectChanges();
    expect(q('.val').textContent?.trim()).toBe('月底');
  });

  it('nullable: 加一 at null emits min', () => {
    const { host, q } = setup({ value: null, nullable: true });
    q<HTMLButtonElement>('.inc').click();
    expect(host.emitted).toEqual([1]);
  });

  it('non-nullable: 減一 disabled at min', () => {
    const { q } = setup({ value: 1 });
    expect(q<HTMLButtonElement>('.dec').disabled).toBe(true);
  });

  it('tapping the value opens an input; typing 25 + Enter emits 25', () => {
    const { fixture, host, q, openInput, type, key } = setup({ value: 10 });
    const input = openInput();
    expect(input.value).toBe('10');
    type(input, '25');
    key(input, 'Enter');
    fixture.detectChanges();
    expect(host.emitted).toEqual([25]);
    expect(q('.val-input')).toBeNull();
  });

  it('typing 27 then blur emits 27', () => {
    const { host, openInput, type } = setup();
    const input = openInput();
    type(input, '27');
    input.dispatchEvent(new Event('blur'));
    expect(host.emitted).toEqual([27]);
  });

  it('typing 40 with max 31 emits 31', () => {
    const { host, openInput, type, key } = setup();
    const input = openInput();
    type(input, '40');
    key(input, 'Enter');
    expect(host.emitted).toEqual([31]);
  });

  it('empty input on a nullable stepper emits null', () => {
    const { host, openInput, type, key } = setup({ nullable: true });
    const input = openInput();
    type(input, '');
    key(input, 'Enter');
    expect(host.emitted).toEqual([null]);
  });

  it('empty input on a non-nullable stepper does not emit', () => {
    const { host, openInput, type, key } = setup();
    const input = openInput();
    type(input, '');
    key(input, 'Enter');
    expect(host.emitted).toEqual([]);
  });

  it('Escape restores the old value without emitting', () => {
    const { fixture, host, q, openInput, type, key } = setup();
    const input = openInput();
    type(input, '30');
    key(input, 'Escape');
    fixture.detectChanges();
    expect(host.emitted).toEqual([]);
    expect(q('.val').textContent?.trim()).toBe('25 號');
  });

  it('ArrowUp on the group emits value+1', () => {
    const { host, q, key } = setup();
    key(q('[role=group]'), 'ArrowUp');
    key(q('[role=group]'), 'ArrowDown');
    expect(host.emitted).toEqual([26, 24]);
  });
});
